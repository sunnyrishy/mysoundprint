"""Simulated context switching, and the same-doorbell, three-profile demo flow.

Everything is deterministic. The doorbell is an INJECTED classifier result (a scripted double), not
audio. These tests check profile policy, event handling and the API. They say nothing about whether
the real classifier recognises a real doorbell: no real doorbell recording exists yet."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.adapters.display_sim import SimDisplay
from sentinel.classify.yamnet import ClassifierStage, resolve_class_indices
from sentinel.config import DEFAULT_CONFIG_PATH, ConfigError, config_from_dict, load_config
from sentinel.dashboard.server import Hub, create_app
from sentinel.eventlog import EventLog
from sentinel.pipeline import Pipeline
from sentinel.profile import ProfileError, ProfileStore
from sentinel.types import SAMPLE_RATE, AlertClass, DisplayCaps

CFG = load_config()
RAW = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
A = AlertClass
ROOT = Path(__file__).resolve().parents[1]
NAMES = [f"class {i}" for i in range(521)]
for _i, _n in enumerate(
    sorted({n for r in CFG.classifier.catalog().values() for n in r.names}), 40
):
    NAMES[_i] = _n
IDX = resolve_class_indices(CFG.classifier, NAMES)


class Scripted:
    """Reports the given sounds as strongly present from the second classifier call on."""

    window_len = 8000

    def __init__(self, sounds: list[A]) -> None:
        self.class_indices, self._sounds, self._calls = IDX, sounds, 0

    def infer(self, window):
        out = np.zeros(521, dtype=np.float32)
        if self._calls >= 2:
            for s in self._sounds:
                out[IDX[s][0]] = 0.9
        self._calls += 1
        return out


def run(sounds: list[A], store: ProfileStore, log: EventLog | None = None):
    """One 6 s run with the injected sounds. Returns the display messages and the event rows."""
    log = log or EventLog()
    messages: list[dict] = []
    display = SimDisplay(messages.append, DisplayCaps(supports_blink=True))
    stage = ClassifierStage(Scripted(sounds), CFG.classifier)
    audio = np.zeros(6 * SAMPLE_RATE, dtype=np.float32)
    Pipeline(CFG, AudioFileSource(audio, t0=0.0), display, eventlog=log, classifier=stage,
             profile=store, source_label="synthetic").run_sync()  # fmt: skip
    return [m for m in messages if m["type"] == "alert"], log.list()


def doorbell_in(profile: str):
    store = ProfileStore.from_config(CFG)
    store.set_active(profile)
    log = EventLog()
    alerts, rows = run([A.DOORBELL], store, log)
    return alerts, rows, store, log


# ---- context switching ----
def test_manual_switching_still_works_and_is_marked_manual() -> None:
    store = ProfileStore.from_config(CFG)
    assert store.context() == {
        "mode": "manual",
        "simulated": False,
        "trigger": None,
        "trigger_label": None,
    }
    store.set_active("sleep")
    assert store.active == "sleep" and not store.context()["simulated"]


def test_a_simulated_trigger_changes_the_profile_and_is_marked_simulated() -> None:
    store = ProfileStore.from_config(CFG)
    assert store.active == "home"
    view = store.simulate("leave_home")  # Home to Commute
    assert store.active == "commute" and view["active"] == "commute"
    ctx = store.context()
    assert ctx == {"mode": "simulated", "simulated": True, "trigger": "leave_home",
                   "trigger_label": "Leaving home"}  # fmt: skip
    assert view["context"] == ctx and store.snapshot()["context"] == ctx


def test_every_configured_trigger_maps_to_a_real_profile() -> None:
    store = ProfileStore.from_config(CFG)
    triggers = {t["id"]: t["profile"] for t in store.snapshot()["triggers"]}
    assert triggers == {"leave_home": "commute", "arrive_home": "home",
                        "start_focus": "focus", "go_to_sleep": "sleep"}  # fmt: skip
    for tid, pid in triggers.items():
        store.simulate(tid)
        assert store.active == pid and store.context()["trigger"] == tid


def test_choosing_a_profile_by_hand_replaces_a_simulated_context() -> None:
    store = ProfileStore.from_config(CFG)
    store.simulate("go_to_sleep")
    store.set_active("focus")
    assert store.active == "focus" and store.context()["mode"] == "manual"
    store.simulate("leave_home")
    store.update({"active": "home"})  # the older update path is also a manual choice
    assert store.active == "home" and not store.context()["simulated"]


def test_returning_to_manual_keeps_the_profile_and_drops_the_simulation() -> None:
    store = ProfileStore.from_config(CFG)
    store.simulate("leave_home")
    view = store.return_to_manual()
    assert store.active == "commute" and view["context"]["mode"] == "manual"
    assert view["context"]["trigger"] is None


def test_an_unknown_trigger_is_rejected_and_changes_nothing() -> None:
    store = ProfileStore.from_config(CFG)
    before = (store.active, store.version, store.context())
    with pytest.raises(ProfileError, match="unknown simulated trigger"):
        store.simulate("teleport")
    assert (store.active, store.version, store.context()) == before


def test_the_simulation_is_not_remembered_across_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    first = ProfileStore.from_config(CFG, path)
    first.simulate("go_to_sleep")
    again = ProfileStore.from_config(CFG, path)
    assert again.active == "sleep"  # the profile is saved...
    assert again.context()["mode"] == "manual"  # ...but a restart never claims it was simulated


def test_nothing_changes_the_profile_by_itself() -> None:
    """No automatic switching: only an explicit call changes the profile or the version."""
    store = ProfileStore.from_config(CFG)
    before = (store.active, store.version)
    for _ in range(5):
        run([A.DOORBELL], store)  # detections never switch profiles
    assert (store.active, store.version) == before


def test_no_location_or_time_detection_exists_in_the_code() -> None:
    banned = ("geolocation", "watchPosition", "getCurrentPosition", "geofence", "DeviceMotion")
    files = [*(ROOT / "src").rglob("*.py"), *(ROOT / "src").rglob("*.html")]
    hits = [(f.name, w) for f in files for w in banned if w.lower() in f.read_text().lower()]
    assert hits == []


# ---- config ----
def test_a_trigger_for_an_unknown_profile_fails_with_a_clear_message() -> None:
    raw = copy.deepcopy(RAW)
    raw["profiles"]["triggers"]["leave_home"]["profile"] = "holiday"
    with pytest.raises(ConfigError, match="holiday"):
        config_from_dict(raw)


def test_a_config_without_triggers_gets_the_four_defaults() -> None:
    raw = copy.deepcopy(RAW)
    del raw["profiles"]["triggers"]
    store = ProfileStore.from_config(config_from_dict(raw))
    assert [t["id"] for t in store.snapshot()["triggers"]] == [
        "leave_home", "arrive_home", "start_focus", "go_to_sleep"]  # fmt: skip


# ---- subsequent detections use the new profile ----
def test_detections_after_a_simulated_switch_use_the_new_profile() -> None:
    store = ProfileStore.from_config(CFG)
    alerts, rows = run([A.DOORBELL], store)  # home
    assert [a["line1"] for a in alerts] == ["DOORBELL"] and rows[0]["profile"] == "home"
    store.simulate("go_to_sleep")
    assert run([A.DOORBELL], store) == ([], [])  # sleep: off
    store.simulate("start_focus")
    alerts, rows = run([A.DOORBELL], store)  # focus: later
    assert alerts == [] and {(r["level"], r["profile"]) for r in rows} == {("later", "focus")}
    store.return_to_manual()  # still focus, now manual
    assert {r["profile"] for r in run([A.DOORBELL], store)[1]} == {"focus"}


# ---- API and phone page ----
@pytest.fixture
def api():
    store, hub, log = ProfileStore.from_config(CFG), Hub(), EventLog()
    with TestClient(create_app(log, hub, profile=store, stats_interval_s=60)) as client:
        yield client, store, hub, log


def test_context_endpoints_switch_broadcast_and_stay_consistent(api) -> None:
    c, store, *_ = api
    with c.websocket_connect("/ws") as ws:
        ws.receive_json()
        snap = c.post("/api/context/simulate", json={"trigger": "leave_home"}).json()
        assert snap["active"] == "commute" and snap["context"]["simulated"] is True
        msg = ws.receive_json()
        assert msg["type"] == "profile" and msg["active"] == "commute"
        assert msg["context"]["trigger_label"] == "Leaving home"
        assert c.get("/api/profiles").json()["context"] == snap["context"]
        snap = c.post("/api/context/manual").json()
        assert snap["active"] == "commute" and snap["context"]["mode"] == "manual"
        assert ws.receive_json()["context"]["mode"] == "manual"
        c.post("/api/context/simulate", json={"trigger": "go_to_sleep"})
        ws.receive_json()
        snap = c.post("/api/profiles/active", json={"name": "home"}).json()  # a manual choice
        assert snap["active"] == "home" and snap["context"]["mode"] == "manual"
    assert store.active == "home"


def test_bad_context_requests_are_rejected(api) -> None:
    c, store, *_ = api
    before = store.snapshot()
    r = c.post("/api/context/simulate", json={"trigger": "teleport"})
    assert r.status_code == 422 and "unknown simulated trigger" in r.json()["detail"]
    assert c.post("/api/context/simulate", json={}).status_code == 422
    assert store.snapshot() == before
    bare = TestClient(create_app(EventLog(), Hub(), stats_interval_s=60))
    assert bare.post("/api/context/manual").status_code == 503


def test_the_old_profile_endpoints_ignore_context_and_still_work(api) -> None:
    c, store, *_ = api
    assert set(c.get("/api/profile").json()) == {"enabled", "sensitivity"}
    assert c.post("/api/profile", json={"sensitivity": 1.2}).status_code == 200
    assert store.context()["mode"] == "manual"


def test_the_phone_page_labels_the_trigger_as_simulated_and_shows_the_active_profile(api) -> None:
    c, *_ = api
    page = c.get("/phone").text
    for needle in (
        "Simulated context (demo only)",
        "Nothing here detects where you are or what time it is",
        "Back to manual",
        'id="profilePill"',
        "/api/context/simulate",
        "/api/context/manual",
        "(simulated)",
        "not by real detection",
    ):
        assert needle in page, needle
    for kept in ("/api/profiles/active", 'id="profiles"', "Always interrupts", "navigator.vibrate"):
        assert kept in page, kept
    assert not re.search(r"geolocation|watchPosition|getCurrentPosition", page)


# ---- the headline demo: the same doorbell, three outcomes ----
def test_focus_saves_the_doorbell_for_later_with_no_alert_and_the_later_api_returns_it() -> None:
    alerts, rows, store, log = doorbell_in("focus")
    assert alerts == []  # nothing on the lens
    assert rows and {(r["alert_class"], r["level"], r["profile"]) for r in rows} == {
        ("doorbell", "later", "focus")}  # fmt: skip
    assert all(r["confirmed"] == 1 and r["t_display"] is None for r in rows)  # never displayed
    with TestClient(create_app(log, Hub(), profile=store, stats_interval_s=60)) as client:
        later = client.get("/api/events", params={"level": "later"}).json()
    assert [r["id"] for r in later] == [r["id"] for r in rows]
    assert later[0]["profile"] == "focus" and later[0]["alert_class"] == "doorbell"


def test_home_interrupts_with_a_tier_1_blinking_alert_that_vibrates_the_phone() -> None:
    alerts, rows, *_ = doorbell_in("home")
    assert len(alerts) == 1
    msg = alerts[0]
    assert (msg["line1"], msg["line2"], msg["tier"], msg["blink"]) == (
        "DOORBELL",
        "NEARBY",
        1,
        True,
    )
    assert [(r["level"], r["profile"], r["alert_class"]) for r in rows] == [
        ("interrupt", "home", "doorbell")]  # fmt: skip
    # The phone vibrates for tier 1 only. The existing page logic is unchanged.
    page = (ROOT / "src/sentinel/dashboard/static/phone.html").read_text()
    assert "Number(m.tier) === 1" in page and "navigator.vibrate" in page


def test_sleep_suppresses_the_doorbell_completely() -> None:
    alerts, rows, store, log = doorbell_in("sleep")
    assert alerts == [] and rows == [] and log.count() == 0
    assert store.level_for(A.DOORBELL).value == "off"
    with TestClient(create_app(log, Hub(), profile=store, stats_interval_s=60)) as client:
        assert client.get("/api/events", params={"level": "later"}).json() == []


def test_the_smoke_or_fire_alarm_still_interrupts_in_sleep() -> None:
    store = ProfileStore.from_config(CFG)
    store.set_active("sleep")
    alerts, rows = run([A.FIRE_ALARM], store)
    assert [(m["line1"], m["tier"], m["blink"]) for m in alerts] == [("FIRE ALARM", 1, True)]
    assert [(r["level"], r["profile"]) for r in rows] == [("interrupt", "sleep")]
    alerts, rows = run([A.DOORBELL, A.FIRE_ALARM], store)  # together: the doorbell stays suppressed
    assert [m["line1"] for m in alerts] == ["FIRE ALARM"]
    assert {r["alert_class"] for r in rows} == {"fire_alarm"}


def test_the_three_outcomes_side_by_side_come_from_the_same_injected_sound() -> None:
    outcome = {}
    for name in ("focus", "home", "sleep"):
        alerts, rows, *_ = doorbell_in(name)
        outcome[name] = (
            "alert" if alerts else "nothing on the lens",
            sorted({r["level"] for r in rows}) or ["no event"],
        )
    assert outcome == {
        "focus": ("nothing on the lens", ["later"]),
        "home": ("alert", ["interrupt"]),
        "sleep": ("nothing on the lens", ["no event"]),
    }


def test_the_doorbell_demo_makes_no_claim_about_real_recognition() -> None:
    """The doorbell in these tests is a scripted classifier result. Keep that explicit."""
    src = Path(__file__).read_text()
    assert "INJECTED classifier result" in src
    assert (
        json.dumps(RAW["classifier"]["classes"]["doorbell"]["names"]) == '["Doorbell", "Ding-dong"]'
    )
