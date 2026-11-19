"""Milestone A: sound catalog, four levels, four named profiles, the protected rule, event log
migration and the profile API. All audio here is synthetic or scripted. Nothing in this file says
anything about how well real sounds are recognised."""

from __future__ import annotations

import copy
import itertools
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.classify.yamnet import ClassifierStage, load_class_map, resolve_class_indices
from sentinel.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    config_from_dict,
    load_config,
    resolve_path,
)
from sentinel.dashboard.server import Hub, create_app
from sentinel.eventlog import COLUMNS, EventLog
from sentinel.pipeline import Pipeline
from sentinel.policy import ClearAlert, Policy, ShowAlert, effective_level
from sentinel.profile import ProfileError, ProfileStore
from sentinel.types import SAMPLE_RATE, Alert, AlertClass, Detection, DisplayCaps, Level

CFG = load_config()
CATALOG = CFG.classifier.catalog()
RAW = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
A = AlertClass

# What each catalog sound listens for. Kept here on purpose: a change to the config that drops a
# mapping must fail a test, not pass silently.
EXPECTED_NAMES = {
    A.REVERSING_ALARM: ["Reversing beeps"],
    A.VEHICLE_HORN: ["Vehicle horn, car horn, honking", "Air horn, truck horn"],
    A.FIRE_ALARM: ["Fire alarm", "Smoke detector, smoke alarm"],
    A.DOORBELL: ["Doorbell", "Ding-dong"],
    A.KNOCK: ["Knock"],
    A.SIREN: ["Siren", "Police car (siren)", "Ambulance (siren)"],
    A.BICYCLE_BELL: ["Bicycle bell"],
    A.BABY_CRY: ["Baby cry, infant cry"],
    A.MICROWAVE: ["Microwave oven"],
    A.ALARM_CLOCK: ["Alarm clock"],
}
FIRST_FAKE_INDEX = 40
FAKE_NAMES = [f"class {i}" for i in range(521)]
for _i, _n in enumerate(sorted({n for v in EXPECTED_NAMES.values() for n in v}), FIRST_FAKE_INDEX):
    FAKE_NAMES[_i] = _n


def det(cls: A, provisional: bool = False, stage: str = "classifier", t: float = 0.0) -> Detection:
    return Detection(cls, stage, 0.8, provisional, t_onset=t - 0.3, t_detect=t)


def policy(**kw) -> Policy:
    ids = itertools.count(1)
    cfg = copy.deepcopy(CFG.policy)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return Policy(cfg, 1.6, lambda: f"e_{next(ids):06d}", catalog=CATALOG)


def kinds(actions) -> list[str]:
    return [type(a).__name__ for a in actions]


def shown(actions) -> list[Alert]:
    return [a.alert for a in actions if isinstance(a, ShowAlert)]


# ---- A1 catalog ----
def test_catalog_covers_every_alert_class_with_the_expected_mappings() -> None:
    assert set(CATALOG) == set(A) == set(EXPECTED_NAMES)
    for cls, names in EXPECTED_NAMES.items():
        assert CATALOG[cls].names == names, cls


def test_every_catalog_name_resolves_in_a_class_map() -> None:
    idx = resolve_class_indices(CFG.classifier, FAKE_NAMES)
    assert all(idx[c] for c in A)


def test_every_catalog_name_resolves_in_the_real_class_map() -> None:
    path = resolve_path(CFG.classifier.class_map_path)
    if not path.exists():
        pytest.skip("run tools/fetch_models.py first")
    idx = resolve_class_indices(CFG.classifier, load_class_map(path))
    assert all(idx[c] for c in A)


def test_labels_fit_the_display_and_never_claim_a_direction() -> None:
    banned = {"BEHIND", "LEFT", "RIGHT", "FRONT", "AHEAD", "ABOVE", "BELOW", "OUTSIDE"}
    for cls, rule in CATALOG.items():
        line1, line2 = rule.labels(cls)
        assert 0 < len(line1) <= 12 and 0 < len(line2) <= 12, cls
        assert not banned & set(line1.split() + line2.split()), cls
    assert len({rule.labels(c)[0] for c, rule in CATALOG.items()}) == len(CATALOG)  # distinct


