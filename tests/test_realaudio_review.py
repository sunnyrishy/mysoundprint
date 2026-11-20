"""Milestone D review of the evaluation harness: confidence intervals, targets, exposure, labeled
windows, reproducibility information. Fixtures are synthetic and the classifier is a scripted
double. Nothing here measures real recognition quality."""

from __future__ import annotations

import hashlib

import pytest

from sentinel.realaudio import (
    _stats,
    evaluate,
    parse_run_configs,
    render_markdown,
    wilson_interval,
)
from test_realaudio import CFG, PRESETS, ToneClassifier, by_file, write_dir


# ---- confidence interval ----
def test_wilson_interval_matches_known_values() -> None:
    assert wilson_interval(0, 0) is None
    lo, hi = wilson_interval(95, 100)
    assert (lo, hi) == (0.888, 0.978)  # standard Wilson 95% interval for 95 of 100
    assert wilson_interval(0, 10)[0] == 0.0 and wilson_interval(10, 10)[1] == 1.0
    lo, hi = wilson_interval(1, 3)
    assert 0 <= lo < 1 / 3 < hi <= 1 and hi - lo > 0.6  # a tiny sample gives a very wide interval


def test_every_class_reports_a_recall_interval_and_names_the_method(tmp_path) -> None:
    d = write_dir(tmp_path)
    rep = evaluate(d, CFG, ToneClassifier())
    c = rep["configs"]["production"]["summary"]["synthetic"]["classes"]["reversing_alarm"]
    assert c["recall_ci95"] == wilson_interval(c["detected"], c["positives"])
    assert "Wilson" in c["recall_ci_method"] and "independent" in c["recall_ci_method"]
    md = render_markdown(rep)
    assert "95% CI [" in md and "Wilson score interval" in md


# ---- false alerts and exposure ----
def test_false_alerts_per_hour_counts_events_over_negative_exposure(tmp_path) -> None:
    d = write_dir(tmp_path, ["neg_unconfirmed.wav", "neg_clean.wav"])
    rep = evaluate(d, CFG, ToneClassifier(), parse_run_configs(["unverified_gate_only"], []))
    prod = rep["configs"]["production"]["summary"]["synthetic"]
    exp = rep["configs"]["unverified_gate_only"]["summary"]["synthetic"]
    assert (
        prod["negative_false_alert_events"] == 0 and prod["negative_false_alerts_per_hour"] == 0.0
    )
    assert prod["false_backup_alarm_events_on_negatives"] == 0
    assert exp["negative_false_alert_events"] >= 1
    hours = (12.0 + 8.0) / 3600.0
    assert exp["negative_audio_hours"] == pytest.approx(hours, abs=1e-4)
    assert exp["negative_false_alerts_per_hour"] == pytest.approx(
        exp["negative_false_alert_events"] / hours, rel=0.01
    )
    assert exp["false_backup_alarm_events_on_negatives"] >= 1
    assert exp["negative_audio_seconds"] == pytest.approx(20.0, abs=0.01)
    assert "false BACKUP ALARM events on negatives" in render_markdown(rep)


def test_without_negatives_the_rate_is_not_invented(tmp_path) -> None:
    rep = evaluate(write_dir(tmp_path, ["rev_ok.wav"]), CFG, ToneClassifier())
    s = rep["configs"]["production"]["summary"]["synthetic"]
    assert s["negative_false_alerts_per_hour"] is None and s["negative_audio_hours"] == 0.0


