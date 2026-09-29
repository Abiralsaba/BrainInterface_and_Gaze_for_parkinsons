#!/usr/bin/env python3
"""Read, trace, graph, and optionally record a NeuroSky ThinkGear stream.

The program only reads from the serial port.  It never writes commands to the
EEG module.  ThinkGear packet framing is AA AA LEN PAYLOAD CHECKSUM, where the
checksum is the low byte of the bitwise inverse of the payload sum.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

SYNC = 0xAA
MAX_PAYLOAD = 169
BAND_NAMES = (
    "delta",
    "theta",
    "low_alpha",
    "high_alpha",
    "low_beta",
    "high_beta",
    "low_gamma",
    "mid_gamma",
)


@dataclass(frozen=True)
class Packet:
    payload: bytes
    wire_bytes: bytes


@dataclass
class ParserStats:
    bytes_received: int = 0
    packets_ok: int = 0
    checksum_errors: int = 0
    invalid_lengths: int = 0
    noise_bytes: int = 0


class ThinkGearPacketParser:
    """Incremental and resynchronizing ThinkGear packet parser."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.stats = ParserStats()

    def feed(self, data: bytes) -> List[Packet]:
        self.stats.bytes_received += len(data)
        self._buffer.extend(data)
        packets: List[Packet] = []

        while True:
            sync_at = self._buffer.find(b"\xaa\xaa")
            if sync_at < 0:
                # Keep one trailing AA because it may be the first sync byte.
                keep = 1 if self._buffer.endswith(b"\xaa") else 0
                discarded = len(self._buffer) - keep
                self.stats.noise_bytes += discarded
                if discarded:
                    del self._buffer[:discarded]
                break
            if sync_at:
                self.stats.noise_bytes += sync_at
                del self._buffer[:sync_at]
            if len(self._buffer) < 3:
                break

            payload_length = self._buffer[2]
            if payload_length > MAX_PAYLOAD:
                self.stats.invalid_lengths += 1
                del self._buffer[0]
                continue

            packet_length = 3 + payload_length + 1
            if len(self._buffer) < packet_length:
                break

            wire_bytes = bytes(self._buffer[:packet_length])
            payload = wire_bytes[3:-1]
            expected = (~sum(payload)) & 0xFF
            if wire_bytes[-1] != expected:
                self.stats.checksum_errors += 1
                # Advance only one byte so AA AA inside the candidate can sync.
                del self._buffer[0]
                continue

            packets.append(Packet(payload=payload, wire_bytes=wire_bytes))
            self.stats.packets_ok += 1
            del self._buffer[:packet_length]

        return packets


@dataclass
class EEGUpdate:
    signal_quality: Optional[int] = None
    attention: Optional[int] = None
    meditation: Optional[int] = None
    blink_strength: Optional[int] = None
    raw_samples: List[int] = field(default_factory=list)
    bands: Dict[str, int] = field(default_factory=dict)
    unknown: List[Tuple[int, int, bytes]] = field(default_factory=list)
    malformed: List[str] = field(default_factory=list)


def decode_payload(payload: bytes) -> EEGUpdate:
    """Decode ThinkGear data rows from a verified packet payload."""
    update = EEGUpdate()
    index = 0

    while index < len(payload):
        excode_level = 0
        while index < len(payload) and payload[index] == 0x55:
            excode_level += 1
            index += 1
        if index >= len(payload):
            update.malformed.append("EXCODE without data code")
            break

        code = payload[index]
        index += 1
        if code < 0x80:
            value_length = 1
        else:
            if index >= len(payload):
                update.malformed.append(f"0x{code:02X} missing value length")
                break
            value_length = payload[index]
            index += 1

        end = index + value_length
        if end > len(payload):
            update.malformed.append(
                f"0x{code:02X} declares {value_length} bytes; "
                f"only {len(payload) - index} remain"
            )
            break
        value = bytes(payload[index:end])
        index = end

        if excode_level:
            update.unknown.append((excode_level, code, value))
        elif code == 0x02:
            update.signal_quality = value[0]
        elif code == 0x04:
            update.attention = value[0]
        elif code == 0x05:
            update.meditation = value[0]
        elif code == 0x16:
            update.blink_strength = value[0]
        elif code == 0x80 and value_length == 2:
            update.raw_samples.append(int.from_bytes(value, "big", signed=True))
        elif code == 0x83 and value_length == 24:
            update.bands.update(
                {
                    name: int.from_bytes(value[offset : offset + 3], "big")
                    for name, offset in zip(BAND_NAMES, range(0, 24, 3))
                }
            )
        else:
            update.unknown.append((0, code, value))

    return update