def test_default_levels_and_priority_cover_the_whole_catalog() -> None:
    assert {c: r.default_level for c, r in CATALOG.items()}[A.FIRE_ALARM] == Level.INTERRUPT
    assert set(CFG.policy.priority) == {c.value for c in A}
    assert CFG.policy.priority[0] == "fire_alarm"
    assert CFG.policy.protected == ["fire_alarm"]


def test_catalog_names_are_not_listed_twice() -> None:
    names = [n for r in CATALOG.values() for n in r.names]
    assert len(names) == len(set(names))


# ---- A2 four levels ----
def test_interrupt_shows_tier_1_and_asks_for_blink() -> None:
    acts = policy().on_detection(det(A.DOORBELL), 0.0, level=Level.INTERRUPT, profile="home")
    assert kinds(acts) == ["CreateEvent", "ShowAlert"]
    alert = shown(acts)[0]
    assert (alert.tier, alert.blink, alert.line1, alert.line2) == (1, True, "DOORBELL", "NEARBY")
    ev = acts[0]
    assert (ev.level, ev.profile, ev.confirmed) == ("interrupt", "home", True)


def test_quiet_shows_tier_2_without_blink_even_when_blink_is_configured() -> None:
    p = policy(blink=True)
    alert = shown(p.on_detection(det(A.KNOCK), 0.0, level=Level.QUIET, profile="focus"))[0]
    assert (alert.tier, alert.blink, alert.line1) == (2, False, "KNOCK")


def test_later_saves_a_confirmed_event_and_shows_nothing() -> None:
    p = policy()
    acts = p.on_detection(det(A.MICROWAVE), 0.0, level=Level.LATER, profile="focus")
    assert kinds(acts) == ["CreateEvent"]
    ev = acts[0]
    assert (ev.level, ev.profile, ev.confirmed, ev.cancel_reason) == ("later", "focus", True, None)
    assert p.displayed_class is None


def test_later_ignores_an_unconfirmed_gate_hit_and_saves_one_event_per_cooldown() -> None:
    p = policy()
    assert p.on_detection(det(A.REVERSING_ALARM, provisional=True, stage="gate"), 0.0,
                          level=Level.LATER) == []  # fmt: skip
    assert kinds(p.on_detection(det(A.REVERSING_ALARM), 0.2, level=Level.LATER)) == ["CreateEvent"]
    assert p.on_detection(det(A.REVERSING_ALARM), 1.0, level=Level.LATER) == []  # cooldown
    assert kinds(p.on_detection(det(A.REVERSING_ALARM), 6.0, level=Level.LATER)) == ["CreateEvent"]


def test_off_drops_the_detection_without_an_event() -> None:
    assert policy().on_detection(det(A.DOORBELL), 0.0, level=Level.OFF, profile="sleep") == []


def test_no_level_uses_the_catalog_default() -> None:
    assert shown(policy().on_detection(det(A.KNOCK), 0.0))[0].tier == 2  # knock defaults to quiet
    acts = policy().on_detection(det(A.MICROWAVE), 0.0)  # microwave defaults to later
    assert kinds(acts) == ["CreateEvent"] and acts[0].level == "later"


def test_policy_without_a_catalog_keeps_the_original_behaviour() -> None:
    ids = itertools.count(1)
    bare = Policy(CFG.policy, 1.6, lambda: f"e_{next(ids):06d}")
    alert = shown(bare.on_detection(det(A.VEHICLE_HORN), 0.0))[0]
    assert (alert.line1, alert.line2, alert.tier, alert.blink) == (
        "VEHICLE HORN",
        "NEARBY",
        1,
        True,
    )


def test_interrupt_outranks_quiet_on_the_display() -> None:
    p = policy()
    p.on_detection(det(A.KNOCK), 0.0, level=Level.QUIET)
    acts = p.on_detection(det(A.VEHICLE_HORN), 0.5, level=Level.INTERRUPT)
    assert kinds(acts) == ["UpdateEvent", "CreateEvent", "ShowAlert"]  # knock superseded
    assert shown(acts)[0].line1 == "VEHICLE HORN" and p.displayed_class == A.VEHICLE_HORN


def test_quiet_never_replaces_an_interrupt_alert() -> None:
    p = policy()
    p.on_detection(det(A.DOORBELL), 0.0, level=Level.INTERRUPT)
    acts = p.on_detection(det(A.KNOCK), 0.5, level=Level.QUIET)
    assert kinds(acts) == ["CreateEvent"] and acts[0].cancel_reason == "superseded"
    assert acts[0].level == "quiet" and p.displayed_class == A.DOORBELL


