"""Gate candidates need classifier confirmation (docs/DECISIONS.md D60).

The classifier here is a scripted double that returns any of the 521 scores. These tests check the
confirmation logic. They say nothing about how well the real classifier recognises real reversing
alarms: no real reversing alarm recording exists yet. The audio is a synthetic backup alarm, used
only because the tonal gate reliably fires on it."""

from __future__ import annotations

import copy
import logging

import numpy as np
import pytest

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.classify.yamnet import ClassifierObservation, ClassifierStage, resolve_class_indices
from sentinel.config import load_config
from sentinel.eventlog import EventLog
from sentinel.metrics import Metrics
from sentinel.pipeline import Pipeline
from sentinel.profile import ProfileStore
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, Alert, AlertClass, DisplayCaps

CFG = load_config()
A = AlertClass
NAMES = [f"class {i}" for i in range(521)]
_CATALOG_NAMES = sorted({n for r in CFG.classifier.catalog().values() for n in r.names})
for _i, _n in enumerate(_CATALOG_NAMES, 40):
    NAMES[_i] = _n
IDX = resolve_class_indices(CFG.classifier, NAMES)
REVERSING, BABY, DOORBELL = IDX[A.REVERSING_ALARM][0], IDX[A.BABY_CRY][0], IDX[A.DOORBELL][0]
VACUUM, LAUGHTER, BEEP = 200, 201, 202  # sounds that are not in the catalog
ALARM_START_S = 5.0
CONTENT_START_CALL = 18  # classifier call that lines up with the alarm starting at 5 s


class Scripted:
    """Reports the given scores (index to score) from classifier call `start` onwards."""

    window_len = 8000

    def __init__(self, spec: dict[int, float], start: int = CONTENT_START_CALL) -> None:
        self.class_indices = IDX
        self._spec, self._start, self._calls = spec, start, 0

    def infer(self, window):
        out = np.zeros(521, dtype=np.float32)
        if self._calls >= self._start:
            for i, v in self._spec.items():
                out[i] = v
        self._calls += 1
        return out


class Rec:
    def __init__(self) -> None:
        self.shown: list[Alert] = []

    def capabilities(self):
        return DisplayCaps(supports_blink=True)

    def show(self, a):
        self.shown.append(a)

    def clear(self, event_id=None): ...
    def close(self): ...


def alarm_scene(total_s: float = 20.0, alarm: bool = True) -> np.ndarray:
    sig = np.zeros(int(total_s * SAMPLE_RATE), dtype=np.float32)
    if alarm:
        a = synth.tonal_alarm(1900, 70, 6)
        s = int(ALARM_START_S * SAMPLE_RATE)
        sig[s : s + len(a)] = a
    return synth.mix(sig, synth.noise("pink", total_s, seed=3), 10).audio


def run(spec, audio=None, confirm=True, profile="home", start=CONTENT_START_CALL, classifier=True):
    cfg = copy.deepcopy(CFG)
    cfg.experimental.unverified_gate_only = not confirm  # explicit opt-in (D63)
    audio = alarm_scene() if audio is None else audio
    store = ProfileStore.from_config(cfg)
    store.set_active(profile)
    stage = ClassifierStage(Scripted(spec, start), cfg.classifier) if classifier else None
    log, display, metrics = EventLog(), Rec(), Metrics()
    Pipeline(cfg, AudioFileSource(audio, t0=0.0), display, metrics, eventlog=log, classifier=stage,
             profile=store, source_label="synthetic").run_sync()  # fmt: skip
    return [a.line1 for a in display.shown], log.list(), metrics


# ---- a gate hit alone is only a candidate ----
def test_a_gate_candidate_alone_never_shows_backup_alarm() -> None:
    shown, rows, m = run({})  # the classifier reports nothing
    assert shown == [] and rows == []
    assert m.gate_candidates == 1 and m.gate_confirmed == 0


def test_the_old_behaviour_is_still_there_when_confirmation_is_off() -> None:
    shown, rows, m = run({}, confirm=False)
    assert shown == ["BACKUP ALARM"] and rows[0]["stage"] == "gate"
    assert m.gate_candidates == 0  # candidates only exist in confirmation mode


def test_without_a_classifier_a_gate_hit_never_shows_backup_alarm(caplog) -> None:
    """D63: the gate-only fallback is not approved. No classifier means nothing can confirm."""
    with caplog.at_level(logging.WARNING, logger="sentinel.pipeline"):
        shown, rows, m = run({}, classifier=False)
    assert shown == [] and rows == []
    assert m.gate_candidates == 1 and m.gate_confirmed == 0
    assert any("never be confirmed" in r.message for r in caplog.records)


def test_the_experimental_gate_only_switch_is_off_by_default() -> None:
    assert load_config().experimental.unverified_gate_only is False
    assert CFG.experimental.unverified_gate_only is False