def build_packet(payload: bytes) -> bytes:
    """Build a framed packet for tests and the offline demo."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds ThinkGear's 169-byte limit")
    return bytes((SYNC, SYNC, len(payload))) + payload + bytes(((~sum(payload)) & 0xFF,))


def describe_update(update: EEGUpdate) -> str:
    fields: List[str] = []
    if update.signal_quality is not None:
        contact = "good" if update.signal_quality == 0 else "poor/no contact"
        fields.append(f"signal={update.signal_quality} ({contact})")
    if update.attention is not None:
        fields.append(f"attention={update.attention}")
    if update.meditation is not None:
        fields.append(f"meditation={update.meditation}")
    if update.blink_strength is not None:
        fields.append(f"blink={update.blink_strength}")
    if update.raw_samples:
        fields.append(f"raw={','.join(map(str, update.raw_samples))}")
    if update.bands:
        fields.append("bands=" + ",".join(f"{k}:{v}" for k, v in update.bands.items()))
    if update.unknown:
        fields.append(
            "unknown="
            + ",".join(
                f"ext{level}:0x{code:02X}[{value.hex(' ')}]"
                for level, code, value in update.unknown
            )
        )
    if update.malformed:
        fields.append("MALFORMED=" + "; ".join(update.malformed))
    return " | ".join(fields) if fields else "empty payload"


class EEGState:
    def __init__(self, history_seconds: float = 30.0) -> None:
        self.started = time.monotonic()
        self.history_seconds = history_seconds
        self.times: Deque[float] = deque()
        self.signal: Deque[float] = deque()
        self.attention: Deque[float] = deque()
        self.meditation: Deque[float] = deque()
        self.detection: Deque[float] = deque()
        self.band_times: Deque[float] = deque()
        self.band_values: Dict[str, Deque[float]] = {name: deque() for name in BAND_NAMES}
        self.raw_samples: Deque[int] = deque(maxlen=512)
        self.latest_signal = math.nan
        self.latest_attention = math.nan
        self.latest_meditation = math.nan
        self.latest_detection: Optional[bool] = None
        self.latest_bands: Dict[str, int] = {}

    def apply(
        self,
        update: EEGUpdate,
        now: Optional[float] = None,
        detection: Optional[bool] = None,
    ) -> float:
        elapsed = (time.monotonic() if now is None else now) - self.started
        changed = False
        if update.signal_quality is not None:
            self.latest_signal = update.signal_quality
            changed = True
        if update.attention is not None:
            self.latest_attention = update.attention
            changed = True
        if update.meditation is not None:
            self.latest_meditation = update.meditation
            changed = True
        if changed:
            self.times.append(elapsed)
            self.signal.append(self.latest_signal)
            self.attention.append(self.latest_attention)
            self.meditation.append(self.latest_meditation)
            self.latest_detection = detection
            self.detection.append(
                math.nan if detection is None else (100.0 if detection else 0.0)
            )
        if update.bands:
            self.latest_bands.update(update.bands)
            self.band_times.append(elapsed)
            for name in BAND_NAMES:
                self.band_values[name].append(float(self.latest_bands.get(name, math.nan)))
        self.raw_samples.extend(update.raw_samples)
        self._trim(elapsed)
        return elapsed

    def _trim(self, elapsed: float) -> None:
        cutoff = elapsed - self.history_seconds
        while self.times and self.times[0] < cutoff:
            self.times.popleft()
            self.signal.popleft()
            self.attention.popleft()
            self.meditation.popleft()
            self.detection.popleft()
        while self.band_times and self.band_times[0] < cutoff:
            self.band_times.popleft()
            for values in self.band_values.values():
                values.popleft()


class CsvRecorder:
    FIELDNAMES = (
        "host_time_iso",
        "elapsed_s",
        "packet_number",
        "signal_quality",
        "attention",
        "meditation",
        "blink_strength",
        "raw_samples",
        "focus_detected",
        *BAND_NAMES,
    )

    def __init__(self, path: Path) -> None:
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()

    def write(
        self,
        elapsed: float,
        packet_number: int,
        update: EEGUpdate,
        detection: Optional[bool] = None,
    ) -> None:
        row = {
            "host_time_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "elapsed_s": f"{elapsed:.6f}",
            "packet_number": packet_number,
            "signal_quality": update.signal_quality,
            "attention": update.attention,
            "meditation": update.meditation,
            "blink_strength": update.blink_strength,
            "raw_samples": ";".join(map(str, update.raw_samples)),
            "focus_detected": "" if detection is None else int(detection),
        }
        row.update(update.bands)
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class LivePlot:
    def __init__(self, state: EEGState, show_detection: bool = False) -> None:
        import matplotlib.pyplot as plt

        self.plt = plt
        self.state = state
        self.show_detection = show_detection
        self.figure = plt.figure(figsize=(12, 9), constrained_layout=True)
        grid = self.figure.add_gridspec(
            4,
            4,
            height_ratios=(1.25, 0.85, 0.85, 1.0),
        )
        self.metrics_axis = self.figure.add_subplot(grid[0, :])
        self.band_axes = {
            name: self.figure.add_subplot(grid[1 + index // 4, index % 4])
            for index, name in enumerate(BAND_NAMES)
        }
        self.raw_axis = self.figure.add_subplot(grid[3, :])
        self.metric_lines = {
            "attention": self.metrics_axis.plot([], [], label="attention")[0],
            "meditation": self.metrics_axis.plot([], [], label="meditation")[0],
            "signal": self.metrics_axis.plot([], [], label="poor signal", alpha=0.7)[0],
        }
        if show_detection:
            self.metric_lines["detection"] = self.metrics_axis.plot(
                [],
                [],
                label="FOCUS DETECTED (0/100)",
                color="magenta",
                linewidth=2,
                drawstyle="steps-post",
            )[0]
        self.band_lines = {
            name: self.band_axes[name].plot([], [])[0]
            for name in BAND_NAMES
        }
        (self.raw_line,) = self.raw_axis.plot([], [], color="black", linewidth=0.8)

        self.metrics_axis.set_ylabel("value")
        self.metrics_axis.set_ylim(-2, 202)
        self.metrics_axis.legend(ncol=2 if show_detection else 3, loc="upper left")
        self.metrics_axis.grid(alpha=0.25)
        for index, name in enumerate(BAND_NAMES):
            axis = self.band_axes[name]
            axis.set_title(name.replace("_", " "))
            axis.set_yscale("log")
            axis.grid(alpha=0.25)
            axis.tick_params(labelsize=8)
            if index % 4 == 0:
                axis.set_ylabel("band power")
        self.raw_axis.set_ylabel("raw")
        self.raw_axis.set_xlabel("latest raw samples")
        self.raw_axis.grid(alpha=0.25)
        self.figure.canvas.manager.set_window_title("ThinkGear EEG monitor")
        plt.ion()
        plt.show(block=False)

    def update(self) -> bool:
        if not self.plt.fignum_exists(self.figure.number):
            return False
        times = list(self.state.times)
        self.metric_lines["attention"].set_data(times, list(self.state.attention))
        self.metric_lines["meditation"].set_data(times, list(self.state.meditation))
        self.metric_lines["signal"].set_data(times, list(self.state.signal))
        if self.show_detection:
            self.metric_lines["detection"].set_data(times, list(self.state.detection))
            if self.state.latest_detection is None:
                title = "DETECTED: NO USABLE SIGNAL"
                color = "tab:red"
            elif self.state.latest_detection:
                title = "DETECTED: FOCUS"
                color = "magenta"
            else:
                title = "DETECTED: REST / NO FOCUS"
                color = "tab:gray"
            self.metrics_axis.set_title(title, color=color, fontweight="bold")
        band_times = list(self.state.band_times)
        for name, line in self.band_lines.items():
            line.set_data(band_times, list(self.state.band_values[name]))
            if band_times:
                axis = self.band_axes[name]
                axis.relim()
                axis.autoscale_view(scalex=False, scaley=True)
        raw = list(self.state.raw_samples)
        self.raw_line.set_data(range(len(raw)), raw)

        elapsed = time.monotonic() - self.state.started
        left = max(0.0, elapsed - self.state.history_seconds)
        right = max(self.state.history_seconds, elapsed)
        self.metrics_axis.set_xlim(left, right)
        for axis in self.band_axes.values():
            axis.set_xlim(left, right)
        if raw:
            self.raw_axis.set_xlim(0, max(16, len(raw) - 1))
            self.raw_axis.relim()
            self.raw_axis.autoscale_view(scalex=False, scaley=True)
        self.figure.canvas.draw_idle()
        self.figure.canvas.flush_events()
        return True


class FocusDetector:
    """Smoothed visualization-only classifier loaded from calibration output."""

    def __init__(self, threshold: float, max_signal_quality: int, window: int = 3) -> None:
        if window < 1:
            raise ValueError("focus detection window must be positive")
        self.threshold = threshold
        self.max_signal_quality = max_signal_quality
        self.window = window
        self._votes: Deque[bool] = deque(maxlen=window)
        self.latest: Optional[bool] = None

    def update(self, update: EEGUpdate) -> Optional[bool]:
        if update.signal_quality is None or update.attention is None:
            return self.latest
        if update.signal_quality > self.max_signal_quality:
            self._votes.clear()
            self.latest = None
            return None
        self._votes.append(update.attention >= self.threshold)
        if len(self._votes) < self.window:
            self.latest = False
        else:
            votes_needed = self.window // 2 + 1
            self.latest = sum(self._votes) >= votes_needed
        return self.latest


def load_focus_detector(path: Path, allow_rejected: bool) -> FocusDetector:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load calibration profile {path}: {exc}") from exc
    if not profile.get("valid") and not allow_rejected:
        raise RuntimeError(
            f"calibration profile {path} is rejected; collect a valid calibration "
            "or use --candidate-calibration for visualization only"
        )
    threshold = profile.get("attention_threshold")
    if threshold is None:
        raise RuntimeError(f"calibration profile {path} has no attention threshold")
    max_quality = int(profile.get("max_accepted_signal_quality", 0))
    return FocusDetector(float(threshold), max_quality)


class FocusDriveController:
    """Fail-safe focus hold gate around the rover command transport."""

    def __init__(
        self,
        command_sender,
        hold_seconds: float = 5.0,
        packet_timeout: float = 1.5,
        keepalive_interval: float = 0.12,
        now: Optional[float] = None,
    ) -> None:
        self.command_sender = command_sender
        self.hold_seconds = hold_seconds
        self.packet_timeout = packet_timeout
        self.keepalive_interval = keepalive_interval
        self.throttle = "STOP"
        self.focus_since: Optional[float] = None
        self.last_detection_at: Optional[float] = None
        self.last_send_at = -math.inf
        self._send("STOP", time.monotonic() if now is None else now)

    def _send(self, throttle: str, now: float) -> None:
        self.command_sender.send(throttle, "CENTER")
        self.last_send_at = now

    def stop(self, now: float) -> None:
        self.focus_since = None
        if self.throttle != "STOP":
            self.throttle = "STOP"
            self._send("STOP", now)

    def update(self, detected_focus: Optional[bool], now: float) -> None:
        self.last_detection_at = now
        if detected_focus is not True:
            self.stop(now)
            return
        if self.focus_since is None:
            self.focus_since = now
        if self.throttle == "STOP" and now - self.focus_since >= self.hold_seconds:
            self.throttle = "FORWARD"
            self._send("FORWARD", now)

    def tick(self, now: float) -> None:
        if (
            self.last_detection_at is None
            or now - self.last_detection_at > self.packet_timeout
        ):
            self.stop(now)
        if now - self.last_send_at >= self.keepalive_interval:
            self._send(self.throttle, now)

    def status(self, now: float) -> str:
        if self.throttle == "FORWARD":
            return "FORWARD"
        if self.focus_since is None:
            return "STOP — waiting for focus"
        held = min(self.hold_seconds, max(0.0, now - self.focus_since))
        return f"STOP — focus hold {held:.1f}/{self.hold_seconds:.1f}s"

    def close(self, now: Optional[float] = None) -> None:
        stopped_at = time.monotonic() if now is None else now
        self.stop(stopped_at)
        self._send("STOP", stopped_at)
        self.command_sender.close()


def _demo_chunks() -> Iterable[bytes]:
    phase = 0.0
    while True:
        attention = round(50 + 35 * math.sin(phase))
        meditation = round(50 + 25 * math.cos(phase * 0.7))
        signal = 0 if int(phase) % 12 else 25
        powers = [max(1, int((index + 1) * 9000 * (1.3 + math.sin(phase + index)))) for index in range(8)]
        band_bytes = b"".join(power.to_bytes(3, "big") for power in powers)
        payload = bytes((0x02, signal, 0x04, attention, 0x05, meditation, 0x83, 24)) + band_bytes
        yield build_packet(payload)
        phase += 0.2
        time.sleep(0.1)


def _open_serial(port: str, baud: int):
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial is missing; run: python -m pip install -r requirements.txt") from exc
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )


def _serial_chunks(serial_port) -> Iterable[bytes]:
    """Yield forever; a serial timeout is an idle sample, not end-of-file."""
    while True:
        yield serial_port.read(max(1, serial_port.in_waiting))


def run(args: argparse.Namespace) -> int:
    calibration_path = args.calibration or args.candidate_calibration
    if args.enable_car and calibration_path is None:
        raise RuntimeError("--enable-car requires --calibration or --candidate-calibration")
    if args.enable_car and args.demo:
        raise RuntimeError("--enable-car cannot be used with synthetic --demo data")
    if args.enable_car and args.candidate_calibration and not args.allow_candidate_car:
        raise RuntimeError(
            "the selected profile is rejected; use an accepted --calibration profile, "
            "or add --allow-candidate-car only for a wheels-raised test"
        )
    classifier = None
    if calibration_path:
        classifier = load_focus_detector(
            calibration_path, allow_rejected=args.candidate_calibration is not None
        )
    parser = ThinkGearPacketParser()
    state = EEGState(args.history)
    recorder = CsvRecorder(args.csv) if args.csv else None
    plot = None if args.no_plot else LivePlot(state, show_detection=classifier is not None)
    serial_port = None
    car_controller = None
    packet_number = 0
    last_data = state.started
    last_status = 0.0

    if args.demo:
        chunks = _demo_chunks()
        source_name = "synthetic demo"
    else:
        serial_port = _open_serial(args.port, args.baud)
        chunks = _serial_chunks(serial_port)
        source_name = f"{args.port} at {args.baud} 8N1"

    if args.enable_car:
        from car_transport import CarCommandSender

        car_controller = FocusDriveController(
            CarCommandSender(args.car_url),
            hold_seconds=args.focus_hold,
            packet_timeout=args.eeg_timeout,
            keepalive_interval=args.car_keepalive,
        )

    # Do not count slow GUI/font initialization or serial-port opening against
    # a requested capture duration.
    state.started = time.monotonic()
    last_data = state.started
    deadline = state.started + args.duration if args.duration else None
    print(f"Reading {source_name}; this process will not transmit serial data.")
    if classifier is not None:
        mode = "REJECTED CANDIDATE / VISUALIZATION ONLY" if args.candidate_calibration else "accepted"
        print(
            f"Loaded {mode} calibration: threshold={classifier.threshold:.1f}, "
            f"max_signal={classifier.max_signal_quality}, vote_window={classifier.window}."
        )
    if car_controller is not None:
        print(
            f"CAR CONTROL ENABLED: {args.focus_hold:.1f}s focus hold, "
            f"{args.eeg_timeout:.1f}s EEG timeout, straight forward only."
        )
    if not args.demo:
        print(f"If no bytes appear within {args.idle_warning:.0f}s, check TX->RX, shared GND, and module power.")

    try:
        for chunk in chunks:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            if chunk:
                last_data = now
            for packet in parser.feed(chunk):
                packet_number += 1
                update = decode_payload(packet.payload)
                detection = classifier.update(update) if classifier is not None else None
                elapsed = state.apply(update, now, detection)
                if car_controller is not None:
                    car_controller.update(detection, now)
                if args.trace:
                    print(f"[{elapsed:9.3f}] packet {packet_number:6d}  {packet.wire_bytes.hex(' ')}")
                    print(f"{'':12}{describe_update(update)}")
                    if classifier is not None:
                        label = "NO SIGNAL" if detection is None else ("FOCUS" if detection else "REST")
                        print(f"{'':12}detected={label}")
                if recorder:
                    recorder.write(elapsed, packet_number, update, detection)

            if car_controller is not None:
                car_controller.tick(now)

            if now - last_status >= 1.0:
                stats = parser.stats
                status = (
                    f"\rbytes={stats.bytes_received} packets={stats.packets_ok} "
                    f"checksum_errors={stats.checksum_errors} "
                    f"signal={state.latest_signal:g} attention={state.latest_attention:g} "
                    f"meditation={state.latest_meditation:g}"
                )
                if classifier is not None:
                    detected = state.latest_detection
                    label = "NO_SIGNAL" if detected is None else ("FOCUS" if detected else "REST")
                    status += f" detected={label}"
                if car_controller is not None:
                    status += f" car={car_controller.throttle}"
                print(status.ljust(120), end="", flush=True)
                last_status = now
                if not args.demo and now - last_data >= args.idle_warning:
                    print(
                        f"\nNo serial bytes received for {now - last_data:.1f}s. "
                        "The port is open, but RX is idle."
                    )
                    last_data = now
            if plot is not None:
                if car_controller is not None:
                    plot.figure.suptitle(
                        f"EEG CAR CONTROL — {car_controller.status(now)}",
                        color="tab:green" if car_controller.throttle == "FORWARD" else "tab:red",
                        fontweight="bold",
                    )
                if not plot.update():
                    break
            if plot is None and not chunk:
                time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        if car_controller is not None:
            car_controller.close()
        if serial_port is not None:
            serial_port.close()
        if recorder is not None:
            recorder.close()
        print()
        stats = parser.stats
        print(
            f"Stopped: {stats.bytes_received} bytes, {stats.packets_ok} valid packets, "
            f"{stats.checksum_errors} checksum errors, {stats.invalid_lengths} invalid lengths, "
            f"{stats.noise_bytes} non-packet bytes."
        )
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/cu.usbmodem5AE71016131", help="serial device")
    parser.add_argument("--baud", type=int, default=9600, help="serial speed (default: 9600)")
    parser.add_argument("--trace", action="store_true", help="print every framed packet and decoded row")
    parser.add_argument("--csv", type=Path, help="write decoded packet rows to this CSV file")
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    parser.add_argument("--history", type=float, default=30.0, help="seconds shown in live plots")
    parser.add_argument("--idle-warning", type=float, default=5.0, help="seconds before an RX-idle warning")
    parser.add_argument("--no-plot", action="store_true", help="terminal/CSV only")
    parser.add_argument("--demo", action="store_true", help="use synthetic packets instead of serial hardware")
    calibration_group = parser.add_mutually_exclusive_group()
    calibration_group.add_argument(
        "--calibration",
        type=Path,
        help="accepted calibration JSON used for live focus detection",
    )
    calibration_group.add_argument(
        "--candidate-calibration",
        type=Path,
        help="use a rejected candidate for visualization; motor use needs an extra explicit override",
    )
    parser.add_argument(
        "--enable-car",
        action="store_true",
        help="send fail-safe STOP/FORWARD commands to the ESP32 rover",
    )
    parser.add_argument(
        "--allow-candidate-car",
        action="store_true",
        help="allow rejected calibration for a wheels-raised car test only",
    )
    parser.add_argument("--car-url", default="http://192.168.4.1", help="ESP32 rover base URL")
    parser.add_argument(
        "--focus-hold",
        type=float,
        default=5.0,
        help="continuous detected-focus seconds required before FORWARD (default: 5)",
    )
    parser.add_argument(
        "--eeg-timeout",
        type=float,
        default=1.5,
        help="seconds without an EEG decision before STOP",
    )
    parser.add_argument(
        "--car-keepalive",
        type=float,
        default=0.12,
        help="seconds between rover watchdog heartbeats",
    )
    args = parser.parse_args(argv)
    if (
        args.baud <= 0
        or args.history <= 0
        or (args.duration is not None and args.duration <= 0)
        or args.focus_hold <= 0
        or args.eeg_timeout <= 0
        or args.car_keepalive <= 0
    ):
        parser.error("baud, history, duration, and safety timings must be positive")
    if args.allow_candidate_car and not args.enable_car:
        parser.error("--allow-candidate-car is only meaningful with --enable-car")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