def test_existing_confirmation_ttl_and_cooldown_still_apply_with_levels() -> None:
    p = policy()
    p.on_detection(det(A.REVERSING_ALARM, True, "gate"), 0.0, level=Level.INTERRUPT)
    assert kinds(p.tick(2.0)) == ["ClearAlert", "UpdateEvent"]  # unconfirmed: timeout
    assert p.on_detection(det(A.REVERSING_ALARM, True, "gate"), 3.0, level=Level.INTERRUPT) == []
    p2 = policy()
    p2.on_detection(det(A.DOORBELL), 0.0, level=Level.QUIET)
    assert isinstance(p2.tick(3.1)[0], ClearAlert)  # ttl clear
    assert p2.on_detection(det(A.DOORBELL), 4.0, level=Level.QUIET) == []  # cooldown


# ---- A3 named profiles ----
def test_four_named_profiles_each_define_every_sound() -> None:
    defs = RAW["profiles"]["definitions"]
    assert list(defs) == ["focus", "home", "sleep", "commute"]
    for name, d in defs.items():
        assert set(d["levels"]) == {c.value for c in A}, name  # explicit, not left to defaults
    store = ProfileStore.from_config(CFG)
    assert store.names() == ["focus", "home", "sleep", "commute"]
    assert store.active == "home"


def test_off_in_yaml_means_the_level_off_not_boolean_false() -> None:
    assert CFG.profiles.definitions["sleep"].levels["doorbell"] == Level.OFF
    assert all(
        isinstance(lv, Level) for d in CFG.profiles.definitions.values() for lv in d.levels.values()
    )


def test_default_level_resolution_matches_the_yaml_and_falls_back_to_the_catalog() -> None:
    store = ProfileStore.from_config(CFG)
    for pid, d in RAW["profiles"]["definitions"].items():
        store.set_active(pid)
        for sound, level in d["levels"].items():
            want = Level.OFF if level is False else Level(level)
            assert store.level_for(A(sound)) == want, (pid, sound)
    raw = copy.deepcopy(RAW)
    del raw["profiles"]["definitions"]["focus"]["levels"]["knock"]
    store2 = ProfileStore.from_config(config_from_dict(raw))
    store2.set_active("focus")
    assert store2.level_for(A.KNOCK) == CATALOG[A.KNOCK].default_level == Level.QUIET


def test_a_config_without_profiles_still_loads_with_four_profiles() -> None:
    raw = copy.deepcopy(RAW)
    del raw["profiles"]
    cfg = config_from_dict(raw)
    store = ProfileStore.from_config(cfg)
    assert store.names() == ["focus", "home", "sleep", "commute"]
    assert store.level_for(A.MICROWAVE) == Level.LATER  # catalog default


def test_switching_profiles_changes_the_level_and_the_sensitivity() -> None:
    raw = copy.deepcopy(RAW)
    raw["profiles"]["definitions"]["sleep"]["sensitivity"] = 0.8
    store = ProfileStore.from_config(config_from_dict(raw))
    assert (store.active, store.level_for(A.DOORBELL), store.sensitivity) == (
        "home",
        Level.INTERRUPT,
        1.0,
    )
    store.set_active("focus")
    assert store.level_for(A.DOORBELL) == Level.LATER
    store.set_active("sleep")
    assert (store.level_for(A.DOORBELL), store.sensitivity) == (Level.OFF, 0.8)
    with pytest.raises(ProfileError, match="unknown profile"):
        store.set_active("holiday")
    assert store.active == "sleep"


def test_editing_a_named_profile_does_not_switch_to_it() -> None:
    store = ProfileStore.from_config(CFG)
    store.update_profile("sleep", {"levels": {"knock": "later"}, "sensitivity": 1.2})
    assert store.active == "home"
    store.set_active("sleep")
    assert (store.level_for(A.KNOCK), store.sensitivity) == (Level.LATER, 1.2)


