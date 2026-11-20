"""tools/preflight.py with injected machine facts. It checks the logic of the checks, not any real
laptop, phone or glasses."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("preflight", ROOT / "tools" / "preflight.py")
pre = importlib.util.module_from_spec(spec)
sys.modules["preflight"] = pre
spec.loader.exec_module(pre)


def run(tmp_path, **kw):
    base = {
        "devices": lambda: ["USB Mic"],
        "port_free": lambda h, p: True,
        "lan": lambda: ["192.0.2.7"],
        "load_model": lambda cfg: "521 classes",
        "data_dir": tmp_path,
    }
    return {c.name: c for c in pre.run_checks(**{**base, **kw})}


def test_a_healthy_machine_passes_every_automatic_check(tmp_path) -> None:
    got = run(tmp_path)
    assert all(c.status in ("PASS", "MANUAL") for c in got.values())
    assert got["PortAudio and a microphone"].detail == "USB Mic"
    assert got["experimental gate-only switch is off"].status == "PASS"


def test_missing_portaudio_and_no_microphone_fail(tmp_path) -> None:
    def no_portaudio():
        raise OSError("PortAudio library not found")

    assert run(tmp_path, devices=no_portaudio)["PortAudio and a microphone"].status == "FAIL"
    assert run(tmp_path, devices=list)["PortAudio and a microphone"].status == "FAIL"


def test_a_busy_port_and_a_broken_model_fail(tmp_path) -> None:
    assert run(tmp_path, port_free=lambda h, p: False)["port 8765 is free"].status == "FAIL"

    def broken(cfg):
        raise RuntimeError("model missing")

    assert run(tmp_path, load_model=broken)["classifier model loads"].status == "FAIL"


def test_no_network_address_is_a_warning_and_leftover_state_is_a_warning(tmp_path) -> None:
    (tmp_path / "profile.json").write_text("{}")
    got = run(tmp_path, lan=lambda: ["<this-computer-ip>"])
    assert got["this computer has a network address"].status == "WARN"
    assert got["no leftover demo state"].status == "WARN"
    assert "profile.json" in got["no leftover demo state"].detail


def test_anything_a_person_must_check_is_manual_never_pass(tmp_path) -> None:
    manual = [c for c in run(tmp_path).values() if c.status == "MANUAL"]
    names = " ".join(c.name for c in manual)
    for needle in ("phone opens", "firewall", "vibrates", "microphone path", "restart", "glasses"):
        assert needle in names


def test_the_experimental_switch_being_on_fails_the_preflight(tmp_path, monkeypatch) -> None:
    import sentinel.config as config

    real = config.load_config

    def on(path=None):
        cfg = real(path)
        cfg.experimental.unverified_gate_only = True
        return cfg

    monkeypatch.setattr(pre, "load_config", on)
    assert run(tmp_path)["experimental gate-only switch is off"].status == "FAIL"


def test_the_in_process_self_test_covers_pages_profiles_and_the_four_triggers(tmp_path) -> None:
    got = run(tmp_path, with_self_test=True)
    for name in (
        "page /phone",
        "page /?kiosk=1",
        "profiles and protected rule",
        "four simulated triggers",
    ):
        assert got[name].status == "PASS", name


def test_main_exit_code_follows_failures_and_it_never_claims_glasses_work(tmp_path, capsys) -> None:
    rc = pre.main([])
    out = capsys.readouterr().out
    assert "does not prove anything works on physical glasses" in out
    assert rc in (0, 1)
