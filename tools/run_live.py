"""Run Sentinel on the live microphone.

Usage: python tools/run_live.py [--sim] [--device NAME_OR_INDEX] [--record]
Stop with Ctrl+C. No raw audio is stored unless --record is given.
With --sim (or --display console,sim) the dashboard and glasses simulator open at the
printed address.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from sentinel.adapters import make_audio_source
from sentinel.adapters.audio_mic import list_devices
from sentinel.classify.yamnet import build_classifier_stage
from sentinel.config import ConfigError, load_config
from sentinel.metrics import CpuMeter, Metrics
from sentinel.pipeline import Pipeline
from sentinel.runtime import start_runtime


def parse_device(value: str | None) -> str | int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def make_trace_printer():
    """Prints what the classifier hears, for finding a sound it recognizes. Read only: it never
    changes a decision. Quiet windows are skipped and repeats are thinned out."""
    last = {"cls": None, "t": -10.0}

    def show(e: dict) -> None:
        kind = e["kind"]
        if kind == "observation":
            score, name = e.get("top_score") or 0.0, e.get("top_class") or "?"
            if score < 0.20:
                return
            if name == last["cls"] and e["t"] - last["t"] < 1.0:
                return
            last["cls"], last["t"] = name, e["t"]
            rev = e.get("reversing_score", 0.0)
            print(f"  heard: {name} {score:.2f}  (reversing beeps score {rev:.2f})")
        elif kind in ("candidate", "suppressed", "unconfirmed", "confirmed"):
            print(f"  tonal gate candidate: {kind}")

    return show


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--input",
        choices=["mic", "glasses"],
        default="mic",
        help="audio input: mic is the external microphone (L1), glasses is L0",
    )
    parser.add_argument("--device", default=None, help="sounddevice name or index")
    parser.add_argument(
        "--display", default=None, help="override display.adapter, e.g. console,sim"
    )
    parser.add_argument(
        "--sim", action="store_true", help="also show alerts in the browser simulator"
    )
    parser.add_argument(
        "--lan",
        action="store_true",
        help="let phones on the same network open the page (no password)",
    )
    parser.add_argument("--no-dashboard", action="store_true", help="never start the dashboard")
    parser.add_argument("--db", default=None, help="event log path (default: config eventlog.path)")
    parser.add_argument("--record", action="store_true", help="save raw audio to data/recordings/")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--no-classifier", action="store_true", help="gate only, no model needed")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument(
        "--show-classifier",
        action="store_true",
        help="diagnostic: print what the classifier hears (top class and score) and what happens "
        "to tonal gate candidates. Changes nothing about what is shown",
    )
    parser.add_argument(
        "--experimental-unverified-gate-only",
        action="store_true",
        help="EXPERIMENTAL, off by default: tonal gate hits alert alone, labeled UNVERIFIED. "
        "Not reliable alarm detection.",
    )
    args = parser.parse_args(argv)
    if args.list_devices:
        print(list_devices())
        return 0

    runtime = None
    try:
        cfg = load_config(args.config)
        cfg.experimental.unverified_gate_only |= args.experimental_unverified_gate_only
        if args.experimental_unverified_gate_only:
            print("EXPERIMENTAL: gate hits alert alone and are labeled UNVERIFIED.")
        display_name = args.display
        if args.sim:
            display_name = (display_name or cfg.display.adapter) + ",sim"
        runtime = start_runtime(
            cfg, "live", display_name, args.db, use_dashboard=not args.no_dashboard, lan=args.lan
        )
        stage = None if args.no_classifier else build_classifier_stage(cfg.classifier)
        if stage is None and not cfg.experimental.unverified_gate_only:
            print("No classifier: BACKUP ALARM cannot show (gate hits need the classifier).")
        metrics = Metrics()
        source = make_audio_source(
            cfg, args.input, parse_device(args.device) if args.device else None, metrics
        )
    except (ConfigError, RuntimeError, NotImplementedError) as err:
        print(err, file=sys.stderr)
        if runtime:
            runtime.close()
        return 2

    def report(event_id: str, processing_ms: float, alarm_to_display_ms: float) -> None:
        print(
            f"  {event_id}: processing {processing_ms:.1f} ms, "
            f"alarm-to-display {alarm_to_display_ms:.0f} ms (software only)"
        )

    pipeline = Pipeline(
        cfg,
        source,
        runtime.display,
        metrics,
        on_latency=report,
        record=args.record,
        classifier=stage,
        eventlog=runtime.eventlog,
        source_label="live",
        on_trace=make_trace_printer() if args.show_classifier else None,
    )
    runtime.attach(pipeline)
    if threading.current_thread() is threading.main_thread():  # signals need the main thread
        signal.signal(signal.SIGINT, lambda *_: pipeline.stop())
    cpu = CpuMeter()
    print(f"Listening on the {args.input} input. Ctrl+C to stop.")
    print("Sentinel is an assistive alert, not a certified safety device.")
    if runtime.url:
        print(f"Dashboard and simulator: {runtime.url}")
        for u in runtime.phone_urls:
            print(f"Phone view (same network, no password): {u}")
    if args.record:
        print("Recording raw audio to data/recordings/ (--record)")
    try:
        pipeline.run(max_seconds=args.duration)
    finally:
        runtime.close()
    if metrics.dropped_frames:
        print(
            f"WARNING: {metrics.dropped_frames} audio frames were dropped because processing "
            "could not keep up. Detections may have been missed. Try --no-classifier."
        )
    print(f"Summary: {metrics.summary()}  cpu={cpu.usage():.0%} of one core")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