@pytest.mark.parametrize("bad", [
    {"levels": {"doorbell": "loud"}}, {"levels": {"klaxon": "off"}}, {"sensitivity": 3},
    {"colour": 1}, {"levels": {"doorbell": False}},
])  # fmt: skip
def test_invalid_profile_edits_are_rejected_and_change_nothing(bad) -> None:
    store = ProfileStore.from_config(CFG)
    before = store.snapshot()
    with pytest.raises(ProfileError):
        store.update_profile("home", bad)
    assert store.snapshot() == before


# ---- A4 the policy at detection time, end to end ----
class Rec:
    def __init__(self) -> None:
        self.shown: list[Alert] = []

    def capabilities(self):
        return DisplayCaps(supports_blink=True)

    def show(self, a):
        self.shown.append(a)

    def clear(self, event_id=None): ...
    def close(self): ...


class Scripted:
    """A classifier stand-in that reports the given sounds as strongly present for N hops."""

    window_len = 8000

    def __init__(self, sounds: list[A], hops: int = 8) -> None:
        self.class_indices = resolve_class_indices(CFG.classifier, FAKE_NAMES)
        self._sounds, self._left = sounds, hops

    def infer(self, window):
        out = np.zeros(521, dtype=np.float32)
        if self._left > 0:
            for s in self._sounds:
                out[self.class_indices[s][0]] = 0.9
        self._left -= 1
        return out


def run(sounds: list[A], store: ProfileStore | None, seconds: int = 5):
    log, display = EventLog(), Rec()
    stage = ClassifierStage(Scripted(sounds), CFG.classifier)
    audio = np.zeros(seconds * SAMPLE_RATE, dtype=np.float32)
    Pipeline(CFG, AudioFileSource(audio, t0=0.0), display, eventlog=log, classifier=stage,
             profile=store, source_label="synthetic").run_sync()  # fmt: skip
    return display.shown, log.list()


def test_same_sound_three_profiles_three_outcomes() -> None:
    """The headline demo, with a scripted classifier: one doorbell, three experiences."""
    results = {}
    for name in ("focus", "home", "sleep"):
        store = ProfileStore.from_config(CFG)
        store.set_active(name)
        results[name] = run([A.DOORBELL], store)
    focus_shown, focus_rows = results["focus"]
    assert focus_shown == [] and [(r["level"], r["profile"]) for r in focus_rows] == [
        ("later", "focus")
    ]
    home_shown, home_rows = results["home"]
    assert [a.line1 for a in home_shown] == ["DOORBELL"] and home_shown[0].tier == 1
    assert [(r["level"], r["profile"], r["alert_class"]) for r in home_rows] == [
        ("interrupt", "home", "doorbell")
    ]
    assert results["sleep"] == ([], [])  # off: no alert and no event


def test_the_profile_is_read_at_detection_time_so_a_switch_applies_immediately() -> None:
    store = ProfileStore.from_config(CFG)
    store.set_active("focus")
    _, rows = run([A.KNOCK], store)
    assert rows[0]["level"] == "later"
    store.set_active("home")
    shown_alerts, rows = run([A.KNOCK], store)
    assert shown_alerts[0].line1 == "KNOCK" and rows[0]["level"] == "interrupt"


def test_event_log_records_level_and_profile_without_a_store_too() -> None:
    _, rows = run([A.KNOCK], None)  # no profile: catalog default level, no profile name
    assert (rows[0]["level"], rows[0]["profile"]) == ("quiet", None)


# ---- the protected rule ----
@pytest.mark.parametrize("level", [Level.OFF, Level.LATER, Level.QUIET, Level.INTERRUPT])
def test_fire_alarm_always_interrupts_whatever_the_profile_asks(level: Level) -> None:
    acts = policy().on_detection(det(A.FIRE_ALARM), 0.0, level=level, profile="sleep")
    alert = shown(acts)[0]
    assert (alert.tier, alert.blink, alert.line1) == (1, True, "FIRE ALARM")
    assert acts[0].level == "interrupt"


def test_the_protected_rule_is_a_visible_pure_function() -> None:
    prot = [A.FIRE_ALARM]
    assert effective_level(Level.OFF, A.FIRE_ALARM, prot) == Level.INTERRUPT
    assert effective_level(Level.OFF, A.DOORBELL, prot) == Level.OFF


