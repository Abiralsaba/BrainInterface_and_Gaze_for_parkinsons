#!/usr/bin/env python3
"""Merged EEG throttle and filtered eye-steering controller.

Default mode uses the calibrated EEG focus detector for forward/stop.  The
on-screen BLINK DRIVE button selects an optional mode where double blinks
toggle forward/stop instead.  Eye gaze controls left/right steering in both
modes.  Changing modes always stops the rover first.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import Deque, Optional, Sequence, Tuple

from camera_app import (
    DEFAULT_CALIBRATION as DEFAULT_EYE_CALIBRATION,
    DEFAULT_MODEL,
    FaceTracker,
    GuidedCalibration,
    draw_interface,
    ensure_model,
    load_calibration,
    save_calibration,
)
from camera_control import BlinkDetector, BlinkEvent, Calibration, ControlConfig, VisionSample
from car_transport import CarCommandSender
from thinkgear_eeg import (
    CsvRecorder,
    EEGState,
    FocusDetector,
    LivePlotProcess,
    ThinkGearPacketParser,
    _open_serial,
    decode_payload,
    load_focus_detector,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_EEG_CALIBRATION = ROOT / "eeg_calibration.json"
DEFAULT_BUTTON = (0, 0, 0, 0)


@dataclass(frozen=True)
class EEGSnapshot:
    version: int = 0
    detected_focus: Optional[bool] = None
    signal_quality: float = math.nan
    attention: float = math.nan
    meditation: float = math.nan
    last_packet_at: Optional[float] = None
    bytes_received: int = 0
    packets_ok: int = 0
    checksum_errors: int = 0
    error: Optional[str] = None


class EEGReader:
    """Read and classify ThinkGear packets without blocking camera frames."""

    def __init__(
        self,
        port: str,
        baud: int,
        detector: FocusDetector,
        csv_path: Optional[Path] = None,
        history_seconds: float = 30.0,
    ) -> None:
        self.port = port
        self.baud = baud
        self.detector = detector
        self.csv_path = csv_path
        self.state = EEGState(history_seconds)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._snapshot = EEGSnapshot()
        self._serial = None
        self._thread = threading.Thread(target=self._run, name="eeg-reader", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> EEGSnapshot:
        with self._lock:
            return self._snapshot

    def plot_snapshot(self) -> dict:
        """Return a process-safe copy of the current graph data."""
        with self._lock:
            source = self.state
            return {
                "started": source.started,
                "history_seconds": source.history_seconds,
                "times": list(source.times),
                "signal": list(source.signal),
                "attention": list(source.attention),
                "meditation": list(source.meditation),
                "detection": list(source.detection),
                "band_times": list(source.band_times),
                "band_values": {
                    name: list(values) for name, values in source.band_values.items()
                },
                "raw_samples": list(source.raw_samples),
                "latest_signal": source.latest_signal,
                "latest_attention": source.latest_attention,
                "latest_meditation": source.latest_meditation,
                "latest_detection": source.latest_detection,
                "latest_bands": dict(source.latest_bands),
            }

    def close(self) -> None:
        self._stop.set()
        serial_port = self._serial
        if serial_port is not None:
            try:
                serial_port.close()
            except OSError:
                pass
        self._thread.join(timeout=1.0)

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, error=message)

    def _run(self) -> None:
        parser = ThinkGearPacketParser()
        recorder = None
        packet_number = 0
        try:
            recorder = CsvRecorder(self.csv_path) if self.csv_path else None
            self._serial = _open_serial(self.port, self.baud)
            while not self._stop.is_set():
                chunk = self._serial.read(max(1, self._serial.in_waiting))
                now = time.monotonic()
                for packet in parser.feed(chunk):
                    packet_number += 1
                    update = decode_payload(packet.payload)
                    detected = self.detector.update(update)
                    with self._lock:
                        elapsed = self.state.apply(update, now, detected)
                        stats = parser.stats
                        self._snapshot = EEGSnapshot(
                            version=self._snapshot.version + 1,
                            detected_focus=detected,
                            signal_quality=self.state.latest_signal,
                            attention=self.state.latest_attention,
                            meditation=self.state.latest_meditation,
                            last_packet_at=now,
                            bytes_received=stats.bytes_received,
                            packets_ok=stats.packets_ok,
                            checksum_errors=stats.checksum_errors,
                        )
                    if recorder is not None:
                        recorder.write(elapsed, packet_number, update, detected)
        except Exception as exc:
            if not self._stop.is_set():
                self._set_error(str(exc))
        finally:
            if recorder is not None:
                recorder.close()
            if self._serial is not None:
                try:
                    self._serial.close()
                except OSError:
                    pass


class FilteredSteering:
    """Median + EMA + hysteresis + consecutive-frame steering filter."""

    def __init__(
        self,
        calibration: Calibration,
        median_window: int = 7,
        hold_frames: int = 5,
        ema_alpha: float = 0.35,
    ) -> None:
        if median_window < 1 or hold_frames < 1:
            raise ValueError("steering filter sizes must be positive")
        if not 0.0 < ema_alpha <= 1.0:
            raise ValueError("EMA alpha must be between 0 and 1")
        self.calibration = calibration
        self.samples: Deque[float] = deque(maxlen=median_window)
        self.hold_frames = hold_frames
        self.ema_alpha = ema_alpha
        self.filtered_score = 0.0
        self._ema: Optional[float] = None
        self._candidate = "CENTER"
        self._candidate_frames = 0
        self.committed = "CENTER"
        self._margin = max(
            0.004,
            (calibration.right_threshold - calibration.left_threshold) * 0.10,
        )

    def reset(self) -> None:
        self.samples.clear()
        self.filtered_score = 0.0
        self._ema = None
        self._candidate = "CENTER"
        self._candidate_frames = 0
        self.committed = "CENTER"

    def update(self, gaze_score: float) -> str:
        score = gaze_score * self.calibration.gaze_direction
        self.samples.append(score)
        smoothed = median(self.samples)
        self._ema = (
            smoothed
            if self._ema is None
            else self.ema_alpha * smoothed + (1.0 - self.ema_alpha) * self._ema
        )
        self.filtered_score = self._ema

        left = self.calibration.left_threshold
        right = self.calibration.right_threshold
        if self.committed == "LEFT" and self._ema <= left + self._margin:
            zone = "LEFT"
        elif self.committed == "RIGHT" and self._ema >= right - self._margin:
            zone = "RIGHT"
        elif self._ema <= left:
            zone = "LEFT"
        elif self._ema >= right:
            zone = "RIGHT"
        else:
            zone = "CENTER"

        if zone == self._candidate:
            self._candidate_frames += 1
        else:
            self._candidate = zone
            self._candidate_frames = 1
        if self._candidate_frames >= self.hold_frames:
            self.committed = zone
        return self.committed


class MergedDriveController:
    """Fuse EEG throttle (or optional blink throttle) with eye steering."""

    def __init__(
        self,
        calibration: Calibration,
        command_sender,
        focus_hold: float = 5.0,
        eeg_timeout: float = 1.5,
        keepalive_interval: float = 0.12,
        median_window: int = 7,
        hold_frames: int = 5,
        blink_median_window: int = 3,
        blink_control_enabled: bool = False,
        now: Optional[float] = None,
    ) -> None:
        self.calibration = calibration
        self.command_sender = command_sender
        self.focus_hold = focus_hold
        self.eeg_timeout = eeg_timeout
        self.keepalive_interval = keepalive_interval
        self.blink_control_enabled = blink_control_enabled
        self.blink_driving = False
        self.eeg_ready = False
        self.detected_focus: Optional[bool] = None
        self.focus_since: Optional[float] = None
        self.last_eeg_at: Optional[float] = None
        self.face_present = False
        self.face_lost_at: Optional[float] = None
        self.face_loss_timeout = 0.30
        self.throttle = "STOP"
        self.steering = "CENTER"
        self.raw_gaze = 0.0
        self.filtered_gaze = 0.0
        self.blink_score = 0.0
        self.reason = "WAITING FOR SIGNALS"
        self.steering_filter = FilteredSteering(
            calibration,
            median_window=median_window,
            hold_frames=hold_frames,
        )
        self.blink_detector = BlinkDetector(calibration.blink_threshold, ControlConfig())
        self._blink_samples: Deque[float] = deque(maxlen=blink_median_window)
        self.last_send_at = -math.inf
        started = time.monotonic() if now is None else now
        self._send("STOP", "CENTER", started)

    @property
    def drive_source(self) -> str:
        return "BLINK" if self.blink_control_enabled else "EEG"

    def _send(self, throttle: str, steering: str, now: float) -> None:
        self.command_sender.send(throttle, steering)
        self.last_send_at = now

    def _stop(self, reason: str, now: float, always_send: bool = False) -> None:
        changed = self.throttle != "STOP" or self.steering != "CENTER"
        self.throttle = "STOP"
        self.steering = "CENTER"
        self.reason = reason
        self.steering_filter.reset()
        if changed or always_send:
            self._send("STOP", "CENTER", now)

    def full_stop(self, reason: str, now: Optional[float] = None) -> None:
        stopped_at = time.monotonic() if now is None else now
        self.blink_driving = False
        self.eeg_ready = False
        self.focus_since = None
        self._stop(reason, stopped_at, always_send=True)

    def set_blink_control(self, enabled: bool, now: float) -> None:
        if enabled == self.blink_control_enabled:
            return
        self.blink_control_enabled = enabled
        self.blink_driving = False
        self.eeg_ready = False
        self.focus_since = None
        self.blink_detector.reset()
        self._blink_samples.clear()
        self._stop(f"{self.drive_source} CONTROL SELECTED", now, always_send=True)

    def toggle_blink_control(self, now: float) -> None:
        self.set_blink_control(not self.blink_control_enabled, now)

    def update_eeg(self, detected_focus: Optional[bool], packet_at: float) -> None:
        self.detected_focus = detected_focus
        self.last_eeg_at = packet_at
        if self.blink_control_enabled:
            return
        if detected_focus is not True:
            self.eeg_ready = False
            self.focus_since = None
            self._stop("EEG REST / NO SIGNAL", packet_at)
            return
        if self.focus_since is None:
            self.focus_since = packet_at
        if packet_at - self.focus_since >= self.focus_hold:
            self.eeg_ready = True

    def update_vision(self, sample: VisionSample, now: float) -> None:
        if not sample.face_present:
            self.face_present = False
            if self.face_lost_at is None:
                self.face_lost_at = now
            if now - self.face_lost_at >= self.face_loss_timeout:
                self.blink_driving = False
                self.eeg_ready = False
                self.focus_since = None
                self._stop("FACE LOST", now)
            self.blink_detector.reset()
            self._blink_samples.clear()
            return

        self.face_present = True
        self.face_lost_at = None
        self.raw_gaze = sample.gaze_score
        self._blink_samples.append(sample.blink_score)
        self.blink_score = median(self._blink_samples)
        blink_event = self.blink_detector.update(self.blink_score, now)
        eyes_closed = self.blink_score >= self.calibration.blink_threshold

        if blink_event == BlinkEvent.LONG:
            self.blink_driving = False
            self.eeg_ready = False
            self.focus_since = None
            self._stop("EMERGENCY STOP", now, always_send=True)
            return

        if blink_event == BlinkEvent.DOUBLE and self.blink_control_enabled:
            self.blink_driving = not self.blink_driving
            if not self.blink_driving:
                self._stop("STOPPED", now, always_send=True)

        if eyes_closed:
            return

        new_steering = self.steering_filter.update(sample.gaze_score)
        self.filtered_gaze = self.steering_filter.filtered_score
        if self.throttle == "FORWARD" and new_steering != self.steering:
            self.steering = new_steering
            self._send("FORWARD", self.steering, now)

    def tick(self, now: float) -> None:
        if not self.blink_control_enabled:
            if self.last_eeg_at is None or now - self.last_eeg_at > self.eeg_timeout:
                self.eeg_ready = False
                self.focus_since = None
                self._stop("EEG TIMEOUT", now)
            elif (
                self.detected_focus is True
                and self.focus_since is not None
                and now - self.focus_since >= self.focus_hold
            ):
                self.eeg_ready = True

        wanted_forward = (
            self.face_present
            and (self.blink_driving if self.blink_control_enabled else self.eeg_ready)
        )
        wanted_throttle = "FORWARD" if wanted_forward else "STOP"
        wanted_steering = self.steering_filter.committed if wanted_forward else "CENTER"
        if wanted_throttle != self.throttle or wanted_steering != self.steering:
            self.throttle = wanted_throttle
            self.steering = wanted_steering
            self.reason = "MOVING" if wanted_forward else "STOPPED"
            self._send(self.throttle, self.steering, now)
        elif now - self.last_send_at >= self.keepalive_interval:
            self._send(self.throttle, self.steering, now)


def _text(frame, text: str, pos, scale=0.6, color=(255, 255, 255), thickness=2):
    import cv2

    cv2.putText(
        frame,
        text,
        pos,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def draw_hud(frame, controller: MergedDriveController, eeg: EEGSnapshot) -> Tuple[int, int, int, int]:
    import cv2

    frame[:] = cv2.flip(frame, 1)
    height, width = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (width, 145), (15, 15, 15), -1)
    cv2.rectangle(frame, (0, height - 98), (width, height), (15, 15, 15), -1)

    face_text = "FACE OK" if controller.face_present else "FACE LOST"
    face_color = (80, 220, 80) if controller.face_present else (40, 40, 240)
    _text(frame, face_text, (20, 31), color=face_color)

    signal = "--" if math.isnan(eeg.signal_quality) else f"{eeg.signal_quality:.0f}"
    attention = "--" if math.isnan(eeg.attention) else f"{eeg.attention:.0f}"
    focus = "NO SIGNAL" if eeg.detected_focus is None else ("FOCUS" if eeg.detected_focus else "REST")
    _text(frame, f"EEG signal: {signal}   attention: {attention}   {focus}", (20, 64), scale=0.53)
    _text(
        frame,
        f"GAZE raw {controller.raw_gaze:+.3f}   filtered {controller.filtered_gaze:+.3f}",
        (20, 96),
        scale=0.50,
    )
    _text(frame, controller.reason, (20, 130), scale=0.62, color=(50, 220, 255))

    button_width = min(250, max(180, width // 4))
    button = (width - button_width - 20, 75, width - 20, 127)
    on = controller.blink_control_enabled
    fill = (40, 150, 55) if on else (65, 65, 65)
    cv2.rectangle(frame, button[:2], button[2:], fill, -1)
    cv2.rectangle(frame, button[:2], button[2:], (230, 230, 230), 2)
    _text(
        frame,
        f"BLINK DRIVE: {'ON' if on else 'OFF'}",
        (button[0] + 13, button[1] + 34),
        scale=0.57,
        thickness=2,
    )

    driving = controller.throttle == "FORWARD"
    _text(
        frame,
        f"THROTTLE: {controller.throttle}",
        (20, height - 60),
        scale=0.78,
        color=(80, 230, 80) if driving else (130, 130, 130),
    )
    _text(
        frame,
        f"STEERING: {controller.steering}    SOURCE: {controller.drive_source}",
        (20, height - 25),
        scale=0.65,
        color=(0, 190, 255) if controller.steering != "CENTER" else (170, 170, 170),
    )
    _text(frame, "Q quit | C calibrate | S stop | B source", (width - 360, height - 8), scale=0.37)

    cross_x = int(width / 2 + controller.filtered_gaze * width * 2)
    cross_x = max(20, min(width - 20, cross_x))
    cross_y = height // 2
    cv2.circle(frame, (cross_x, cross_y), 18, (0, 255, 255), 2)
    cv2.line(frame, (cross_x - 25, cross_y), (cross_x + 25, cross_y), (0, 255, 255), 1)

    if eeg.error:
        _text(frame, "EEG ERROR: " + eeg.error[:70], (20, 168), scale=0.48, color=(40, 40, 240))
    elif eeg.bytes_received == 0:
        _text(
            frame,
            "EEG WAITING: no serial data - check --port",
            (20, 168),
            scale=0.48,
            color=(0, 190, 255),
        )
    return button


def run(args: argparse.Namespace) -> int:
    import cv2

    ensure_model(args.model)
    eye_calibration = load_calibration(args.eye_calibration)
    eeg_path = args.eeg_calibration or args.candidate_calibration
    if eeg_path is None:
        raise RuntimeError("provide --eeg-calibration or --candidate-calibration")
    if args.candidate_calibration and not args.allow_candidate_car:
        raise RuntimeError(
            "the EEG candidate calibration is rejected; use an accepted profile or "
            "add --allow-candidate-car only for a wheels-raised test"
        )
    detector = load_focus_detector(
        eeg_path,
        allow_rejected=args.candidate_calibration is not None,
    )

    tracker = FaceTracker(args.model)
    camera = cv2.VideoCapture(args.camera)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    camera.set(cv2.CAP_PROP_FPS, 30)
    if not camera.isOpened():
        tracker.close()
        raise RuntimeError(f"cannot open camera {args.camera}")

    sender = CarCommandSender(args.car_url)
    sender.send("STOP", "CENTER")
    eeg_reader = EEGReader(
        args.port,
        args.baud,
        detector,
        args.csv,
        history_seconds=args.history,
    )
    eeg_reader.start()

    plot = None if args.no_plot else LivePlotProcess(
        history_seconds=args.history,
        show_detection=True,
        dark=True,
    )

    window = "Merged EEG + Eye Car Controller"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    ui = {"button": DEFAULT_BUTTON, "toggle": False}

    def on_mouse(event, x, y, _flags, _param):
        if event != cv2.EVENT_LBUTTONUP:
            return
        left, top, right, bottom = ui["button"]
        if left <= x <= right and top <= y <= bottom:
            ui["toggle"] = True

    cv2.setMouseCallback(window, on_mouse)
    controller: Optional[MergedDriveController] = None
    calibrator: Optional[GuidedCalibration] = None
    message = ""
    if eye_calibration is not None:
        controller = MergedDriveController(
            eye_calibration,
            sender,
            focus_hold=args.focus_hold,
            eeg_timeout=args.eeg_timeout,
            keepalive_interval=args.car_keepalive,
            median_window=args.median,
            hold_frames=args.hold,
            blink_median_window=args.blink_median,
            blink_control_enabled=args.start_with_blink_control,
        )
    else:
        calibrator = GuidedCalibration(time.monotonic())
        message = calibrator.instruction

    last_eeg_version = -1
    next_plot_at = 0.0
    plot_interval = 1.0 / args.plot_fps
    try:
        while True:
            ok, frame = camera.read()
            now = time.monotonic()
            if plot is not None and now >= next_plot_at:
                next_plot_at = now + plot_interval
                if not plot.publish(eeg_reader.plot_snapshot()):
                    plot.close()
                    plot = None
            if not ok or frame is None:
                if controller is not None:
                    controller.update_vision(VisionSample(now, False), now)
                    controller.tick(now)
                time.sleep(0.02)
                continue

            sample = tracker.detect(frame, now)
            eeg = eeg_reader.snapshot()
            if controller is not None and eeg.version != last_eeg_version:
                last_eeg_version = eeg.version
                if eeg.last_packet_at is not None:
                    controller.update_eeg(eeg.detected_focus, eeg.last_packet_at)

            if calibrator is not None:
                completed, message = calibrator.update(sample, now)
                if completed is not None:
                    save_calibration(args.eye_calibration, completed)
                    controller = MergedDriveController(
                        completed,
                        sender,
                        focus_hold=args.focus_hold,
                        eeg_timeout=args.eeg_timeout,
                        keepalive_interval=args.car_keepalive,
                        median_window=args.median,
                        hold_frames=args.hold,
                        blink_median_window=args.blink_median,
                        blink_control_enabled=args.start_with_blink_control,
                        now=now,
                    )
                    calibrator = None
                    last_eeg_version = -1
                draw_interface(frame, sample, None, message)
                cv2.imshow(window, frame)
            elif controller is not None:
                if ui["toggle"]:
                    ui["toggle"] = False
                    controller.toggle_blink_control(now)
                controller.update_vision(sample, now)
                controller.tick(now)
                ui["button"] = draw_hud(frame, controller, eeg)
                cv2.imshow(window, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                if controller is not None:
                    controller.full_stop("CALIBRATING", now)
                controller = None
                calibrator = GuidedCalibration(now)
                message = calibrator.instruction
                ui["button"] = DEFAULT_BUTTON
            elif key == ord("s") and controller is not None:
                controller.full_stop("MANUAL STOP", now)
            elif key == ord("b") and controller is not None:
                controller.toggle_blink_control(now)
    finally:
        if controller is not None:
            controller.full_stop("CLOSED")
        sender.close()
        eeg_reader.close()
        if plot is not None:
            plot.close()
        camera.release()
        tracker.close()
        cv2.destroyAllWindows()
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--eye-calibration", type=Path, default=DEFAULT_EYE_CALIBRATION)
    eeg_group = parser.add_mutually_exclusive_group()
    eeg_group.add_argument("--eeg-calibration", type=Path, default=None)
    eeg_group.add_argument("--candidate-calibration", type=Path, default=None)
    parser.add_argument("--allow-candidate-car", action="store_true")
    parser.add_argument("--enable-car", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--car-url", default="http://192.168.4.1")
    parser.add_argument("--focus-hold", type=float, default=5.0)
    parser.add_argument("--eeg-timeout", type=float, default=1.5)
    parser.add_argument("--car-keepalive", type=float, default=0.12)
    parser.add_argument("--median", type=int, default=7, help="eye median window (default: 7)")
    parser.add_argument("--hold", type=int, default=5, help="stable gaze frames (default: 5)")
    parser.add_argument("--blink-median", type=int, default=3)
    parser.add_argument("--start-with-blink-control", action="store_true")
    parser.add_argument("--history", type=float, default=30.0, help="seconds shown in the EEG graph")
    parser.add_argument(
        "--plot-fps",
        type=float,
        default=1.0,
        help="EEG graph refresh rate (default: 1; camera FPS is unaffected)",
    )
    parser.add_argument("--no-plot", action="store_true", help="do not open the live EEG graph")
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args(argv)
    if args.eeg_calibration is None and args.candidate_calibration is None:
        if DEFAULT_EEG_CALIBRATION.exists():
            args.eeg_calibration = DEFAULT_EEG_CALIBRATION
        else:
            parser.error("provide --eeg-calibration or --candidate-calibration")
    if (
        args.baud <= 0
        or args.focus_hold <= 0
        or args.eeg_timeout <= 0
        or args.car_keepalive <= 0
        or args.median <= 0
        or args.hold <= 0
        or args.blink_median <= 0
        or args.history <= 0
        or args.plot_fps <= 0
    ):
        parser.error("baud, timings, and filter sizes must be positive")
    return args


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