def test_the_experimental_gate_only_mode_labels_every_alert_unverified(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="sentinel.pipeline"):
        shown, _, _ = run({}, confirm=False, classifier=False)
    assert shown == ["BACKUP ALARM"]
    assert any("EXPERIMENTAL" in r.message for r in caplog.records)
    audio_shown = []

    class Cap(Rec):
        def show(self, alert):
            audio_shown.append((alert.line1, alert.line2))
            super().show(alert)

    cfg = copy.deepcopy(CFG)
    cfg.experimental.unverified_gate_only = True
    Pipeline(cfg, AudioFileSource(alarm_scene(), t0=0.0), Cap(), eventlog=EventLog(),
             source_label="synthetic").run_sync()  # fmt: skip
    assert audio_shown == [("BACKUP ALARM", "UNVERIFIED")]


def test_the_switch_does_not_touch_other_sounds_or_normal_runs() -> None:
    shown, rows, _ = run({BABY: 0.8}, confirm=False)  # a classified sound is not labeled unverified
    assert "BABY CRY" in shown
    assert "baby_cry" in [r["alert_class"] for r in rows]
    normal, normal_rows, _ = run({})
    assert normal == [] and normal_rows == []


# ---- contradiction: the classifier confidently says something else ----
def test_baby_cry_classified_confidently_suppresses_the_backup_alarm() -> None:
    shown, rows, m = run({BABY: 0.8})
    assert "BACKUP ALARM" not in shown and shown == ["BABY CRY"]  # the real sound is still shown
    assert "baby_cry" in [r["alert_class"] for r in rows]
    assert m.gate_suppressed >= 1 and m.gate_suppressed == m.gate_candidates  # every pulse
    assert m.gate_confirmed == 0


@pytest.mark.parametrize("sound, index", [("vacuum cleaner", VACUUM), ("laughter", LAUGHTER)])
def test_non_catalog_sounds_classified_confidently_suppress_the_backup_alarm(sound, index) -> None:
    shown, rows, m = run({index: 0.8})
    assert shown == [] and rows == [], sound  # not in the catalog: nothing to show at all
    assert m.gate_suppressed >= 1 and m.gate_suppressed == m.gate_candidates


def test_a_contradicted_window_can_never_count_as_a_reversing_hit() -> None:
    """Reversing scores 0.5 (above its 0.30 threshold) but another sound scores 0.8: no alarm."""
    shown, rows, m = run({REVERSING: 0.5, VACUUM: 0.8})
    assert "BACKUP ALARM" not in shown and rows == []
    assert m.gate_suppressed >= 1


def test_the_stage_blocks_a_contradicted_reversing_detection_but_not_other_sounds() -> None:
    stage = ClassifierStage(Scripted({}), CFG.classifier)
    scores = {c: 0.0 for c in A} | {A.REVERSING_ALARM: 0.6, A.DOORBELL: 0.6}
    for t in (1.0, 1.25):
        out = stage.update(scores, t, t, contradicts_reversing=True)
    assert [d.alert_class for d in out] == [A.DOORBELL]  # doorbell follows its normal rules
    out = []
    for t in (2.0, 2.25):
        out = stage.update(scores, t, t, contradicts_reversing=False)
    assert {d.alert_class for d in out} == {A.REVERSING_ALARM, A.DOORBELL}


# ---- an inconclusive result is not positive confirmation ----
def test_an_inconclusive_classifier_result_does_not_confirm_the_candidate() -> None:
    # Reversing 0.2 is below its 0.30 threshold. The top class (0.25) is below it too, so nothing
    # contradicts the candidate and nothing confirms it. It must not alert, and it must expire.
    shown, rows, m = run({REVERSING: 0.2, BEEP: 0.25}, audio=alarm_scene(total_s=24.0))
    assert shown == [] and rows == []
    assert m.gate_confirmed == 0 and m.gate_suppressed == 0 and m.gate_unconfirmed >= 1


# ---- genuine confirmation ----
def test_positive_reversing_evidence_shows_the_alert_with_both_stages() -> None:
    shown, rows, m = run({REVERSING: 0.6})
    assert shown == ["BACKUP ALARM"]
    ev = rows[0]
    assert (ev["alert_class"], ev["stage"], ev["confirmed"], ev["level"]) == (
        "reversing_alarm", "both", 1, "interrupt")  # fmt: skip
    assert m.gate_confirmed >= 1 and m.gate_suppressed == 0
    # the onset comes from the gate, which is far more precise than the classifier's estimate
    assert ALARM_START_S - 0.1 <= ev["t_onset"] <= ALARM_START_S + 0.1


def test_confirmation_is_never_faster_than_the_gate_alone() -> None:
    """The alert now waits for the classifier. The scripted classifier models no window lag, so
    this only pins the direction. The real figure is larger and is reported separately (D60)."""
    _, gate_rows, _ = run({}, confirm=False)
    _, conf_rows, _ = run({REVERSING: 0.6})
    assert conf_rows[0]["alarm_to_display_ms"] > gate_rows[0]["alarm_to_display_ms"]


def test_the_classifier_alone_can_still_raise_a_reversing_alert_without_a_gate_hit() -> None:
    shown, rows, m = run({REVERSING: 0.6}, audio=alarm_scene(alarm=False))
    assert shown == ["BACKUP ALARM"] and rows[0]["stage"] == "classifier"
    assert m.gate_candidates == 0