def test_the_sleep_profile_cannot_suppress_smoke_or_fire_even_if_the_wearer_set_it_off() -> None:
    store = ProfileStore.from_config(CFG)
    store.set_active("sleep")
    store.update({"levels": {"fire_alarm": "off"}})  # the wearer's own choice is stored as given
    assert store.level_for(A.FIRE_ALARM) == Level.OFF
    assert store.snapshot()["effective"]["fire_alarm"] == "interrupt"  # and shown as overridden
    assert store.snapshot()["protected"] == ["fire_alarm"]
    shown_alerts, rows = run([A.FIRE_ALARM], store)
    assert [a.line1 for a in shown_alerts] == ["FIRE ALARM"]
    assert (rows[0]["level"], rows[0]["profile"]) == ("interrupt", "sleep")
    assert store.level_for(A.FIRE_ALARM) == Level.OFF  # the preference was not rewritten


def test_the_shipped_sleep_profile_lets_the_smoke_detector_class_through() -> None:
    assert "Smoke detector, smoke alarm" in CATALOG[A.FIRE_ALARM].names  # smoke maps to fire_alarm
    store = ProfileStore.from_config(CFG)
    store.set_active("sleep")
    shown_alerts, _ = run([A.FIRE_ALARM], store)
    assert [a.line1 for a in shown_alerts] == ["FIRE ALARM"]
    assert run([A.DOORBELL], store) == ([], [])  # while an ordinary sound stays suppressed


def test_protected_list_must_name_real_sounds() -> None:
    raw = copy.deepcopy(RAW)
    raw["policy"]["protected"] = ["fire_alarm", "klaxon"]
    with pytest.raises(ConfigError, match="klaxon"):
        config_from_dict(raw)


# ---- sensitivity per profile ----
def test_sensitivity_follows_the_active_profile() -> None:
    raw = copy.deepcopy(RAW)
    raw["profiles"]["definitions"]["focus"]["sensitivity"] = 1.5
    raw["profiles"]["definitions"]["sleep"]["sensitivity"] = 0.6
    store = ProfileStore.from_config(config_from_dict(raw))
    stage = ClassifierStage(Scripted([]), CFG.classifier)
    stage.profile = store
    weak = {c: 0.0 for c in A} | {A.KNOCK: 0.22}  # below the 0.30 threshold at sensitivity 1.0

    def fires() -> bool:
        stage._history[A.KNOCK].clear()
        stage.update(weak, 1.0, 1.0)
        return bool(stage.update(weak, 1.25, 1.25))

    assert not fires()  # home, 1.0
    store.set_active("focus")
    assert fires()  # 1.5 lowers the threshold to 0.20
    store.set_active("sleep")
    assert not fires()  # 0.6 raises it to 0.50


# ---- backward compatibility of the profile API ----
def test_original_profile_api_shape_and_behaviour_are_kept() -> None:
    s = ProfileStore()
    assert s.get() == {"enabled": {c.value: True for c in A}, "sensitivity": 1.0}
    out = s.update({"enabled": {"vehicle_horn": False}, "sensitivity": 1.2})
    assert out["enabled"]["vehicle_horn"] is False and out["sensitivity"] == 1.2
    assert s.level_for(A.VEHICLE_HORN) == Level.OFF and not s.is_enabled(A.VEHICLE_HORN)
    s.update({"enabled": {"vehicle_horn": True}})
    assert s.level_for(A.VEHICLE_HORN) == Level.INTERRUPT and s.is_enabled(A.VEHICLE_HORN)


def test_turning_a_sound_back_on_restores_its_catalog_default_not_always_interrupt() -> None:
    store = ProfileStore.from_config(CFG)
    store.update({"enabled": {"knock": False}})
    store.update({"enabled": {"knock": True}})
    assert store.level_for(A.KNOCK) == Level.QUIET
    store.update({"levels": {"knock": "later"}})
    store.update({"enabled": {"knock": True}})  # already on: a level that is not off is kept
    assert store.level_for(A.KNOCK) == Level.LATER


def test_an_old_single_profile_file_is_read_as_overrides_to_the_active_profile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"enabled": {"fire_alarm": False}, "sensitivity": 0.8}))
    store = ProfileStore.from_config(CFG, path)
    assert store.active == "home" and store.sensitivity == 0.8
    assert store.level_for(A.FIRE_ALARM) == Level.OFF
    assert store.level_for(A.DOORBELL) == Level.INTERRUPT  # untouched


