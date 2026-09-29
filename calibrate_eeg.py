#!/usr/bin/env python3
"""Guided two-phase ThinkGear contact check and preliminary calibration."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from thinkgear_eeg import (
    BAND_NAMES,
    EEGState,
    LivePlot,
    ThinkGearPacketParser,
    _open_serial,
    decode_payload,
)


@dataclass
class Sample:
    phase: str
    elapsed: float
    quality: Optional[int]
    attention: Optional[int]
    meditation: Optional[int]
    bands: Dict[str, int]

def update_live_plot(live_plot, label: str, remaining: int) -> None:
    if live_plot is None:
        return
    live_plot.figure.suptitle(f"CALIBRATION — {label.upper()} — {remaining}s remaining")
    live_plot.update()


def collect_phase(
    serial_port,
    parser,
    phase: str,
    seconds: float,
    started: float,
    writer,
    live_state=None,
    live_plot=None,
) -> List[Sample]:
    samples: List[Sample] = []
    phase_started = time.monotonic()
    last_second = None
    while True:
        now = time.monotonic()
        elapsed_phase = now - phase_started
        if elapsed_phase >= seconds:
            break
        remaining = max(0, math.ceil(seconds - elapsed_phase))
        if remaining != last_second:
            print(f"\r{phase.upper():7s}: {remaining:2d} seconds remaining", end="", flush=True)
            last_second = remaining

        chunk = serial_port.read(max(1, serial_port.in_waiting))
        for packet in parser.feed(chunk):
            update = decode_payload(packet.payload)
            if live_state is not None:
                live_state.apply(update)
            sample = Sample(
                phase=phase,
                elapsed=time.monotonic() - started,
                quality=update.signal_quality,
                attention=update.attention,
                meditation=update.meditation,
                bands=dict(update.bands),
            )
            samples.append(sample)
            row = {
                "phase": phase,
                "elapsed_s": f"{sample.elapsed:.6f}",
                "signal_quality": sample.quality,
                "attention": sample.attention,
                "meditation": sample.meditation,
            }
            row.update(sample.bands)
            writer.writerow(row)
        update_live_plot(live_plot, phase, remaining)
    print()
    return samples


def warm_up(serial_port, parser, seconds: float, live_state=None, live_plot=None) -> None:
    """Read and discard startup packets while eSense settles."""
    started = time.monotonic()
    last_second = None
    latest_quality = None
    latest_attention = None
    while True:
        elapsed = time.monotonic() - started
        if elapsed >= seconds:
            break
        chunk = serial_port.read(max(1, serial_port.in_waiting))
        for packet in parser.feed(chunk):
            update = decode_payload(packet.payload)
            if live_state is not None:
                live_state.apply(update)
            if update.signal_quality is not None:
                latest_quality = update.signal_quality
            if update.attention is not None:
                latest_attention = update.attention
        remaining = max(0, math.ceil(seconds - elapsed))
        if remaining != last_second:
            quality = "n/a" if latest_quality is None else str(latest_quality)
            attention = "n/a" if latest_attention is None else str(latest_attention)
            print(
                f"\rWARM-UP: {remaining:2d}s remaining  signal={quality:>3s} "
                f"attention={attention:>3s}",
                end="",
                flush=True,
            )
            last_second = remaining
        update_live_plot(live_plot, "warm-up", remaining)
    print()


def phase_summary(samples: List[Sample], max_signal_quality: int) -> Dict[str, object]:
    quality_samples = [sample for sample in samples if sample.quality is not None]
    usable = [
        sample
        for sample in samples
        if sample.quality is not None
        and sample.quality <= max_signal_quality
        and sample.attention is not None
    ]
    perfect_contact = [sample for sample in quality_samples if sample.quality == 0]
    attention = [sample.attention for sample in usable if sample.attention is not None]
    meditation = [sample.meditation for sample in usable if sample.meditation is not None]
    return {
        "packet_count": len(samples),
        "usable_count": len(usable),
        "good_contact_fraction": (
            len(usable) / len(quality_samples) if quality_samples else 0.0
        ),
        "perfect_contact_fraction": (
            len(perfect_contact) / len(quality_samples) if quality_samples else 0.0
        ),
        "attention_mean": statistics.fmean(attention) if attention else None,
        "attention_median": statistics.median(attention) if attention else None,
        "attention_min": min(attention) if attention else None,
        "attention_max": max(attention) if attention else None,
        "meditation_mean": statistics.fmean(meditation) if meditation else None,
        "attention_values": attention,
    }


def threshold_accuracy(focus: List[int], rest: List[int], threshold: float) -> float:
    correct = sum(value >= threshold for value in focus)
    correct += sum(value < threshold for value in rest)
    return correct / (len(focus) + len(rest))


def analyze(
    focus_samples: List[Sample], rest_samples: List[Sample], max_signal_quality: int = 25
) -> Dict[str, object]:
    focus = phase_summary(focus_samples, max_signal_quality)
    rest = phase_summary(rest_samples, max_signal_quality)
    reasons: List[str] = []

    for name, summary in (("focus", focus), ("rest", rest)):
        if summary["usable_count"] < 5:
            reasons.append(f"{name} had fewer than 5 usable packets")
        if summary["good_contact_fraction"] < 0.8:
            reasons.append(
                f"{name} contact was good for only "
                f"{100 * summary['good_contact_fraction']:.0f}% of packets"
            )

    threshold = None
    accuracy = None
    difference = None
    if focus["attention_mean"] is not None and rest["attention_mean"] is not None:
        difference = focus["attention_mean"] - rest["attention_mean"]
        threshold = (focus["attention_mean"] + rest["attention_mean"]) / 2
        accuracy = threshold_accuracy(
            focus["attention_values"], rest["attention_values"], threshold
        )
        if difference < 8:
            reasons.append(f"attention separation was only {difference:.1f} points (need at least 8)")
        if accuracy < 0.7:
            reasons.append(f"trial classification accuracy was only {100 * accuracy:.0f}%")

    return {
        "valid": not reasons,
        "focus": focus,
        "rest": rest,
        "attention_difference": difference,
        "attention_threshold": threshold,
        "trial_accuracy": accuracy,
        "rejection_reasons": reasons,
        "max_accepted_signal_quality": max_signal_quality,
        "note": "A valid result is preliminary; repeat randomized focus/rest trials before motor control.",
    }


def print_report(report: Dict[str, object]) -> None:
    print("\nCalibration report")
    for name in ("focus", "rest"):
        summary = report[name]
        mean = summary["attention_mean"]
        mean_text = "n/a" if mean is None else f"{mean:.1f}"
        print(
            f"  {name:5s}: {summary['usable_count']}/{summary['packet_count']} usable, "
            f"contact {100 * summary['good_contact_fraction']:.0f}%, "
            f"perfect {100 * summary['perfect_contact_fraction']:.0f}%, "
            f"attention mean {mean_text}"
        )
    if report["attention_difference"] is not None:
        print(f"  focus-rest difference: {report['attention_difference']:.1f}")
        print(f"  candidate threshold:  {report['attention_threshold']:.1f}")
        print(f"  trial accuracy:       {100 * report['trial_accuracy']:.0f}%")
    if report["valid"]:
        print("RESULT: preliminary calibration accepted.")
    else:
        print("RESULT: calibration rejected; do not use this threshold.")
        for reason in report["rejection_reasons"]:
            print(f"  - {reason}")


def plot_calibration(
    focus_samples: List[Sample],
    rest_samples: List[Sample],
    report: Dict[str, object],
    path: Path,
    show: bool = False,
) -> None:
    import matplotlib.pyplot as plt

    samples = focus_samples + rest_samples
    if not samples:
        raise RuntimeError("calibration CSV contains no samples to graph")
    boundary = rest_samples[0].elapsed if rest_samples else focus_samples[-1].elapsed
    end = samples[-1].elapsed
    figure, axes = plt.subplots(3, 1, figsize=(12, 9), constrained_layout=True)
    metric_axis, band_axis, distribution_axis = axes

    times = [sample.elapsed for sample in samples]
    attention = [math.nan if sample.attention is None else sample.attention for sample in samples]
    meditation = [math.nan if sample.meditation is None else sample.meditation for sample in samples]
    quality = [math.nan if sample.quality is None else sample.quality for sample in samples]

    metric_axis.plot(times, attention, marker="o", label="attention")
    metric_axis.plot(times, meditation, marker="o", label="meditation")
    threshold = report.get("attention_threshold")
    if threshold is not None:
        metric_axis.axhline(threshold, color="black", linestyle="--", alpha=0.6, label="candidate threshold")
    metric_axis.set_ylim(-2, 102)
    metric_axis.set_ylabel("eSense value")
    metric_axis.grid(alpha=0.25)
    metric_axis.legend(loc="upper left", ncol=3)

    quality_axis = metric_axis.twinx()
    quality_axis.plot(times, quality, color="tab:green", marker=".", alpha=0.55, label="poor signal")
    quality_axis.set_ylim(-5, 205)
    quality_axis.set_ylabel("poor signal (0 is best)", color="tab:green")

    for name in BAND_NAMES:
        values = [sample.bands.get(name, math.nan) for sample in samples]
        band_axis.plot(times, values, marker=".", linewidth=1, label=name.replace("_", " "))
    band_axis.set_yscale("log")
    band_axis.set_ylabel("EEG band power (log)")
    band_axis.grid(alpha=0.25)
    band_axis.legend(loc="upper left", ncol=4, fontsize=8)

    focus_attention = report["focus"]["attention_values"]
    rest_attention = report["rest"]["attention_values"]
    distribution_axis.boxplot(
        [focus_attention, rest_attention],
        tick_labels=["focus", "rest"],
        widths=0.45,
        showmeans=True,
    )
    distribution_axis.scatter([1] * len(focus_attention), focus_attention, alpha=0.7)
    distribution_axis.scatter([2] * len(rest_attention), rest_attention, alpha=0.7)
    if threshold is not None:
        distribution_axis.axhline(threshold, color="black", linestyle="--", alpha=0.6)
    distribution_axis.set_ylabel("attention")
    distribution_axis.set_ylim(-2, 102)
    distribution_axis.grid(axis="y", alpha=0.25)

    for axis in (metric_axis, band_axis):
        axis.axvspan(0, boundary, color="tab:blue", alpha=0.06)
        axis.axvspan(boundary, end, color="tab:orange", alpha=0.06)
        axis.axvline(boundary, color="gray", linestyle=":")
        axis.set_xlim(0, max(end, 1))
        axis.set_xlabel("elapsed seconds — blue: focus, orange: rest")

    status = "ACCEPTED" if report["valid"] else "REJECTED"
    figure.suptitle(
        f"EEG focus/rest calibration — {status}\n"
        f"focus mean {report['focus']['attention_mean']:.1f}, "
        f"rest mean {report['rest']['attention_mean']:.1f}, "
        f"trial accuracy {100 * report['trial_accuracy']:.0f}%"
    )
    figure.savefig(path, dpi=160)
    print(f"Graph:           {path}")
    if show:
        plt.ioff()
        plt.show()
    else:
        plt.close(figure)


def load_calibration_csv(path: Path) -> List[Sample]:
    samples: List[Sample] = []
    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            bands = {name: int(row[name]) for name in BAND_NAMES if row.get(name)}
            samples.append(
                Sample(
                    phase=row["phase"],
                    elapsed=float(row["elapsed_s"]),
                    quality=int(row["signal_quality"]) if row.get("signal_quality") else None,
                    attention=int(row["attention"]) if row.get("attention") else None,
                    meditation=int(row["meditation"]) if row.get("meditation") else None,
                    bands=bands,
                )
            )
    return samples


def finish_report(
    focus: List[Sample], rest: List[Sample], report: Dict[str, object], args: argparse.Namespace
) -> int:
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    plot_calibration(focus, rest, report, args.plot, args.show_plot)
    print(f"Raw calibration: {args.csv}")
    print(f"Report:          {args.output}")
    return 0 if report["valid"] else 1


def run(args: argparse.Namespace) -> int:
    fieldnames = (
        "phase",
        "elapsed_s",
        "signal_quality",
        "attention",
        "meditation",
        *BAND_NAMES,
    )
    if args.replot:
        samples = load_calibration_csv(args.replot)
        focus = [sample for sample in samples if sample.phase == "focus"]
        rest = [sample for sample in samples if sample.phase == "rest"]
        report = analyze(focus, rest, args.max_signal_quality)
        return finish_report(focus, rest, report, args)

    live_state = None
    live_plot = None
    if args.live_plot:
        history = max(45.0, args.warmup + 2 * args.phase_seconds + 5)
        live_state = EEGState(history)
        live_plot = LivePlot(live_state)

    serial_port = _open_serial(args.port, args.baud)
    parser = ThinkGearPacketParser()
    try:
        serial_port.reset_input_buffer()
        print(f"Opened {args.port} at {args.baud} baud (read-only).")
        if args.prepare:
            print("Prepare to focus. Keep still and avoid blinking or jaw movement.")
            for remaining in range(args.prepare, 0, -1):
                print(f"\rStarting in {remaining}...", end="", flush=True)
                time.sleep(1)
            print()

        if args.warmup:
            print("Warm-up: relax and remain still; these packets will be discarded.")
            warm_up(serial_port, parser, args.warmup, live_state, live_plot)

        started = time.monotonic()
        with args.csv.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            print("\aFOCUS NOW: use one steady mental task and keep physically still.")
            focus = collect_phase(
                serial_port,
                parser,
                "focus",
                args.phase_seconds,
                started,
                writer,
                live_state,
                live_plot,
            )
            print("\aRELAX NOW: stop the task, breathe normally, keep eyes open and remain still.")
            rest = collect_phase(
                serial_port,
                parser,
                "rest",
                args.phase_seconds,
                started,
                writer,
                live_state,
                live_plot,
            )

        report = analyze(focus, rest, args.max_signal_quality)
        report["port"] = args.port
        report["baud"] = args.baud
        report["phase_seconds"] = args.phase_seconds
        report["parser_stats"] = vars(parser.stats)
        return finish_report(focus, rest, report, args)
    finally:
        serial_port.close()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/cu.usbmodem5AE71016131")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--phase-seconds", type=float, default=10.0)
    parser.add_argument("--prepare", type=int, default=3)
    parser.add_argument("--warmup", type=float, default=15.0)
    parser.add_argument(
        "--max-signal-quality",
        type=int,
        default=25,
        help="largest poor-signal value accepted for calibration (default: 25)",
    )
    parser.add_argument("--csv", type=Path, default=Path("eeg_calibration.csv"))
    parser.add_argument("--output", type=Path, default=Path("eeg_calibration.json"))
    parser.add_argument("--plot", type=Path, default=Path("eeg_calibration.png"))
    parser.add_argument("--show-plot", action="store_true", help="open the graph after saving it")
    parser.add_argument(
        "--live-plot",
        action="store_true",
        help="show the scrolling ThinkGear graph throughout calibration",
    )
    parser.add_argument("--replot", type=Path, help="graph an existing calibration CSV without serial capture")
    args = parser.parse_args(argv)
    if (
        args.baud <= 0
        or args.phase_seconds <= 0
        or args.prepare < 0
        or args.warmup < 0
        or not 0 <= args.max_signal_quality <= 200
    ):
        parser.error("invalid baud, duration, prepare, warmup, or signal-quality limit")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
