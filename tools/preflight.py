"""Check THIS computer before a demo. Run it on the actual demo laptop.

  python tools/preflight.py              # environment checks
  python tools/preflight.py --self-test  # also start the app in process and hit its endpoints

Each line is PASS, FAIL, WARN or MANUAL. MANUAL means a person must check it: this program cannot
see your phone, your Wi-Fi, your firewall or your glasses, and it never reports them as passed.
A passing preflight is not proof that anything works on physical glasses.
Exit code 1 if any check FAILs.
"""

from __future__ import annotations

import argparse
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sentinel.config import PROJECT_ROOT, ConfigError, SentinelConfig, load_config

MANUAL = [
    ("phone opens the Phone: address printed by --lan", "same network, no client isolation"),
    ("firewall allows inbound on the dashboard port", "allow it for the demo network only"),
    ("phone vibrates on an interrupt alert", "browser dependent, never verified here"),
    ("live microphone path hears a real sound", "play a sound at the mic and watch the page"),
    ("start, Ctrl+C stop and restart cleanly", "run the demo twice"),
    ("glasses show a message", "only tools/hw_proof.py plus a person looking at the lens"),
]


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS | FAIL | WARN | MANUAL
    detail: str


def input_devices() -> list[str]:
    """Names of microphones PortAudio can see. Raises OSError if PortAudio is missing."""
    import sounddevice as sd

    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def model_loads(cfg: SentinelConfig) -> str:
    from sentinel.classify.yamnet import YamnetClassifier

    clf = YamnetClassifier(cfg.classifier)
    return f"{len(clf.class_names)} classes, window {clf.window_s:.3f} s"


def self_test(cfg: SentinelConfig) -> list[Check]:
    """The app wired in process with a test client. This is not a network or phone test."""
    from fastapi.testclient import TestClient

    from sentinel.dashboard.server import Hub, create_app
    from sentinel.eventlog import EventLog
    from sentinel.profile import ProfileStore

    out: list[Check] = []
    store = ProfileStore.from_config(cfg)
    with TestClient(create_app(EventLog(), Hub(), profile=store, stats_interval_s=60)) as c:
        out.append(
            Check("page /phone", "PASS" if c.get("/phone").status_code == 200 else "FAIL", "")
        )
        out.append(
            Check("page /?kiosk=1", "PASS" if c.get("/?kiosk=1").status_code == 200 else "FAIL", "")
        )
        got = c.get("/api/profiles").json()
        ids = [p["id"] for p in got["profiles"]]
        ok = ids == ["focus", "home", "sleep", "commute"] and got["protected"] == ["fire_alarm"]
        out.append(Check("profiles and protected rule", "PASS" if ok else "FAIL", f"{ids}"))
        seen = []
        for trig in ("leave_home", "arrive_home", "start_focus", "go_to_sleep"):
            r = c.post("/api/context/simulate", json={"trigger": trig})
            seen.append((trig, r.json().get("active") if r.status_code == 200 else None))
        c.post("/api/context/manual")
        want = ["commute", "home", "focus", "sleep"]
        ok = [a for _, a in seen] == want
        out.append(Check("four simulated triggers", "PASS" if ok else "FAIL", str(seen)))
    return out


def _ok(cond: bool) -> str:
    return "PASS" if cond else "FAIL"


def run_checks(
    cfg_path: str | None = None,
    devices: Callable[[], list[str]] = input_devices,
    port_free: Callable[[str, int], bool] = port_is_free,
    lan: Callable[[], list[str]] | None = None,
    load_model: Callable[[SentinelConfig], str] = model_loads,
    data_dir: Path | None = None,
    with_self_test: bool = False,
) -> list[Check]:
    from sentinel.runtime import lan_addresses

    lan = lan or lan_addresses
    out: list[Check] = []
    v = sys.version_info
    out.append(
        Check("python 3.11 or newer", "PASS" if v >= (3, 11) else "FAIL", f"{v.major}.{v.minor}")
    )
    try:
        cfg = load_config(cfg_path)
    except ConfigError as err:
        return [*out, Check("config", "FAIL", str(err).splitlines()[0])]
    out.append(Check("config loads", "PASS", f"hash {cfg.config_hash()}"))
    exp = cfg.experimental.unverified_gate_only
    out.append(
        Check(
            "experimental gate-only switch is off",
            "FAIL" if exp else "PASS",
            "ON in this config: not allowed for the stage demo" if exp else "off (default)",
        )
    )
    try:
        out.append(Check("classifier model loads", "PASS", load_model(cfg)))
    except Exception as err:  # a missing or broken model must be reported, not crash the check
        out.append(Check("classifier model loads", "FAIL", f"{type(err).__name__}: {err}"))
    try:
        mics = devices()
        out.append(
            Check(
                "PortAudio and a microphone",
                "PASS" if mics else "FAIL",
                ", ".join(mics[:3]) or "no input device found",
            )
        )
    except OSError as err:
        out.append(Check("PortAudio and a microphone", "FAIL", f"PortAudio missing: {err}"))
    port = cfg.dashboard.port
    out.append(
        Check(f"port {port} is free", "PASS" if port_free("127.0.0.1", port) else "FAIL",
              "another copy may be running" if not port_free("127.0.0.1", port) else "")
    )  # fmt: skip
    ips = [ip for ip in lan() if not ip.startswith("<")]
    out.append(
        Check("this computer has a network address", "PASS" if ips else "WARN",
              ", ".join(ips) or "none found: --lan will print a placeholder")
    )  # fmt: skip
    root = data_dir or (PROJECT_ROOT / "data")
    stale = [n for n in ("profile.json", "demo_events.sqlite") if (root / n).exists()]
    msg = f"{stale} exist. profile.json keeps old profile edits: delete it" if stale else ""
    out.append(Check("no leftover demo state", "WARN" if stale else "PASS", msg))
    try:
        probe = root / ".preflight_write_test"
        root.mkdir(parents=True, exist_ok=True)
        probe.write_text("x")
        probe.unlink()
        out.append(Check("data folder is writable", "PASS", str(root)))
    except OSError as err:
        out.append(Check("data folder is writable", "FAIL", str(err)))
    if with_self_test:
        out += self_test(cfg)
    out += [Check(n, "MANUAL", d) for n, d in MANUAL]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--self-test", action="store_true", help="also exercise the app in process")
    args = parser.parse_args(argv)
    checks = run_checks(args.config, with_self_test=args.self_test)
    for c in checks:
        print(f"[{c.status:<6}] {c.name}" + (f": {c.detail}" if c.detail else ""))
    fails = [c for c in checks if c.status == "FAIL"]
    print(
        f"\n{len(fails)} failed, {sum(c.status == 'MANUAL' for c in checks)} for a person to check."
    )
    print("A passing preflight does not prove anything works on physical glasses.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