def test_the_new_file_format_round_trips_with_the_active_profile(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    a = ProfileStore.from_config(CFG, path)
    a.set_active("sleep")
    a.update_profile("focus", {"levels": {"doorbell": "quiet"}})
    b = ProfileStore.from_config(CFG, path)
    assert b.active == "sleep"
    b.set_active("focus")
    assert b.level_for(A.DOORBELL) == Level.QUIET
    path.write_text("{broken")
    assert ProfileStore.from_config(CFG, path).active == "home"  # damaged file: defaults
    path.write_text(json.dumps({"version": 2, "active": "nowhere", "profiles": {}}))
    assert ProfileStore.from_config(CFG, path).active == "home"


# ---- A5 event log ----
OLD_SCHEMA = """CREATE TABLE events (
  id TEXT PRIMARY KEY, ts_wall TEXT NOT NULL, alert_class TEXT NOT NULL, stage TEXT NOT NULL,
  score REAL NOT NULL, confirmed INTEGER NOT NULL, cancel_reason TEXT,
  t_onset REAL, t_detect REAL, t_display REAL, processing_ms REAL, alarm_to_display_ms REAL,
  acknowledged INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, config_hash TEXT NOT NULL
);"""
OLD_ROW = (
    "INSERT INTO events (id, ts_wall, alert_class, stage, score, confirmed, source, config_hash) "
    "VALUES ('e_000007', '2026-11-13T21:00:00.000+00:00', 'reversing_alarm', 'gate', 0.9, 1, "
    "'synthetic', 'oldhash')"
)


def make_old_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(OLD_ROW)
    conn.commit()
    conn.close()


def test_an_old_database_opens_keeps_its_history_and_gains_the_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    make_old_database(path)
    log = EventLog(path)
    cols = [r[1] for r in log._conn.execute("PRAGMA table_info(events)")]
    assert cols == COLUMNS and cols[-2:] == ["level", "profile"]
    old = log.get("e_000007")
    assert (
        old["alert_class"] == "reversing_alarm" and old["level"] is None and old["profile"] is None
    )
    assert log.count() == 1 and log.next_id() == "e_000008"
    log.insert(id="e_000008", alert_class="doorbell", stage="classifier", score=0.7, confirmed=True,
               source="live", config_hash="h", level="later", profile="focus")  # fmt: skip
    log.close()
    again = EventLog(path)  # a second open must not try to add the columns again
    assert again.count() == 2 and again.get("e_000008")["profile"] == "focus"
    assert again.stats()["events_total"] == 2
    assert again.to_csv().splitlines()[0].split(",")[-2:] == ["level", "profile"]


def test_the_level_filter_returns_only_that_level_newest_first() -> None:
    log = EventLog()
    for lv in ["later", "interrupt", "later", None]:
        log.insert(id=log.next_id(), alert_class="doorbell", stage="classifier", score=0.5,
                   confirmed=True, source="synthetic", config_hash="h", level=lv)  # fmt: skip
    assert [r["id"] for r in log.list(level="later")] == ["e_000003", "e_000001"]
    assert len(log.list()) == 4 and log.list(level="quiet") == []


def test_the_event_log_rejects_updates_to_the_new_columns() -> None:
    log = EventLog()
    log.insert(id="e_000001", alert_class="doorbell", stage="classifier", score=0.5, confirmed=True,
               source="live", config_hash="h", level="quiet", profile="home")  # fmt: skip
    with pytest.raises(ValueError, match="cannot update"):
        log.update("e_000001", level="off")


# ---- A6 API and phone page ----
@pytest.fixture
def api():
    store, hub, log = ProfileStore.from_config(CFG), Hub(), EventLog()
    with TestClient(create_app(log, hub, profile=store, stats_interval_s=60)) as client:
        yield client, store, hub, log


def test_profiles_endpoint_lists_profiles_sounds_levels_and_protection(api) -> None:
    c, *_ = api
    snap = c.get("/api/profiles").json()
    assert snap["active"] == "home" and [p["id"] for p in snap["profiles"]] == [
        "focus",
        "home",
        "sleep",
        "commute",
    ]
    assert [s["id"] for s in snap["sounds"]] == [x.value for x in A]
    assert snap["levels"] == ["interrupt", "quiet", "later", "off"]
    assert snap["protected"] == ["fire_alarm"] and snap["effective"]["fire_alarm"] == "interrupt"
    assert {"id", "label1", "label2", "default_level"} <= set(snap["sounds"][0])


def test_switching_and_editing_profiles_over_http_applies_and_broadcasts(api) -> None:
    c, store, *_ = api
    with c.websocket_connect("/ws") as ws:
        ws.receive_json()
        snap = c.post("/api/profiles/active", json={"name": "sleep"}).json()
        assert snap["active"] == "sleep" and store.active == "sleep"
        msg = ws.receive_json()
        assert (
            msg["type"] == "profile"
            and msg["active"] == "sleep"
            and msg["levels"]["doorbell"] == "off"
        )
        snap = c.post("/api/profiles/sleep", json={"levels": {"doorbell": "later"}}).json()
        assert (
            next(p for p in snap["profiles"] if p["id"] == "sleep")["levels"]["doorbell"] == "later"
        )
        assert ws.receive_json()["type"] == "profile"
    assert store.level_for(A.DOORBELL) == Level.LATER


def test_bad_profile_requests_are_rejected_with_a_message_and_change_nothing(api) -> None:
    c, store, *_ = api
    before = store.snapshot()
    for url, body in [("/api/profiles/active", {"name": "holiday"}),
                      ("/api/profiles/home", {"levels": {"doorbell": "loud"}}),
                      ("/api/profiles/home", {"sensitivity": 9}),
                      ("/api/profiles/holiday", {"levels": {"doorbell": "off"}})]:  # fmt: skip
        r = c.post(url, json=body)
        assert r.status_code == 422 and r.json()["detail"], (url, body)
    assert store.snapshot() == before


def test_the_original_profile_endpoints_still_work_on_the_active_profile(api) -> None:
    c, store, *_ = api
    assert set(c.get("/api/profile").json()) == {"enabled", "sensitivity"}
    r = c.post("/api/profile", json={"enabled": {"doorbell": False}, "sensitivity": 1.3})
    assert r.status_code == 200 and r.json()["enabled"]["doorbell"] is False
    assert store.level_for(A.DOORBELL) == Level.OFF and store.sensitivity == 1.3
    assert c.post("/api/profile", json={"sensitivity": 9}).status_code == 422


def test_events_endpoint_can_filter_the_later_digest(api) -> None:
    c, _, _, log = api
    for lv in ("later", "interrupt", "later"):
        log.insert(
            id=log.next_id(), alert_class="knock", stage="classifier", score=0.5, confirmed=True,
            source="synthetic", config_hash="h", level=lv, profile="focus",
        )  # fmt: skip
    rows = c.get("/api/events", params={"level": "later"}).json()
    assert [r["id"] for r in rows] == ["e_000003", "e_000001"] and rows[0]["profile"] == "focus"
    assert len(c.get("/api/events").json()) == 3


def test_phone_page_has_the_profile_and_level_controls_and_keeps_the_old_ones(api) -> None:
    c, *_ = api
    page = c.get("/phone").text
    for needle in (
        "/api/profiles",
        "Always interrupts",
        'id="profiles"',
        'id="sounds"',
        "snap.levels",
    ):
        assert needle in page, needle
    for kept in (
        "navigator.vibrate",
        "/api/test-alert",
        'id="sens"',
        "/api/events/",
        "not a certified safety device",
    ):
        assert kept in page, kept
    assert "Number(m.tier) === 1" in page  # only interrupt alerts vibrate the phone


def test_config_rejects_bad_profiles_and_labels() -> None:
    cases = [
        (("profiles", "active"), "holiday", "holiday"),
        (("profiles", "definitions", "home", "levels", "klaxon"), "off", "klaxon"),
        (("profiles", "definitions", "home", "levels", "doorbell"), "loud", "levels.doorbell"),
        (("profiles", "definitions", "home", "sensitivity"), 9, "sensitivity"),
        (("classifier", "classes", "doorbell", "label1"), "THIS LABEL IS FAR TOO LONG", "label1"),
        (("classifier", "classes", "doorbell", "default_level"), "shout", "default_level"),
    ]
    for path, value, needle in cases:
        raw = copy.deepcopy(RAW)
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        with pytest.raises(ConfigError, match=needle):
            config_from_dict(raw)
