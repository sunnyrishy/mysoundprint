"""Real-audio evaluation harness (docs/REAL_AUDIO_EVAL.md, DECISIONS D63 and D64).

Every audio file here is a SMALL SYNTHETIC FIXTURE written to a temp folder, and every classifier
here is a scripted double keyed on the tone frequency. These tests check the harness logic: the
manifest, the bookkeeping, the timing and the reports. They say nothing about how the real
classifier performs on real doorbells or real reversing alarms. No real recording is used."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from sentinel.classify.yamnet import resolve_class_indices
from sentinel.config import ConfigError, load_config
from sentinel.realaudio import (
    PRESETS,
    ManifestError,
    apply_overrides,
    csv_rows,
    evaluate,
    load_manifest,
    parse_run_configs,
    render_markdown,
    write_manifest_skeleton,
)
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, AlertClass

CFG = load_config()
A = AlertClass
NAMES = [f"class {i}" for i in range(521)]
for _i, _n in enumerate(
    sorted({n for r in CFG.classifier.catalog().values() for n in r.names}), 40
):
    NAMES[_i] = _n
IDX = resolve_class_indices(CFG.classifier, NAMES)
VACUUM = 200  # not in the catalog
LABELS = {c.value for c in AlertClass}

# The scripted classifier decides from the dominant frequency of the window.
FREQ_RULES = {1900: {IDX[A.REVERSING_ALARM][0]: 0.9}, 2200: {VACUUM: 0.8}, 2600: {}, 600: {}}


class ToneClassifier:
    """Scores from the dominant tone: 1900 Hz reversing, 2200 Hz a confident other sound (not in
    the catalog), 2600 Hz nothing, 600 Hz doorbell. Silence and noise give nothing."""

    window_len = 15600
    class_names = NAMES

    def __init__(self) -> None:
        self.class_indices = IDX

    def infer(self, window: np.ndarray) -> np.ndarray:
        out = np.zeros(521, dtype=np.float32)
        if float(np.sqrt(np.mean(window**2))) < 0.02:
            return out
        spec = np.abs(np.fft.rfft(window * np.hanning(len(window))))
        hz = int(np.argmax(spec) * SAMPLE_RATE / len(window))
        if 570 <= hz <= 640:
            out[IDX[A.DOORBELL][0]] = 0.9
        else:
            for f, scores in FREQ_RULES.items():
                if abs(hz - f) <= 60:
                    for i, v in scores.items():
                        out[i] = v
        return out


def alarm(freq: int, total_s: float = 12.0) -> np.ndarray:
    sig = np.zeros(int(total_s * SAMPLE_RATE), dtype=np.float32)
    a = synth.tonal_alarm(freq, 70, 6)
    s = int(5.0 * SAMPLE_RATE)
    sig[s : s + len(a)] = a
    return synth.mix(sig, synth.noise("pink", total_s, seed=3), 10).audio


def chime(total_s: float = 8.0) -> np.ndarray:
    t = np.arange(int(total_s * SAMPLE_RATE)) / SAMPLE_RATE
    sig = np.zeros_like(t, dtype=np.float32)
    on = (t > 2.0) & (t < 6.0)
    sig[on] = (0.5 * np.sin(2 * np.pi * 600 * t[on])).astype(np.float32)
    return sig


def quiet(total_s: float = 8.0) -> np.ndarray:
    return synth.noise("pink", total_s, seed=5).astype(np.float32) * 0.001


FIXTURES = {
    "rev_ok.wav": (lambda: alarm(1900), "reversing_alarm", 5.0),
    "rev_wrongly_suppressed.wav": (lambda: alarm(2200), "reversing_alarm", 5.0),
    "rev_unconfirmed.wav": (lambda: alarm(2600), "reversing_alarm", 5.0),
    "neg_suppressed.wav": (lambda: alarm(2200), "negative", None),
    "neg_unconfirmed.wav": (lambda: alarm(2600), "negative", None),
    "neg_clean.wav": (quiet, "negative", None),
    "bell_ok.wav": (chime, "doorbell", 2.0),
    "bell_missing.wav": (quiet, "doorbell", 2.0),
}


def write_dir(tmp_path: Path, names: list[str] | None = None, origin: str = "synthetic") -> Path:
    d = tmp_path / "rec"
    d.mkdir()
    rows = ["file,label,origin,onset_s,notes"]
    for name in names or list(FIXTURES):
        make, label, onset = FIXTURES[name]
        sf.write(d / name, make(), SAMPLE_RATE, subtype="FLOAT")
        rows.append(f"{name},{label},{origin},{'' if onset is None else onset},fixture")
    (d / "manifest.csv").write_text("\n".join(rows) + "\n")
    return d


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    d = write_dir(tmp_path_factory.mktemp("fx"))
    return evaluate(
        d, CFG, ToneClassifier(), parse_run_configs(["unverified_gate_only"], []), "home"
    )


def by_file(report, config="production") -> dict[str, dict]:
    return {r["file"]: r for r in report["configs"][config]["recordings"]}


# ---- manifest ----
def test_a_valid_manifest_with_several_recordings_per_category_loads(tmp_path) -> None:
    d = write_dir(tmp_path)
    recs = load_manifest(d, LABELS)
    assert len(recs) == len(FIXTURES)
    assert sum(1 for r in recs if r.label == "reversing_alarm") == 3
    assert sum(1 for r in recs if r.label == "doorbell") == 2
    assert all(r.origin == "synthetic" and r.duration_s > 1 for r in recs)
    assert next(r for r in recs if r.rel == "rev_ok.wav").onset_s == 5.0


def test_subfolders_and_every_catalog_label_are_accepted(tmp_path) -> None:
    d = tmp_path / "r"
    (d / "knocks").mkdir(parents=True)
    sf.write(d / "knocks" / "k1.wav", quiet(), SAMPLE_RATE)
    (d / "manifest.csv").write_text("file,label,origin\nknocks/k1.wav,knock,real\n")
    recs = load_manifest(d, LABELS)
    assert recs[0].label == "knock" and recs[0].origin == "real"


def bad_manifest(tmp_path, body: str, files: dict[str, bytes] | None = None) -> Path:
    d = tmp_path / "bad"
    d.mkdir(parents=True, exist_ok=True)
    sf.write(d / "ok.wav", quiet(), SAMPLE_RATE)
    for name, data in (files or {}).items():
        (d / name).write_bytes(data)
    (d / "manifest.csv").write_text(body)
    return d


def test_every_manifest_problem_is_reported_together(tmp_path) -> None:
    body = (
        "file,label,origin,onset_s,end_s\n"
        "ok.wav,doorbel,real,,\n"  # unknown label
        "ok.wav,doorbell,real,,\n"  # duplicate
        "gone.wav,doorbell,real,,\n"  # missing file
        "junk.wav,doorbell,real,,\n"  # not audio
        "notes.txt,doorbell,real,,\n"  # unsupported format
        "ok.wav2,negative,maybe,,\n"  # bad origin (and unsupported)
        "../out.wav,doorbell,real,,\n"  # outside the folder
        "late.wav,doorbell,real,99,\n"  # onset past the end
        "neg.wav,negative,real,1,\n"  # negative cannot have an onset
        "num.wav,doorbell,real,abc,\n"  # not a number
    )
    d = bad_manifest(
        tmp_path,
        body,
        {"junk.wav": b"not audio at all", "notes.txt": b"x"},
    )
    sf.write(d / "late.wav", quiet(), SAMPLE_RATE)
    sf.write(d / "neg.wav", quiet(), SAMPLE_RATE)
    sf.write(d / "num.wav", quiet(), SAMPLE_RATE)
    with pytest.raises(ManifestError) as err:
        load_manifest(d, LABELS)
    msg = str(err.value)
    for needle in (
        "unknown label 'doorbel'",
        "duplicate file 'ok.wav'",
        "audio file not found: gone.wav",
        "cannot read junk.wav",
        "unsupported format '.txt'",
        "origin must be 'real' or 'synthetic'",
        "inside the recordings folder",
        "onset_s 99.0 is not inside",
        "negative recording cannot have onset_s or end_s",
        "onset_s is not a number",
    ):
        assert needle in msg, needle


def test_a_missing_required_column_or_folder_or_manifest_fails_clearly(tmp_path) -> None:
    d = bad_manifest(tmp_path, "file,label\nok.wav,doorbell\n")
    with pytest.raises(ManifestError, match=r"missing column.*origin"):
        load_manifest(d, LABELS)
    with pytest.raises(ManifestError, match="folder not found"):
        load_manifest(tmp_path / "nope", LABELS)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ManifestError, match="Manifest not found"):
        load_manifest(empty, LABELS)
    d2 = bad_manifest(tmp_path / "again", "file,label,origin\n")
    with pytest.raises(ManifestError, match="no recordings"):
        load_manifest(d2, LABELS)


def test_a_silent_zero_length_file_is_rejected(tmp_path) -> None:
    d = bad_manifest(tmp_path, "file,label,origin\nz.wav,negative,real\n")
    sf.write(d / "z.wav", np.zeros(0, dtype=np.float32), SAMPLE_RATE)
    with pytest.raises(ManifestError, match="no audio"):
        load_manifest(d, LABELS)


def test_the_skeleton_lists_files_never_guesses_a_label_and_never_overwrites(tmp_path) -> None:
    d = tmp_path / "s"
    d.mkdir()
    sf.write(d / "doorbell_01.wav", quiet(), SAMPLE_RATE)
    path = write_manifest_skeleton(d)
    text = path.read_text()
    assert "doorbell_01.wav,,,,,,,,,," in text  # the name suggests a label, but it is not used
    with pytest.raises(ManifestError, match=r"empty|unknown label|origin"):
        load_manifest(d, LABELS)
    with pytest.raises(ManifestError, match="already exists"):
        write_manifest_skeleton(d)


# ---- originals and privacy ----
def snapshot(d: Path) -> dict[str, str]:
    return {
        p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


def test_evaluation_leaves_the_recordings_folder_exactly_as_it_was(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav", "neg_clean.wav"])
    before = snapshot(d)
    evaluate(d, CFG, ToneClassifier())
    assert snapshot(d) == before  # no copies, no edits, no extra files, no changed bytes


def test_the_default_recording_folders_are_gitignored() -> None:
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text().splitlines()
    assert "data/*" in ignore  # data/real_audio/ is under it
    assert "reports/*" in ignore  # reports/real_audio/ is under it


# ---- ground truth, candidates, suppression and misses ----
def test_a_confirmed_reversing_alarm_is_detected_and_shows_the_alert(report) -> None:
    r = by_file(report)["rev_ok.wav"]
    assert r["outcome"] == "detected" and r["final_decision"] == "alert_shown"
    assert r["gate_candidate"] and "confirmed" in r["candidate_fates"]
    assert r["alerts_shown"][0]["line1"] == "BACKUP ALARM" and r["profile"] == "home"
    assert r["events"][0]["level"] == "interrupt" and r["events"][0]["profile"] == "home"
    assert r["unverified"] is False


def test_a_suppressed_candidate_on_a_negative_is_correct_and_not_a_miss(report) -> None:
    r = by_file(report)["neg_suppressed.wav"]
    assert r["outcome"] == "clean" and r["correctly_suppressed"] is True
    assert r["candidate_fate"] == "suppressed" and r["events"] == []
    assert r["classifier_top"] == NAMES[VACUUM] if r["classifier_top"] else True


def test_an_unconfirmed_candidate_on_a_negative_is_also_correctly_suppressed(report) -> None:
    r = by_file(report)["neg_unconfirmed.wav"]
    assert r["outcome"] == "clean" and r["correctly_suppressed"] is True
    assert r["candidate_fate"] == "unconfirmed"


def test_a_positive_whose_candidate_is_suppressed_is_a_miss_with_that_reason(report) -> None:
    r = by_file(report)["rev_wrongly_suppressed.wav"]
    assert r["outcome"] == "missed" and r["correctly_suppressed"] is False
    assert r["miss_reason"] == "candidate_suppressed_by_classifier"
    assert r["final_decision"] == "none"


def test_a_positive_whose_candidate_never_confirms_is_a_different_miss(report) -> None:
    r = by_file(report)["rev_unconfirmed.wav"]
    assert r["outcome"] == "missed" and r["miss_reason"] == "candidate_never_confirmed"


def test_a_positive_with_no_evidence_at_all_is_a_third_kind_of_miss(report) -> None:
    r = by_file(report)["bell_missing.wav"]
    assert r["outcome"] == "missed" and not r["gate_candidate"]
    assert r["miss_reason"] == "no_gate_candidate_and_no_classifier_detection"


def test_a_clean_negative_has_no_candidate_and_is_not_called_suppressed(report) -> None:
    r = by_file(report)["neg_clean.wav"]
    assert r["outcome"] == "clean" and r["correctly_suppressed"] is False
    assert r["candidate_fate"] == "none"


def test_a_doorbell_is_detected_with_no_gate_candidate(report) -> None:
    r = by_file(report)["bell_ok.wav"]
    assert r["outcome"] == "detected" and not r["gate_candidate"]
    assert r["events"][0]["alert_class"] == "doorbell" and r["peak_scores"]["doorbell"] >= 0.9


def test_the_summary_counts_each_kind_separately(report) -> None:
    s = report["configs"]["production"]["summary"]["synthetic"]
    rev, bell = s["classes"]["reversing_alarm"], s["classes"]["doorbell"]
    assert (rev["positives"], rev["detected"], rev["recall"]) == (3, 1, 0.333)
    assert rev["miss_reasons"] == {
        "candidate_never_confirmed": 1,
        "candidate_suppressed_by_classifier": 1,
    }
    assert (bell["positives"], bell["detected"], bell["recall"]) == (2, 1, 0.5)
    assert rev["indicative_only"] and bell["indicative_only"]  # fewer than 20
    assert rev["precision"] == 1.0 and rev["false_alert_files"] == 0
    c = s["candidates"]
    assert c["correctly_suppressed_negatives"] == 2
    assert c["wrongly_suppressed_positives"] == 1 and c["never_confirmed_positives"] == 1
    assert c["confirmed_on_positives"] == 1 and c["false_confirmed_on_negatives"] == 0
    assert s["negative_false_alert_files"] == 0 and s["negative_false_alerts_per_hour"] == 0.0


def test_a_false_alert_on_a_negative_and_a_wrong_class_alert_are_counted(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav", "bell_ok.wav"])
    rows = (d / "manifest.csv").read_text()
    rows = rows.replace("rev_ok.wav,reversing_alarm", "rev_ok.wav,negative").replace(
        ",5.0,fixture", ",,fixture"
    )  # the alarm is now declared a negative
    rows = rows.replace("bell_ok.wav,doorbell", "bell_ok.wav,knock")  # the chime is mislabeled
    (d / "manifest.csv").write_text(rows)
    rep = evaluate(d, CFG, ToneClassifier())
    res = by_file(rep)
    assert res["rev_ok.wav"]["outcome"] == "false_alert"
    assert res["bell_ok.wav"]["outcome"] == "missed"
    assert res["bell_ok.wav"]["wrong_class_events"] == ["doorbell"]
    s = rep["configs"]["production"]["summary"]["synthetic"]
    assert s["negative_false_alert_files"] == 1 and s["negative_false_alerts_per_hour"] > 0
    assert s["positive_files_with_wrong_class_alert"] == 1
    assert s["classes"]["knock"]["recall"] == 0.0 and s["classes"]["knock"]["precision"] is None


# ---- profile ----
def test_the_selected_profile_decides_the_final_action(tmp_path) -> None:
    d = write_dir(tmp_path, ["bell_ok.wav"])
    focus = by_file(evaluate(d, CFG, ToneClassifier(), None, "focus"))["bell_ok.wav"]
    assert focus["outcome"] == "detected" and focus["final_decision"] == "saved_for_later"
    assert focus["alerts_shown"] == [] and focus["events"][0]["level"] == "later"
    assert focus["events"][0]["profile"] == "focus"
    sleep = by_file(evaluate(d, CFG, ToneClassifier(), None, "sleep"))["bell_ok.wav"]
    assert sleep["outcome"] == "profile_off" and sleep["final_decision"] == "none"


def test_a_profile_off_sound_is_kept_out_of_recall(tmp_path) -> None:
    d = write_dir(tmp_path, ["bell_ok.wav", "bell_missing.wav"])
    rep = evaluate(d, CFG, ToneClassifier(), None, "sleep")
    c = rep["configs"]["production"]["summary"]["synthetic"]["classes"]["doorbell"]
    assert c["positives"] == 0 and c["profile_off_excluded"] == 2 and c["recall"] is None


def test_an_unknown_profile_is_an_error(tmp_path) -> None:
    d = write_dir(tmp_path, ["neg_clean.wav"])
    with pytest.raises(ConfigError):
        evaluate(d, CFG, ToneClassifier(), None, "holiday")


# ---- timing ----
def test_timing_is_reported_in_stream_time_and_is_ordered_sensibly(report) -> None:
    r = by_file(report)["rev_ok.wav"]
    assert 5.0 <= r["first_candidate_s"] < r["confirmed_s"]
    assert r["confirmation_latency_s"] == pytest.approx(
        r["confirmed_s"] - r["first_candidate_s"], abs=0.002
    )
    assert 0 < r["confirmation_latency_s"] <= CFG.gate.confirm_timeout_s
    assert r["first_alert_s"] is not None and r["first_alert_s"] >= r["first_candidate_s"]
    assert r["detection_latency_s"] == pytest.approx(r["first_alert_s"] - 5.0, abs=0.002)
    assert r["detect_from_file_start_s"] == pytest.approx(r["first_alert_s"], abs=0.002)
    assert "audio stream time" in report["time_basis"]


def test_without_an_onset_there_is_no_latency_only_a_time_from_file_start(tmp_path) -> None:
    d = write_dir(tmp_path, ["bell_ok.wav"])
    (d / "manifest.csv").write_text("file,label,origin\nbell_ok.wav,doorbell,synthetic\n")
    r = by_file(evaluate(d, CFG, ToneClassifier()))["bell_ok.wav"]
    assert r["detection_latency_s"] is None and r["detect_from_file_start_s"] > 2.0
    s = evaluate(d, CFG, ToneClassifier())["configs"]["production"]["summary"]["synthetic"]
    assert s["classes"]["doorbell"]["detection_latency_s"] == {"n": 0}


def test_p95_is_only_reported_with_enough_samples(report) -> None:
    lat = report["configs"]["production"]["summary"]["synthetic"]["classes"]["reversing_alarm"][
        "detection_latency_s"
    ]
    assert lat["n"] == 1 and lat["p95"] is None  # one sample: no p95


def test_the_evaluation_is_reproducible(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav", "neg_suppressed.wav", "bell_ok.wav"])
    one = evaluate(d, CFG, ToneClassifier(), parse_run_configs(["classifier_only"], []))
    two = evaluate(d, CFG, ToneClassifier(), parse_run_configs(["classifier_only"], []))
    dump = lambda r: json.dumps(  # noqa: E731
        {k: v for k, v in r.items() if k != "recordings_dir"}, sort_keys=True
    )
    # event ids are deterministic too, so the whole report matches byte for byte
    assert dump(one) == dump(two)


# ---- reports ----
def test_a_synthetic_only_report_says_real_audio_was_not_evaluated(report) -> None:
    assert report["real_audio_evaluated"] is False and report["real_recordings"] == 0
    md = render_markdown(report)
    assert "Real-audio evaluation has NOT been performed" in md
    assert "No real recordings. Not performed." in md
    assert "SYNTHETIC audio" in md and "not end to end latency" in md


def test_real_and_synthetic_results_are_never_mixed(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav", "bell_ok.wav"])
    text = (
        (d / "manifest.csv")
        .read_text()
        .replace("bell_ok.wav,doorbell,synthetic", "bell_ok.wav,doorbell,real")
    )
    (d / "manifest.csv").write_text(text)  # a fixture standing in as "real" to test the split only
    rep = evaluate(d, CFG, ToneClassifier())
    s = rep["configs"]["production"]["summary"]
    assert rep["real_audio_evaluated"] and rep["real_recordings"] == 1
    assert set(s["real"]["classes"]) == {"doorbell"}
    assert set(s["synthetic"]["classes"]) == {"reversing_alarm"}
    assert "Real recordings evaluated: 1" in render_markdown(rep)


def test_csv_has_one_row_per_recording_per_config(report) -> None:
    cols, rows = csv_rows(report)
    assert len(rows) == 2 * len(FIXTURES) and cols[0] == "config"
    assert {r[0] for r in rows} == {"production", "unverified_gate_only"}


# ---- experimental controls ----
def test_experimental_settings_are_off_in_the_shipped_config() -> None:
    assert load_config().experimental.unverified_gate_only is False
    assert PRESETS["production"].overrides == () and PRESETS["production"].experimental is False
    assert all(p.experimental for n, p in PRESETS.items() if n != "production")


def test_comparing_configs_does_not_change_the_production_config(tmp_path) -> None:
    before = CFG.config_hash()
    d = write_dir(tmp_path, ["rev_ok.wav"])
    evaluate(
        d,
        CFG,
        ToneClassifier(),
        parse_run_configs(
            ["classifier_only", "unverified_gate_only"], ["gate.confirm_timeout_s=3"]
        ),
    )
    assert CFG.config_hash() == before and CFG.experimental.unverified_gate_only is False
    assert CFG.gate.enabled and CFG.gate.confirm_timeout_s == 1.6


def test_production_is_always_first_and_other_configs_are_labeled_experimental(report) -> None:
    assert list(report["configs"]) == ["production", "unverified_gate_only"]
    assert report["configs"]["production"]["experimental"] is False
    assert report["configs"]["unverified_gate_only"]["experimental"] is True
    md = render_markdown(report)
    assert "EXPERIMENTAL, not the production behavior" in md


def test_the_unverified_mode_alerts_where_production_does_not_and_says_so(report) -> None:
    prod = by_file(report)["rev_unconfirmed.wav"]
    exp = by_file(report, "unverified_gate_only")["rev_unconfirmed.wav"]
    assert prod["outcome"] == "missed" and prod["final_decision"] == "none"
    assert exp["outcome"] == "detected" and exp["unverified"] is True
    assert exp["alerts_shown"][0]["line2"] == "UNVERIFIED"
    assert "UNVERIFIED gate alerts" in render_markdown(report)
    # and it is a false alert on a negative, which is exactly what production avoids
    assert (
        by_file(report, "unverified_gate_only")["neg_unconfirmed.wav"]["outcome"] == "false_alert"
    )
    assert by_file(report)["neg_unconfirmed.wav"]["outcome"] == "clean"


def test_classifier_only_misses_what_needs_the_gate_and_not_the_classifier(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav"])
    rep = evaluate(d, CFG, ToneClassifier(), parse_run_configs(["classifier_only"], []))
    r = by_file(rep, "classifier_only")["rev_ok.wav"]
    assert (
        not r["gate_candidate"]
        and r["outcome"] == "detected"
        and r["events"][0]["stage"] == "classifier"
    )


def test_overrides_are_validated_and_applied_to_a_copy() -> None:
    cfg2 = apply_overrides(
        CFG, ["gate.confirm_timeout_s=2.5", "experimental.unverified_gate_only=true"]
    )
    assert cfg2.gate.confirm_timeout_s == 2.5 and cfg2.experimental.unverified_gate_only
    assert CFG.gate.confirm_timeout_s == 1.6 and not CFG.experimental.unverified_gate_only
    for bad in (
        "gate.nope=1",
        "nosection.key=1",
        "gate.confirm_timeout_s",
        "gate.confirm_timeout_s=-1",
    ):
        with pytest.raises(ConfigError):
            apply_overrides(CFG, [bad])
    with pytest.raises(ConfigError, match="unknown config"):
        parse_run_configs(["gate_but_better"], [])


def test_without_a_classifier_the_gate_cannot_alert_unless_explicitly_opted_in(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav"])
    rep = evaluate(d, CFG, None, parse_run_configs(["gate_without_classifier"], []))
    assert by_file(rep)["rev_ok.wav"]["outcome"] == "missed"  # production run with no classifier
    assert by_file(rep)["rev_ok.wav"]["miss_reason"] == "candidate_never_confirmed"
    exp = by_file(rep, "gate_without_classifier")["rev_ok.wav"]
    assert exp["outcome"] == "detected" and exp["unverified"] is True


# ---- the tools stay opt-in
ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


def test_the_latency_probe_refuses_to_run_without_the_experimental_flag(capsys) -> None:
    probe = load_tool("latency_probe")
    rc = probe.main(["--probes", "1", "--interval", "8", "--display", "console", "--no-classifier"])
    err = capsys.readouterr().err
    assert rc == 2 and "--experimental-unverified-gate-only" in err and "UNVERIFIED" in err


def test_the_replay_tool_shows_no_backup_alarm_without_the_flag(tmp_path, capsys) -> None:
    from sentinel.eventlog import EventLog

    mod = load_tool("run_replay")
    db = tmp_path / "e.sqlite"
    rc = mod.main([str(ROOT / "scenarios" / "tonal_10db.yaml"), "--no-classifier", "--db", str(db)])
    assert rc == 0
    assert EventLog(str(db)).list() == []
    assert "UNVERIFIED" not in capsys.readouterr().out.replace("cannot show", "")


def test_the_eval_tool_reports_problems_and_writes_nothing_for_a_bad_folder(
    tmp_path, capsys
) -> None:
    tool = load_tool("eval_real")
    rc = tool.main(["--dir", str(tmp_path / "missing"), "--out", str(tmp_path / "out")])
    assert rc == 2 and "folder not found" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_the_eval_tool_init_manifest_never_guesses(tmp_path, capsys) -> None:
    tool = load_tool("eval_real")
    d = tmp_path / "r"
    d.mkdir()
    sf.write(d / "a.wav", quiet(), SAMPLE_RATE)
    assert tool.main(["--dir", str(d), "--init-manifest"]) == 0
    assert (d / "manifest.csv").read_text().splitlines()[1] == "a.wav,,,,,,,,,,"


# ---- first hardware proof ----
class FakeLens:
    """A display that remembers what is on it, so stale text would be visible."""

    def __init__(self, fail_on: int | None = None) -> None:
        self.on_lens: str | None = None
        self.history: list[str | None] = []
        self.n = 0
        self.fail_on = fail_on

    def capabilities(self):
        from sentinel.types import DisplayCaps

        return DisplayCaps()

    def show(self, alert) -> None:
        self.n += 1
        if self.n == self.fail_on:
            raise RuntimeError("link dropped")
        self.on_lens = alert.line1
        self.history.append(alert.line1)

    def clear(self, event_id=None) -> None:
        self.on_lens = None
        self.history.append(None)

    def close(self) -> None:
        pass


def test_the_hardware_proof_runs_the_four_stages_and_leaves_the_lens_empty() -> None:
    proof = load_tool("hw_proof")
    lens = FakeLens()
    log = proof.run_proof(lens, updates=6, hold_s=0, sleep=lambda s: None)
    assert [c["stage"] for c in log["calls"]] == ["1_show", "2_replace"] + ["3_updates"] * 6 + [
        "4_clear"
    ]
    assert lens.history[:2] == ["HELLO", "DOORBELL"]  # B replaced A with no clear between
    assert None not in lens.history[:-1]  # nothing cleared until stage 4
    assert lens.history[2:8] == ["HELLO", "DOORBELL"] * 3 and lens.on_lens is None
    assert log["adapter_errors"] == 0 and len(log["human_must_check"]) == 5
    assert all(c["observed_on_lens"] is None for c in log["calls"])  # only a human fills this in
    assert "not time to photons" in log["note"]


def test_the_hardware_proof_records_adapter_failures_and_keeps_going() -> None:
    proof = load_tool("hw_proof")
    log = proof.run_proof(FakeLens(fail_on=3), updates=4, hold_s=0, sleep=lambda s: None)
    assert log["adapter_errors"] == 1 and len(log["calls"]) == 7
    assert "link dropped" in next(c["error"] for c in log["calls"] if c["error"])


def test_the_hardware_proof_skips_cleanly_on_the_stub_adapter(capsys, tmp_path) -> None:
    proof = load_tool("hw_proof")
    rc = proof.main(["--display", "microlumin", "--log", str(tmp_path / "p.json")])
    assert rc == 0 and "SKIPPED" in capsys.readouterr().out and not (tmp_path / "p.json").exists()


def test_config_copy_is_untouched_by_these_tests() -> None:
    assert copy.deepcopy(CFG).experimental.unverified_gate_only is False