def test_a_confirmed_candidate_does_not_leave_a_second_event_behind() -> None:
    _, rows, _ = run({REVERSING: 0.6})
    assert len(rows) == 1  # cooldown and the existing one alert at a time rules still hold


# ---- other sounds are untouched ----
def test_other_sounds_follow_their_normal_profile_rules_whatever_the_gate_does() -> None:
    assert run({DOORBELL: 0.8}, profile="home")[0] == ["DOORBELL"]
    shown, rows, _ = run({DOORBELL: 0.8}, profile="focus")
    assert shown == [] and rows and {r["level"] for r in rows} == {"later"}
    assert run({DOORBELL: 0.8}, profile="sleep")[:2] == ([], [])


def test_the_reversing_profile_levels_still_apply_after_confirmation() -> None:
    shown, rows, _ = run({REVERSING: 0.6}, profile="focus")  # focus: quiet
    assert shown == ["BACKUP ALARM"] and rows[0]["level"] == "quiet"
    assert run({REVERSING: 0.6}, profile="sleep")[:2] == ([], [])  # sleep: off


# ---- the observation itself ----
def observe(spec: dict[int, float], sensitivity: float = 1.0) -> ClassifierObservation:
    stage = ClassifierStage(Scripted({}), CFG.classifier)
    if sensitivity != 1.0:
        store = ProfileStore.from_config(CFG)
        store.update({"sensitivity": sensitivity})
        stage.profile = store
    raw = np.zeros(521, dtype=np.float32)
    for i, v in spec.items():
        raw[i] = v
    scores = {c: float(max((raw[i] for i in IDX[c]), default=0.0)) for c in IDX}
    return stage._observe(raw, scores, 1.0, 1.0)


def test_observation_contradicts_only_when_another_class_is_confident_enough() -> None:
    assert observe({VACUUM: 0.8}).contradicts_reversing
    assert not observe({VACUUM: 0.25}).contradicts_reversing  # below the reversing threshold 0.30
    assert not observe({REVERSING: 0.8}).contradicts_reversing  # the top class is reversing beeps
    assert not observe({REVERSING: 0.9, VACUUM: 0.5}).contradicts_reversing  # reversing outranks it
    assert observe({REVERSING: 0.4, VACUUM: 0.8}).contradicts_reversing  # another class outranks it
    assert not observe({}).contradicts_reversing


def test_observation_threshold_follows_the_active_sensitivity() -> None:
    assert not observe({VACUUM: 0.25}).contradicts_reversing
    assert observe({VACUUM: 0.25}, sensitivity=1.5).contradicts_reversing  # threshold 0.20
    assert not observe({VACUUM: 0.4}, sensitivity=0.5).contradicts_reversing  # threshold 0.60


def test_observation_carries_the_top_class_and_is_taken_once() -> None:
    stage = ClassifierStage(Scripted({VACUUM: 0.7}, start=0), CFG.classifier)
    from sentinel.buffer import RingBuffer
    from sentinel.types import AudioFrame

    ring = RingBuffer(5.0)
    frame = AudioFrame(np.zeros(8000, dtype=np.float32), 0.5)
    ring.write(frame.samples, frame.t_end)
    stage.step(frame, ring, lambda: 0.6)
    obs = stage.take_observation()
    assert obs is not None and obs.top_index == VACUUM and obs.top_score == pytest.approx(0.7)
    assert obs.t_window_end == 0.5 and stage.take_observation() is None


# ---- threads ----
def test_observations_reach_the_policy_thread_in_the_threaded_pipeline() -> None:
    """Real-time, threaded run: a contradicting classifier must suppress the candidate too."""
    cfg = copy.deepcopy(CFG)
    # a short scene with the alarm at 1 s
    sig = np.zeros(7 * SAMPLE_RATE, dtype=np.float32)
    a = synth.tonal_alarm(1900, 70, 5)
    sig[SAMPLE_RATE : SAMPLE_RATE + len(a)] = a
    audio = synth.mix(sig, synth.noise("pink", 7.0, seed=3), 10).audio
    store = ProfileStore.from_config(cfg)
    stage = ClassifierStage(Scripted({VACUUM: 0.8}, start=3), cfg.classifier)
    log, display, metrics = EventLog(), Rec(), Metrics()
    Pipeline(cfg, AudioFileSource(audio, realtime=True), display, metrics, eventlog=log,
             classifier=stage, profile=store, source_label="synthetic").run()  # fmt: skip
    assert [a.line1 for a in display.shown] == []
    assert metrics.gate_candidates >= 1 and metrics.gate_suppressed >= 1
    assert metrics.dropped_frames == 0


def test_the_summary_reports_candidate_outcomes() -> None:
    _, _, m = run({VACUUM: 0.8})
    s = m.summary()
    assert s["gate_candidates"] == s["gate_suppressed"] >= 1 and s["gate_confirmed"] == 0