# ---- labeled window: an event outside the labeled time is not a detection ----
def test_an_event_far_outside_the_labeled_window_is_not_a_detection(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav"])
    p = d / "manifest.csv"
    # label the sound as ending at 1.0 s: the alert at about 5.3 s is outside 0.5 to 2.0 s
    p.write_text("file,label,origin,onset_s,end_s\nrev_ok.wav,reversing_alarm,synthetic,0.5,1.0\n")
    r = by_file(evaluate(d, CFG, ToneClassifier()))["rev_ok.wav"]
    assert r["outcome"] == "missed" and r["miss_reason"] == "event_outside_labeled_window"
    assert len(r["out_of_window_events"]) == 1


def test_an_event_inside_the_labeled_window_is_a_detection_with_its_own_time(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav"])
    (d / "manifest.csv").write_text(
        "file,label,origin,onset_s,end_s\nrev_ok.wav,reversing_alarm,synthetic,5.0,9.0\n"
    )
    r = by_file(evaluate(d, CFG, ToneClassifier()))["rev_ok.wav"]
    assert r["outcome"] == "detected" and r["out_of_window_events"] == []
    assert 5.0 <= r["events"][0]["t_detect_s"] <= 10.0
    assert r["events"][0]["t_detect_s"] == pytest.approx(r["first_alert_s"], abs=0.002)


def test_a_manifest_without_times_accepts_an_event_anywhere_in_the_file(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav"])
    (d / "manifest.csv").write_text("file,label,origin\nrev_ok.wav,reversing_alarm,synthetic\n")
    assert by_file(evaluate(d, CFG, ToneClassifier()))["rev_ok.wav"]["outcome"] == "detected"


# ---- timing statistics ----
def test_confirmation_time_reports_median_p95_and_max_only_when_measurable() -> None:
    assert _stats([]) == {"n": 0}
    few = _stats([0.2, 0.4, 0.3])
    assert few["median"] == 0.3 and few["max"] == 0.4 and few["p95"] is None
    many = _stats([i / 100 for i in range(1, 21)])
    assert many["n"] == 20 and many["max"] == 0.2 and many["p95"] == pytest.approx(0.191, abs=0.001)
    assert "p95 n/a (n<20)" in render_markdown_for_latency(few)


def render_markdown_for_latency(stats: dict) -> str:
    from sentinel.realaudio import _lat

    return _lat(stats)


def test_the_report_never_calls_stream_time_end_to_end_latency(tmp_path) -> None:
    rep = evaluate(write_dir(tmp_path), CFG, ToneClassifier())
    md = render_markdown(rep)
    assert "audio stream time" in rep["time_basis"] and "not end to end latency" in md
    assert "end-to-end latency:" not in md.lower()


# ---- reproduction information ----
def test_the_report_has_what_is_needed_to_reproduce_a_run(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav", "neg_clean.wav"])
    rep = evaluate(d, CFG, ToneClassifier())
    info = rep["run_info"]
    assert info["config_hash"] == CFG.config_hash() and info["config"]["gate"]["enabled"] is True
    assert info["config"]["experimental"]["unverified_gate_only"] is False
    assert set(info["classifier_model"]) == {"path", "sha256", "found"}
    assert (info["classifier_model"]["sha256"] is None) is (not info["classifier_model"]["found"])
    assert set(info["software"]) >= {"python", "numpy", "scipy", "soundfile"}
    assert "git_commit" in info and "git_working_tree_clean" in info
    inputs = {i["file"]: i for i in rep["inputs"]}
    assert (
        inputs["rev_ok.wav"]["sha256"]
        == hashlib.sha256((d / "rev_ok.wav").read_bytes()).hexdigest()
    )
    assert (
        inputs["rev_ok.wav"]["duration_s"] == 12.0
        and inputs["neg_clean.wav"]["label"] == "negative"
    )
    rec = by_file(rep)["rev_ok.wav"]
    assert rec["sha256"] == inputs["rev_ok.wav"]["sha256"]


def test_production_always_runs_first_even_if_asked_otherwise(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_ok.wav"])
    rep = evaluate(d, CFG, ToneClassifier(), [PRESETS["classifier_only"]])
    assert list(rep["configs"]) == ["production", "classifier_only"]
    assert rep["configs"]["production"]["experimental"] is False
    assert rep["configs"]["classifier_only"]["experimental"] is True


def test_events_in_a_report_carry_the_experimental_marker(tmp_path) -> None:
    d = write_dir(tmp_path, ["rev_unconfirmed.wav"])
    rep = evaluate(d, CFG, ToneClassifier(), parse_run_configs(["unverified_gate_only"], []))
    assert by_file(rep)["rev_unconfirmed.wav"]["events"] == []
    exp = by_file(rep, "unverified_gate_only")["rev_unconfirmed.wav"]["events"]
    assert exp and all(e["experimental"] is True for e in exp)
