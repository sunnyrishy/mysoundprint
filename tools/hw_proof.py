"""First hardware proof, staged and deliberately small. Works with ANY display adapter.

Stages (each one prints what a human should SEE on the lens, then waits):
  1. show message A
  2. replace it with message B, with no clear in between
  3. update the display N times in a row (A, B, A, B ...)
  4. clear, and confirm the lens is empty
It writes a JSON log of every call with its start time, duration and any error, so the log can be
compared with a slow-motion video of the lens. The adapter call times are measured here. WHAT
APPEARED ON THE LENS IS NOT KNOWN BY THIS PROGRAM: a human fills in the observed column.

With the stub MicroLumin adapter this exits cleanly and says it is not implemented. Nothing here
claims anything works on the M6 until a person has watched it on physical glasses.

Extended cases (--extended), each logged separately. For every case three things are kept apart:
  * software_call: did our call return or raise (measured here),
  * device_ack: whether the SDK or device confirmed it. UNKNOWN today: the DisplayAdapter
    protocol has no acknowledgement and no SDK is known,
  * observed_on_lens: what a person actually saw (filled in by hand, never by this program).
Cases: rapid alternating messages, a stale clear after a newer message, a delayed old show that
times out and lands after a newer one, short and long messages, a repeated-update soak of several
minutes, and two manual cases (disconnect during an update, reconnect) that need a person to cut
and restore the link. Durations are call times on this computer. They are not end to end latency.

Usage: python tools/hw_proof.py [--display console|sim|bridge|microlumin] [--updates 20]
                                [--hold-s 2.0] [--log reports/hw_proof.json]
                                [--extended] [--soak-minutes 5] [--manual]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from sentinel.adapters import make_display
from sentinel.adapters.base import AsyncDisplay, fit_lines
from sentinel.config import ConfigError, load_config
from sentinel.types import Alert, AlertClass, DisplayAdapter

DEVICE_ACK = "UNKNOWN"  # no SDK is known and the adapter protocol returns nothing from the device

# What a person records after watching the lens. verdict is PASS or FAIL, set by hand only.
# failure_category, when FAIL, is one of FAILURE_CATEGORIES.
FAILURE_CATEGORIES = {
    "stale_message": "text from an older message is on the lens when a newer one should be, or an "
    "old message comes back after a newer one appeared",
    "unexpected_clear": "the lens is blank, or lost part of the text, when a message should be "
    "visible",
    "failed_update": "the newest message never appeared, appeared wrong, was skipped, or the "
    "software call raised for an update that should have worked",
    "no_recovery": "after the link came back the newest message did not appear without restarting "
    "the program (use NOT_SUPPORTED, not PASS, if the SDK has no reconnect)",
}
GUIDANCE: dict[str, dict[str, str]] = {
    "rapid_alternating": {
        "pass_if": "the lens ends on DOORBELL alone, no ghost of A, no blank, no stuck frame",
        "fail_if": "stale_message if A remains or returns, unexpected_clear if blank, "
        "failed_update if it ends on A or stops updating",
    },
    "stale_clear": {
        "pass_if": "DOORBELL is still on the lens after clear(A)",
        "fail_if": "unexpected_clear if B disappears, stale_message if A appears",
    },
    "delayed_old_show": {
        "pass_if": "DOORBELL is on the lens and old_show_finished_after_newer is false, or the "
        "adapter ignored the late show",
        "fail_if": "stale_message if HELLO is on the lens (the old show overwrote the newer one)",
    },
    "short_and_long": {
        "pass_if": "each message appears complete on its own lines, nothing wrapped or cut, "
        "within the DOCUMENTED limits",
        "fail_if": "failed_update if text is cut, wrapped or garbled. The limits used are ASSUMED "
        "until documented or physically verified, so a pass here is not a limit",
    },
    "soak": {
        "pass_if": "always the newest message for the whole run, no freeze, drift, dimming, "
        "flicker or disconnect, and zero software errors",
        "fail_if": "failed_update for any skipped or frozen update or software error, "
        "stale_message if old text returns, unexpected_clear if it blanks",
    },
    "disconnect_during_update": {
        "pass_if": "record what the lens did (kept the last message or blanked) and from which "
        "update our calls began to raise. There is no pass without a documented expectation",
        "fail_if": "failed_update if our calls never raised after the cut (the adapter cannot "
        "see the loss), unexpected_clear if it blanked when the SDK says it should not",
    },
    "reconnect": {
        "pass_if": "DOORBELL appears after the link is restored, without restarting the program",
        "fail_if": "no_recovery if it does not. NOT_SUPPORTED if the SDK has no reconnect",
    },
    "final_clear": {
        "pass_if": "the lens is empty and stays empty",
        "fail_if": "stale_message if any text remains, failed_update if the clear call raised",
    },
}
STAGE_GUIDANCE = {
    "1_show": "PASS if HELLO is readable. FAIL (failed_update) if it does not appear",
    "2_replace": "PASS if DOORBELL / TWO replaced A with no part of A left. FAIL stale_message if "
    "any of A remains, unexpected_clear if blank",
    "3_updates": "PASS if each of the updates shows the newest message and the lens never "
    "cleared by itself. FAIL failed_update for a skipped or stuck update, unexpected_clear for a "
    "blank gap, stale_message for old text",
    "4_clear": "PASS if the lens is empty. FAIL stale_message if text remains",
}

# The eleven operations of the first hardware proof, in order, and where each one is logged.
PROCEDURE = [
    (1, "show one message", "calls:1_show", None),
    (2, "replace it with another", "calls:2_replace", None),
    (3, "perform 20 updates", "calls:3_updates", None),
    (4, "rapidly alternate messages", "extended_cases:rapid_alternating", "--extended"),
    (5, "clear an old event after a newer message", "extended_cases:stale_clear", "--extended"),
    (6, "delay an old show past a newer message", "extended_cases:delayed_old_show", "--extended"),
    (7, "disconnect during an update", "manual_cases:disconnect_during_update", "--manual"),
    (8, "reconnect, if the SDK supports it", "manual_cases:reconnect", "--manual"),
    (9, "short and long messages within the limits", "extended_cases:short_and_long", "--extended"),
    (10, "multi-minute update soak", "extended_cases:soak", "--extended"),
    (11, "clear the final message", "final_clear", None),
]


def procedure_status(log: dict[str, Any]) -> list[dict[str, Any]]:
    """Which of the eleven operations were run, and where their results are in the log. A
    verdict is only ever filled in by a person after watching the lens."""
    out = []
    for n, op, where, flag in PROCEDURE:
        kind, _, key = where.partition(":")
        if kind == "calls":
            ran = any(c["stage"] == key for c in log.get("calls", []))
        elif kind == "final_clear":
            ran = "final_clear" in log
        else:
            ran = any(c["case"] == key for c in log.get(kind, []))
        out.append(
            {"step": n, "operation": op, "logged_in": where, "ran": ran,
             "needs_flag": flag, "verdict": None}
        )  # fmt: skip
    return out


def final_clear(display: DisplayAdapter) -> dict[str, Any]:
    """Operation 11: clear whatever is on the lens last, as a logged case of its own."""
    call = _timed(display, "clear", None, time.perf_counter())
    return _case("final_clear", "clear the last message", "nothing on the lens", [call])


FIRST_TEST = [
    # (stage, call, event id, text, what a person should see)
    ("1_hello", "show", "first_1", "HELLO", "HELLO"),
    ("2_replace", "show", "first_2", "DOORBELL", "DOORBELL only, nothing left of HELLO"),
    ("3_clear", "clear", "first_2", None, "an empty lens"),
]
FIRST_TEST_NOTE = (
    "Short single-line messages on purpose. No line or character limit is assumed here: the SDK "
    "documentation or this test on the lens must establish it. Record how each message rendered. "
    "Run the official SDK sample unchanged before this, and initialize with documented calls only."
)


def run_first_test(
    display: DisplayAdapter, hold_s: float = 3.0, sleep=time.sleep
) -> dict[str, Any]:
    """The very first physical test: HELLO, replace with DOORBELL, clear. Each step logs
    software_call, device_ack (UNKNOWN) and an empty observed_on_lens, verdict and
    failure_category. Repeated updates, rapid alternation, stale clear, delayed old show,
    disconnect and the soak are the later cases (--extended, --manual), to be run only after this
    passes. Nothing here says anything is visible: only a person can."""
    t0 = time.perf_counter()
    steps: list[dict[str, Any]] = []
    for stage, kind, eid, text, expect in FIRST_TEST:
        rec = _timed(display, kind, _alert(eid, text or "x"), t0)
        rec.update(
            {"stage": stage, "device_ack": DEVICE_ACK, "expected_on_lens": expect,
             "observed_on_lens": None, "verdict": None, "failure_category": None}
        )  # fmt: skip
        steps.append(rec)
        sleep(hold_s)
    return {
        "first_test": steps,
        "software_errors": sum(1 for x in steps if x["error"]),
        "caps": str(display.capabilities()),
        "note": FIRST_TEST_NOTE,
        "failure_categories": FAILURE_CATEGORIES,
        "device_ack_note": "UNKNOWN: the DisplayAdapter protocol returns nothing from the device.",
        "human_must_check": [f"{x[0]}: {x[4]}" for x in FIRST_TEST],
    }


def _alert(event_id: str, line1: str, line2: str | None = None) -> Alert:
    return Alert(event_id, AlertClass.REVERSING_ALARM, 1, line1, line2, False, 30.0)


def run_proof(
    display: DisplayAdapter,
    updates: int = 20,
    hold_s: float = 2.0,
    sleep=time.sleep,
) -> dict[str, Any]:
    """Drive the display through the four stages. Returns the log (also what gets written)."""
    calls: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    def call(stage: str, kind: str, fn, expect: str) -> None:
        start = time.perf_counter()
        error = None
        try:
            fn()
        except Exception as exc:  # the proof must record a failure, not stop on it
            error = f"{type(exc).__name__}: {exc}"
        calls.append(
            {
                "stage": stage, "call": kind, "t_start_s": round(start - t0, 4),
                "duration_ms": round((time.perf_counter() - start) * 1000.0, 3),
                "error": error, "software_call": "error" if error else "ok",
                "device_ack": DEVICE_ACK, "expected_on_lens": expect, "observed_on_lens": None,
                "verdict": None,
            }
        )  # fmt: skip

    a, b = _alert("proof_a", "HELLO"), _alert("proof_b", "DOORBELL")
    call("1_show", "show", lambda: display.show(a), "HELLO")
    sleep(hold_s)
    call("2_replace", "show", lambda: display.show(b), "DOORBELL only, no A left behind")
    sleep(hold_s)
    for i in range(updates):
        nxt = a if i % 2 == 0 else b
        call("3_updates", "show", lambda m=nxt: display.show(m), f"{nxt.line1} only")
    sleep(hold_s)
    call("4_clear", "clear", lambda: display.clear(None), "nothing on the lens")
    sleep(hold_s)
    errors = [c for c in calls if c["error"]]
    return {
        "caps": str(display.capabilities()),
        "updates": updates,
        "calls": calls,
        "adapter_errors": len(errors),
        "human_must_check": [
            "stage 1: message A is readable",
            "stage 2: B replaced A and no part of A remains",
            "stage 3: every update shows the newest message, none are skipped or stuck, no flicker",
            "stage 3: the display did not clear by itself between updates",
            "stage 4: the lens is empty, no stale text",
        ],
        "stage_guidance": STAGE_GUIDANCE,
        "failure_categories": FAILURE_CATEGORIES,
        "device_ack_note": "UNKNOWN: the DisplayAdapter protocol returns nothing from the device.",
        "note": "Call durations are measured on this computer. They are not time to photons.",
    }


# ---- extended cases ---------------------------------------------------------------------------
class _Tap:
    """Wraps an adapter, records when each inner call starts and finishes, and can delay chosen
    shows to imitate a slow link. Nothing is simulated about the glasses themselves."""

    def __init__(self, inner: DisplayAdapter, delay_s: dict[str, float] | None = None) -> None:
        self.inner, self.delay_s, self.events = inner, delay_s or {}, []
        self._t0 = time.perf_counter()
        self._lock = threading.Lock()

    def _log(self, what: str) -> None:
        with self._lock:
            self.events.append((round(time.perf_counter() - self._t0, 3), what))

    def capabilities(self):
        return self.inner.capabilities()

    def show(self, alert: Alert) -> None:
        self._log(f"show {alert.event_id} start")
        time.sleep(self.delay_s.get(alert.event_id, 0.0))
        self.inner.show(alert)
        self._log(f"show {alert.event_id} done")

    def clear(self, event_id: str | None = None) -> None:
        self._log(f"clear {event_id} start")
        self.inner.clear(event_id)
        self._log(f"clear {event_id} done")

    def close(self) -> None:
        self.inner.close()


def _case(name: str, what: str, expect: str, calls: list[dict[str, Any]], **extra: Any):
    return {
        "case": name, "what": what, "expected_on_lens": expect, "calls": calls,
        "software_errors": sum(1 for c in calls if c.get("error")),
        "device_ack": DEVICE_ACK, "observed_on_lens": None, "verdict": None,
        "failure_category": None, **GUIDANCE.get(name, {}), **extra,
    }  # fmt: skip


def _timed(display: DisplayAdapter, kind: str, alert: Alert | None, t0: float) -> dict[str, Any]:
    start, error = time.perf_counter(), None
    try:
        if kind == "show":
            assert alert is not None
            display.show(alert)
        else:
            display.clear(alert.event_id if alert else None)
    except Exception as exc:  # recorded, never raised: a failing link is the thing being studied
        error = f"{type(exc).__name__}: {exc}"
    return {
        "call": kind, "event_id": alert.event_id if alert else None,
        "text": alert.line1 if alert else None, "t_start_s": round(start - t0, 4),
        "duration_ms": round((time.perf_counter() - start) * 1000, 3),
        "software_call": "error" if error else "ok", "error": error,
    }  # fmt: skip


def run_extended(
    display: DisplayAdapter,
    hold_s: float = 2.0,
    soak_minutes: float = 5.0,
    soak_interval_s: float = 2.0,
    sleep=time.sleep,
    caps_documented: bool = False,
) -> list[dict[str, Any]]:
    """The extra cases that need no human action. Returns one record per case."""
    t0 = time.perf_counter()
    caps = display.capabilities()
    a, b = _alert("proof_a", "HELLO"), _alert("proof_b", "DOORBELL")
    cases: list[dict[str, Any]] = []

    # 1. rapid alternating messages: no pause at all
    calls = [_timed(display, "show", a if i % 2 == 0 else b, t0) for i in range(40)]
    sleep(hold_s)
    cases.append(_case(
        "rapid_alternating", "40 shows, A and B alternating, no pause",
        "the last message (DOORBELL) only; no flicker of old text", calls,
    ))  # fmt: skip

    # 2. a stale clear after a newer message
    calls = [_timed(display, "show", a, t0), _timed(display, "show", b, t0)]
    calls.append(_timed(display, "clear", a, t0))  # clear for A, which B has already replaced
    sleep(hold_s)
    cases.append(_case(
        "stale_clear", "show A, show B, then clear(A): A is no longer current",
        "DOORBELL still on the lens (a stale clear must not erase a newer message)", calls,
    ))  # fmt: skip

    # 3. delayed old show that times out and may land after a newer one
    tap = _Tap(display, {"proof_a": 1.2})
    wrapped = AsyncDisplay(tap, call_timeout_s=0.4)
    wrapped.show(a)
    time.sleep(0.1)  # real time: this case is about real call timing
    wrapped.show(b)
    time.sleep(2.0)  # long enough for the delayed A to finish after B
    wrapped.close()
    order = list(tap.events)
    done = [w for _, w in order if w.endswith("done")]
    hazard = (
        "show proof_a done" in done
        and "show proof_b done" in done
        and done.index("show proof_a done") > done.index("show proof_b done")
    )
    cases.append(_case(
        "delayed_old_show",
        "show A delayed 1.2 s on the link with a 0.4 s timeout, then show B at once",
        "DOORBELL. If HELLO is on the lens, the old show overwrote the newer one",
        [], inner_call_order=order, old_show_finished_after_newer=hazard,
        note="A true here means the adapter needs a sequence check (docs/M6_INTEGRATION_PLAN.md).",
    ))  # fmt: skip

    # 4. short and long messages within the limits the config declares (ASSUMED until documented)
    limit = caps.max_chars_per_line
    msgs = [("S", "1"), ("X" * limit, "Y" * limit), ("W" * (limit - 1), "M" * (limit - 1))]
    calls = []
    for i, (l1, l2) in enumerate(msgs):
        calls.append(_timed(display, "show", _alert(f"proof_len_{i}", l1, l2), t0))
        sleep(hold_s)
    over = _alert("proof_over", "Z" * (limit + 6), None)
    cases.append(_case(
        "short_and_long", f"messages of 1, {limit} and {limit - 1} characters per line",
        "each message complete, on its own lines, nothing wrapped or cut",
        calls, caps_are_assumed=not caps_documented,
        note=(
            "Limits come from display.caps in the config, which you said are documented "
            "(--caps-documented). Verify them on the lens."
            if caps_documented
            else "Limits come from display.caps in the config and are ASSUMED until documented "
            "or verified on a lens. Run this case for real only after the SDK states the limits "
            "and display.caps matches, then pass --caps-documented."
        ),
        software_truncation_of_an_over_limit_line=fit_lines(over, caps),
    ))  # fmt: skip

    # 5. repeated updates for several minutes
    calls, end, i = [], time.perf_counter() + soak_minutes * 60, 0
    while time.perf_counter() < end:
        calls.append(_timed(display, "show", a if i % 2 == 0 else b, t0))
        i += 1
        sleep(soak_interval_s)
    errs = [c for c in calls if c["error"]]
    cases.append(_case(
        "soak", f"alternating show every {soak_interval_s} s for {soak_minutes} min",
        "always the newest message; no freeze, drift, dimming, flicker or disconnect",
        calls, calls_made=len(calls), errors=len(errs),
        slowest_call_ms=max((c["duration_ms"] for c in calls), default=None),
    ))  # fmt: skip
    return cases


def run_manual_cases(display: DisplayAdapter, ask=input, sleep=time.sleep) -> list[dict[str, Any]]:
    """Cases that need a person to cut and restore the link. Skipped unless asked for."""
    t0 = time.perf_counter()
    a, b = _alert("proof_a", "HELLO"), _alert("proof_b", "DOORBELL")
    ask("DISCONNECT CASE: press Enter, then switch off the glasses or their link within 5 s.")
    calls = []
    for i in range(20):
        calls.append(_timed(display, "show", a if i % 2 == 0 else b, t0))
        sleep(0.5)
    first_err = next((i for i, c in enumerate(calls) if c["error"]), None)
    out = [_case(
        "disconnect_during_update", "20 updates, 0.5 s apart, while the link is cut by hand",
        "the lens keeps the last delivered message or goes blank. Record which",
        calls, first_error_index=first_err,
        note="A call that does not raise after the cut means the adapter cannot see the loss.",
    )]  # fmt: skip
    ask("RECONNECT CASE (only if the SDK supports reconnect): restore the link, then press Enter.")
    calls = []
    for _ in range(10):
        calls.append(_timed(display, "show", b, t0))
        sleep(1.0)
    ok_idx = next((i for i, c in enumerate(calls) if not c["error"]), None)
    out.append(_case(
        "reconnect", "10 shows, 1 s apart, after the link is restored",
        "DOORBELL appears without restarting this program",
        calls, first_success_index=ok_idx,
        note="If the SDK has no reconnect, the program must be restarted. Say so in SDK_NOTES.",
    ))  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--display", default=None, help="adapter to test (default: config)")
    parser.add_argument("--updates", type=int, default=20)
    parser.add_argument("--hold-s", type=float, default=2.0)
    parser.add_argument("--log", default="reports/hw_proof.json")
    parser.add_argument("--extended", action="store_true", help="also run the extra cases")
    parser.add_argument("--soak-minutes", type=float, default=5.0)
    parser.add_argument(
        "--caps-documented", action="store_true",
        help="display.caps in the config matches the SDK's documented limits (else ASSUMED)",
    )  # fmt: skip
    parser.add_argument("--manual", action="store_true", help="also run disconnect and reconnect")
    parser.add_argument(
        "--first-test", action="store_true",
        help="only the first physical test: HELLO, replace with DOORBELL, clear",
    )  # fmt: skip
    args = parser.parse_args(argv)
    name = args.display or "(config)"
    try:
        cfg = load_config(args.config)
        name = args.display or cfg.display.adapter
        display = make_display(cfg, name)
    except NotImplementedError as err:
        print(f"SKIPPED: display adapter '{name}' is not implemented.\n{err}")
        return 0
    except (ConfigError, RuntimeError) as err:
        print(err, file=sys.stderr)
        return 2
    if args.first_test:
        log = run_first_test(display, args.hold_s)
        display.close()
        path = Path(args.log)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(log, indent=2) + "\n")
        print(
            f"{len(log['first_test'])} steps, {log['software_errors']} software errors. Log: {path}"
        )
        print(
            "Fill in observed_on_lens and verdict from what you SAW. A returned call is not proof."
        )
        return 1 if log["software_errors"] else 0
    log = run_proof(display, args.updates, args.hold_s)
    if args.extended:
        log["extended_cases"] = run_extended(
            display, args.hold_s, args.soak_minutes, caps_documented=args.caps_documented
        )
    if args.manual:
        log["manual_cases"] = run_manual_cases(display)
    log["final_clear"] = final_clear(display)
    log["procedure"] = procedure_status(log)
    display.close()
    path = Path(args.log)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2) + "\n")
    print(f"{len(log['calls'])} calls, {log['adapter_errors']} adapter errors. Log: {path}")
    print(
        "Now fill in observed_on_lens and verdict (PASS or FAIL) from the video. "
        "Do not claim success before that."
    )
    return 1 if log["adapter_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
