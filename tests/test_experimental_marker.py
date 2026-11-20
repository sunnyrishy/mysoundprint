"""Per-event experimental provenance (docs/DECISIONS.md D66).

The marker comes from the execution config (an experimental switch was on for the run) and from
nothing else: not the sound, not the profile. All audio here is a synthetic scripted fixture."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.classify.yamnet import ClassifierStage, resolve_class_indices
from sentinel.config import load_config
from sentinel.dashboard.server import Hub, create_app
from sentinel.eventlog import COLUMNS, UPDATABLE, EventLog
from sentinel.pipeline import Pipeline
from sentinel.profile import ProfileStore
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, AlertClass, DisplayCaps

CFG = load_config()
A = AlertClass
NAMES = [f"class {i}" for i in range(521)]
for _i, _n in enumerate(
    sorted({n for r in CFG.classifier.catalog().values() for n in r.names}), 40
):
    NAMES[_i] = _n
IDX = resolve_class_indices(CFG.classifier, NAMES)


class Scripted:
    window_len = 8000

    def __init__(self, scores: dict[int, float]) -> None:
        self.class_indices, self._s, self._n = IDX, scores, 0

    def infer(self, window):
        out = np.zeros(521, dtype=np.float32)
        if self._n >= 18:  # roughly when the alarm in alarm_scene is under the window
            for i, v in self._s.items():
                out[i] = v
        self._n += 1
        return out


class Lens:
    def capabilities(self):
        return DisplayCaps(supports_blink=True)

    def show(self, a): ...
    def clear(self, event_id=None): ...
    def close(self): ...


def scene() -> np.ndarray:
    sig = np.zeros(20 * SAMPLE_RATE, dtype=np.float32)
    a = synth.tonal_alarm(1900, 70, 6)
    sig[5 * SAMPLE_RATE : 5 * SAMPLE_RATE + len(a)] = a
    return synth.mix(sig, synth.noise("pink", 20.0, seed=3), 10).audio


def run(experimental: bool, scores: dict[int, float] | None = None, classifier: bool = True):
    cfg = copy.deepcopy(CFG)
    cfg.experimental.unverified_gate_only = experimental
    log = EventLog()
    stage = ClassifierStage(Scripted(scores or {}), cfg.classifier) if classifier else None
    Pipeline(cfg, AudioFileSource(scene(), t0=0.0), Lens(), eventlog=log, classifier=stage,
             source_label="synthetic").run_sync()  # fmt: skip
    return log


# ---- schema and migration ----
def test_the_column_exists_is_insert_only_and_defaults_to_false() -> None:
    assert COLUMNS[-1] == "experimental" and "experimental" not in UPDATABLE
    log = EventLog()
    log.insert(id="e_000001", alert_class="doorbell", stage="classifier", score=0.5,
               confirmed=True, source="synthetic", config_hash="h")  # fmt: skip
    assert log.get("e_000001")["experimental"] is False
    with pytest.raises(ValueError, match="cannot update"):
        log.update("e_000001", experimental=True)


def test_an_explicit_true_is_stored_and_read_back_as_a_boolean(tmp_path: Path) -> None:
    path = tmp_path / "e.sqlite"
    log = EventLog(path)
    for i, flag in enumerate([True, False, None, 1, 0], start=1):
        log.insert(id=f"e_{i:06d}", alert_class="doorbell", stage="classifier", score=0.5,
                   confirmed=True, source="synthetic", config_hash="h",
                   experimental=flag)  # fmt: skip
    log.close()
    rows = {r["id"]: r["experimental"] for r in EventLog(path).list()}
    assert rows == {
        "e_000001": True, "e_000002": False, "e_000003": False, "e_000004": True,
        "e_000005": False,
    }  # fmt: skip
    assert all(type(v) is bool for v in rows.values())


MILESTONE_C_SCHEMA = """
CREATE TABLE events (
  id TEXT PRIMARY KEY, ts_wall TEXT NOT NULL, alert_class TEXT NOT NULL, stage TEXT NOT NULL,
  score REAL NOT NULL, confirmed INTEGER NOT NULL, cancel_reason TEXT, t_onset REAL, t_detect REAL,
  t_display REAL, processing_ms REAL, alarm_to_display_ms REAL,
  acknowledged INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, config_hash TEXT NOT NULL,
  level TEXT, profile TEXT
);
"""


def old_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(MILESTONE_C_SCHEMA)
    conn.execute(
        "INSERT INTO events (id, ts_wall, alert_class, stage, score, confirmed, source, "
        "config_hash, level, profile) VALUES ('e_000004', '2026-11-19T10:00:00.000+00:00', "
        "'reversing_alarm', 'gate', 0.8, 1, 'synthetic', 'oldhash', 'interrupt', 'home')"
    )
    conn.commit()
    conn.close()


def test_an_existing_database_migrates_and_old_rows_are_not_experimental(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    old_database(path)
    log = EventLog(path)
    old = log.get("e_000004")
    assert old["experimental"] is False  # provenance unknown, so treated as a normal event
    assert (old["level"], old["profile"], old["config_hash"]) == ("interrupt", "home", "oldhash")
    assert log.count() == 1 and log.next_id() == "e_000005"
    log.insert(id="e_000005", alert_class="doorbell", stage="classifier", score=0.7,
               confirmed=True, source="live", config_hash="h", experimental=True)  # fmt: skip
    log.close()
    again = EventLog(path)  # a second open must not add the column again
    assert [r["experimental"] for r in again.list()] == [True, False]
    assert again.stats()["events_total"] == 2
    assert again.to_csv().splitlines()[0].split(",")[-1] == "experimental"


def test_migration_is_safe_on_a_database_that_already_has_the_column(tmp_path: Path) -> None:
    path = tmp_path / "e.sqlite"
    EventLog(path).close()
    EventLog(path).close()
    cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(events)")]
    assert cols.count("experimental") == 1


# ---- events from real pipeline runs ----
def test_normal_runs_never_mark_events_experimental() -> None:
    reversing = IDX[A.REVERSING_ALARM][0]
    confirmed = run(False, {reversing: 0.9}).list()
    assert confirmed and all(r["experimental"] is False for r in confirmed)
    other = run(False, {IDX[A.BABY_CRY][0]: 0.8}).list()  # contradiction plus a baby cry event
    assert other and all(r["experimental"] is False for r in other)
    assert run(False, classifier=False).list() == []  # no classifier: no BACKUP ALARM, no event


def test_a_gate_only_backup_alarm_is_marked_experimental() -> None:
    gate_only = run(True, classifier=False).list()
    assert [r["alert_class"] for r in gate_only] == ["reversing_alarm"]
    assert all(r["experimental"] is True for r in gate_only)


def test_a_normal_classifier_event_in_an_experimental_run_is_not_marked() -> None:
    """The switch is on, but the baby cry came from the classifier and normal profile policy."""
    rows = run(True, {IDX[A.BABY_CRY][0]: 0.8}).list()
    by_class = {r["alert_class"]: r["experimental"] for r in rows}
    assert by_class["baby_cry"] is False
    assert by_class["reversing_alarm"] is True  # the gate-only alert in the same run


def test_a_classifier_alarm_without_a_gate_hit_is_normal_even_with_the_switch_on() -> None:
    cfg = copy.deepcopy(CFG)
    cfg.experimental.unverified_gate_only = True
    cfg.gate.enabled = False  # no gate path exists, so nothing can be gate-only
    log = EventLog()
    stage = ClassifierStage(Scripted({IDX[A.REVERSING_ALARM][0]: 0.9}), cfg.classifier)
    Pipeline(cfg, AudioFileSource(scene(), t0=0.0), Lens(), eventlog=log, classifier=stage,
             source_label="synthetic").run_sync()  # fmt: skip
    rows = log.list()
    assert rows and {r["stage"] for r in rows} == {"classifier"}
    assert all(r["experimental"] is False for r in rows)


def test_a_classifier_confirmation_does_not_launder_a_gate_only_event() -> None:
    """The alert was first shown on the gate alone, so the event depended on experimental
    behavior even after the classifier agreed."""
    rows = run(True, {IDX[A.REVERSING_ALARM][0]: 0.9}).list()
    assert [r["alert_class"] for r in rows] == ["reversing_alarm"]
    assert rows[0]["stage"] in ("gate", "both") and rows[0]["experimental"] is True


def test_a_later_level_event_in_an_experimental_run_is_normal() -> None:
    cfg = copy.deepcopy(CFG)
    cfg.experimental.unverified_gate_only = True
    store = ProfileStore.from_config(cfg)
    store.set_active("focus")
    log = EventLog()
    stage = ClassifierStage(Scripted({IDX[A.DOORBELL][0]: 0.9}), cfg.classifier)
    Pipeline(cfg, AudioFileSource(scene(), t0=0.0), Lens(), eventlog=log, classifier=stage,
             profile=store, source_label="synthetic").run_sync()  # fmt: skip
    rows = {r["alert_class"]: r for r in log.list()}
    assert rows["doorbell"]["level"] == "later" and rows["doorbell"]["experimental"] is False


def test_the_marker_ignores_the_sound_and_the_profile() -> None:
    for profile in ("focus", "home", "sleep", "commute"):
        cfg = copy.deepcopy(CFG)
        store = ProfileStore.from_config(cfg)
        store.set_active(profile)
        log = EventLog()
        stage = ClassifierStage(Scripted({IDX[A.DOORBELL][0]: 0.9}), cfg.classifier)
        Pipeline(cfg, AudioFileSource(scene(), t0=0.0), Lens(), eventlog=log, classifier=stage,
                 profile=store, source_label="synthetic").run_sync()  # fmt: skip
        assert all(r["experimental"] is False for r in log.list()), profile


def test_the_marker_does_not_change_what_the_pipeline_decides() -> None:
    """Same input, same decisions. Only the experimental run differs, by its own switch."""
    reversing = IDX[A.REVERSING_ALARM][0]
    a = [
        (r["alert_class"], r["stage"], r["level"], r["profile"])
        for r in run(False, {reversing: 0.9}).list()
    ]
    b = [
        (r["alert_class"], r["stage"], r["level"], r["profile"])
        for r in run(False, {reversing: 0.9}).list()
    ]
    assert a == b and a


def test_pipeline_event_messages_carry_the_marker() -> None:
    for flag in (False, True):
        cfg = copy.deepcopy(CFG)
        cfg.experimental.unverified_gate_only = flag
        seen: list[dict] = []
        pipe = Pipeline(cfg, AudioFileSource(scene(), t0=0.0), Lens(), eventlog=EventLog(),
                        source_label="synthetic")  # fmt: skip
        pipe.add_listener(seen.append)
        pipe.run_sync()
        msgs = [m["event"] for m in seen if m["type"] == "event" and "alert_class" in m["event"]]
        assert bool(msgs) is flag  # without the switch and without a classifier there is no event
        assert all(m["experimental"] is True for m in msgs)  # each one is a gate-only alert

    # a normal classifier event on the same bus is not marked, even with the switch on
    cfg = copy.deepcopy(CFG)
    cfg.experimental.unverified_gate_only = True
    seen = []
    stage = ClassifierStage(Scripted({IDX[A.BABY_CRY][0]: 0.8}), cfg.classifier)
    pipe = Pipeline(cfg, AudioFileSource(scene(), t0=0.0), Lens(), eventlog=EventLog(),
                    classifier=stage, source_label="synthetic")  # fmt: skip
    pipe.add_listener(seen.append)
    pipe.run_sync()
    msgs = {m["event"]["alert_class"]: m["event"]["experimental"] for m in seen
            if m["type"] == "event" and "alert_class" in m["event"]}  # fmt: skip
    assert msgs["baby_cry"] is False and msgs["reversing_alarm"] is True


# ---- API ----
def test_the_events_api_exposes_the_marker_as_a_boolean() -> None:
    log = EventLog()
    for i, flag in enumerate([False, True], start=1):
        log.insert(id=f"e_{i:06d}", alert_class="doorbell", stage="classifier", score=0.5,
                   confirmed=True, source="synthetic", config_hash="h", level="later",
                   experimental=flag)  # fmt: skip
    with TestClient(create_app(log, Hub(), stats_interval_s=60)) as c:
        rows = c.get("/api/events").json()
        assert [(r["id"], r["experimental"]) for r in rows] == [
            ("e_000002", True),
            ("e_000001", False),
        ]
        one = c.get("/api/events", params={"level": "later"}).json()
        assert all(isinstance(r["experimental"], bool) for r in one)
        csv_lines = c.get("/api/events.csv").text.splitlines()
    assert csv_lines[0].split(",")[-1] == "experimental"
    assert [ln.split(",")[-1] for ln in csv_lines[1:]] == [
        "0",
        "1",
    ]  # oldest first, 0 or 1 like the other flags
