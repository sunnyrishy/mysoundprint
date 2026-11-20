"""Acceptance rules for the reversing alarm (docs/REAL_AUDIO_EVAL.md, DECISIONS D69).

These test the rules and the statistics with built-up results and a few synthetic fixtures. They
say nothing about how the real classifier performs. No real recording is used. A fixture that is
marked origin real in a manifest here is a stand-in used only to test the bookkeeping."""

from __future__ import annotations

import pytest

from sentinel.realaudio import (
    ACCEPT_NEG_HOURS,
    RecordingResult,
    evaluate,
    negative_acceptance,
    parse_run_configs,
    poisson_upper_bound,
    positive_acceptance,
    render_markdown,
    wilson_interval,
)
from test_realaudio import CFG, ToneClassifier, write_dir


def rr(file, label="reversing_alarm", origin="real", outcome="detected", duration=10.0, **meta):
    r = RecordingResult(file, label, origin, duration, "production", "home", None)
    r.outcome = outcome
    r.metadata = {k: v for k, v in meta.items() if v}
    r.sha256 = meta.get("sha") or file
    r.metadata.pop("sha", None)
    return r


def units(n: int, detected: int, label="reversing_alarm", origin="real"):
    return [
        rr(f"p{i}.wav", label, origin, "detected" if i < detected else "missed", source_id=f"s{i}")
        for i in range(n)
    ]


# ---- statistics --------------------------------------------------------------------------------
def test_wilson_interval_known_values_and_decision_uses_unrounded_bounds() -> None:
    assert wilson_interval(95, 100) == [0.888, 0.978]
    assert wilson_interval(99, 100)[0] == pytest.approx(0.946, abs=0.001)
    assert wilson_interval(100, 100)[0] == pytest.approx(0.963, abs=0.001)
    assert wilson_interval(0, 0) is None


def test_poisson_upper_bound_matches_the_exact_values() -> None:
    assert poisson_upper_bound(0, 30.0) == pytest.approx(0.0999, abs=0.0002)  # about 0.1 per hour
    assert poisson_upper_bound(0, 1.0) == pytest.approx(2.9957, abs=0.001)  # the rule of three
    assert poisson_upper_bound(2, 10.0) == pytest.approx(0.6296, abs=0.001)
    assert poisson_upper_bound(0, 0.0) is None


# ---- positive acceptance ------------------------------------------------------------------------
def test_no_real_positive_recordings_is_not_evaluated() -> None:
    assert positive_acceptance([])["status"] == "NOT_EVALUATED"
    synthetic = units(200, 200, origin="synthetic")
    assert positive_acceptance(synthetic)["status"] == "NOT_EVALUATED"  # synthetic never counts
    doorbell = units(200, 200, label="doorbell")
    assert positive_acceptance(doorbell)["status"] == "NOT_EVALUATED"  # not combined with doorbell


def test_a_small_real_set_is_indicative_even_when_every_one_is_detected() -> None:
    acc = positive_acceptance(units(50, 50))
    assert acc["status"] == "INDICATIVE" and acc["observed_recall"] == 1.0
    assert acc["requirements_met"]["enough_independent_units"] is False
    assert any("need at least 100" in u for u in acc["unmet"])


def test_99_of_100_is_not_met_because_the_wilson_lower_bound_is_below_95() -> None:
    acc = positive_acceptance(units(100, 99))
    assert acc["observed_recall"] == 0.99
    assert acc["requirements_met"] == {
        "enough_independent_units": True,
        "observed_recall": True,
        "wilson_lower_bound": False,
    }
    assert acc["status"] == "INDICATIVE" and acc["recall_ci95"][0] < 0.95
    assert any("Wilson lower bound" in u for u in acc["unmet"])


def test_100_of_100_independent_real_recordings_is_met() -> None:
    acc = positive_acceptance(units(100, 100))
    assert acc["status"] == "MET" and acc["independent_units"] == 100
    assert acc["recall_ci95"][0] >= 0.95 and acc["unmet"] == []
    assert "Wilson" in acc["recall_ci_method"] and "not safety claims" in acc["note"]


def test_low_recall_with_enough_units_is_indicative_and_names_what_failed() -> None:
    acc = positive_acceptance(units(120, 100))
    assert acc["status"] == "INDICATIVE" and acc["observed_recall"] < 0.95
    assert acc["requirements_met"]["observed_recall"] is False and acc["missed_units"] == 20


def test_files_without_a_source_id_are_not_counted_as_independent() -> None:
    files = [rr(f"f{i}.wav") for i in range(150)]  # 150 detected files, no source_id at all
    acc = positive_acceptance(files)
    assert acc["status"] == "INDICATIVE" and acc["independent_units"] == 0
    assert acc["eligible_real_files"] == 150 and len(acc["files_without_source_id"]) == 150
    assert "independence is not established" in acc["independence_limitation"]
    assert acc["observed_recall"] is None and acc["recall_ci95"] is None


def test_many_files_with_one_source_id_are_one_independent_unit() -> None:
    files = [rr(f"take{i}.wav", source_id="one_truck") for i in range(100)]
    acc = positive_acceptance(files)
    assert acc["eligible_real_files"] == 100 and acc["independent_units"] == 1
    assert acc["status"] == "INDICATIVE" and acc["source_ids_with_several_files"] == ["one_truck"]


def test_a_unit_counts_as_detected_only_if_every_file_in_it_was_detected() -> None:
    files = [rr("a1.wav", source_id="A"), rr("a2.wav", outcome="missed", source_id="A")]
    acc = positive_acceptance([*files, rr("b1.wav", source_id="B")])
    assert (acc["independent_units"], acc["detected_units"], acc["missed_units"]) == (2, 1, 1)


def test_a_sound_the_profile_turns_off_is_not_an_eligible_positive() -> None:
    files = [rr("x.wav", outcome="profile_off", source_id="x"), rr("y.wav", source_id="y")]
    assert positive_acceptance(files)["independent_units"] == 1


def test_per_recording_outcomes_and_only_supplied_metadata_are_reported() -> None:
    files = [
        rr("a.wav", source_id="A", session="day1"),
        rr("b.wav", outcome="missed", source_id="B"),
    ]
    acc = positive_acceptance(files)
    by = {p["file"]: p for p in acc["per_recording"]}
    assert by["a.wav"]["metadata"] == {"source_id": "A", "session": "day1"}
    assert by["b.wav"]["metadata"] == {"source_id": "B"}  # nothing invented for the rest
    assert by["b.wav"]["outcome"] == "missed" and acc["distinct_sessions_reported"] == 1


# ---- negative acceptance ------------------------------------------------------------------------
def neg(file, hours, k=0, sha=None, shown=True):
    r = rr(file, label="negative", duration=hours * 3600.0, sha=sha or file)
    r.events = [
        {"event_id": f"{file}_{i}", "alert_class": "reversing_alarm", "shown": shown,
         "cancel_reason": None if shown else "timeout"}
        for i in range(k)
    ]  # fmt: skip
    return r


def test_no_real_negative_data_is_not_evaluated() -> None:
    assert negative_acceptance([])["status"] == "NOT_EVALUATED"
    syn = [rr("s.wav", label="negative", origin="synthetic", duration=200 * 3600)]
    acc = negative_acceptance(syn)
    assert acc["status"] == "NOT_EVALUATED" and acc["rate_upper_bound_per_hour_95"] is None


def test_zero_false_alerts_with_too_little_exposure_is_indicative() -> None:
    acc = negative_acceptance([neg("n1", 10.0), neg("n2", 19.99)])
    assert acc["status"] == "INDICATIVE" and acc["false_backup_alarms"] == 0
    assert acc["negative_exposure_hours"] == pytest.approx(29.99, abs=0.001)
    assert acc["rate_upper_bound_per_hour_95"] == pytest.approx(0.1, abs=0.002)
    assert any("need at least 30" in u for u in acc["unmet"])


def test_zero_false_alerts_over_30_hours_is_met_with_the_bound_labeled_an_estimate() -> None:
    acc = negative_acceptance([neg("n1", 15.0), neg("n2", 15.0)])
    assert ACCEPT_NEG_HOURS == 30.0 and acc["status"] == "MET"
    assert acc["negative_exposure_seconds"] == 108000.0 and acc["negative_exposure_hours"] == 30.0
    assert acc["rate_upper_bound_per_hour_95"] == pytest.approx(0.0999, abs=0.0002)
    assert "not proof" in acc["rate_upper_bound_note"]
    assert "NOT verified" in " ".join(acc["rate_upper_bound_assumptions"])
    assert "Garwood" in acc["rate_upper_bound_method"]


def test_a_false_alert_is_not_met_whatever_the_exposure_and_is_reported_in_full() -> None:
    acc = negative_acceptance([neg("n1", 20.0, k=1), neg("n2", 20.0)])
    assert acc["status"] == "NOT_MET" and acc["false_backup_alarms"] == 1
    assert acc["false_backup_alarms_per_hour"] == pytest.approx(0.025, abs=0.0001)
    assert acc["rate_upper_bound_per_hour_95"] == pytest.approx(0.1, abs=0.05) and (
        acc["rate_upper_bound_per_hour_95"] > acc["false_backup_alarms_per_hour"]
    )
    assert acc["false_backup_alarm_events"] == [{"file": "n1", "event_id": "n1_0"}]
    assert any("1 false BACKUP ALARM" in u for u in acc["unmet"])
    # exposure alone does not rescue it, and a short run with a false alert is not met either
    assert negative_acceptance([neg("n", 0.5, k=3)])["status"] == "NOT_MET"


def test_an_alert_that_was_never_shown_and_was_cancelled_is_not_a_false_alert() -> None:
    assert negative_acceptance([neg("n", 40.0, k=2, shown=False)])["status"] == "MET"


def test_the_same_audio_is_never_counted_twice() -> None:
    acc = negative_acceptance([neg("a.wav", 20.0, sha="same"), neg("copy.wav", 20.0, sha="same")])
    assert acc["unique_recordings_counted"] == 1 and acc["duplicate_files_not_counted"] == [
        "copy.wav"
    ]
    assert acc["negative_exposure_hours"] == 20.0 and acc["status"] == "INDICATIVE"


# ---- real and synthetic stay apart, through the real pipeline path ------------------------------
def test_synthetic_recordings_never_feed_acceptance(tmp_path) -> None:
    d = write_dir(tmp_path)  # every fixture is origin synthetic
    rep = evaluate(d, CFG, ToneClassifier())
    acc = rep["acceptance"]
    assert (
        acc["positive"]["status"] == "NOT_EVALUATED"
        and acc["negative"]["status"] == "NOT_EVALUATED"
    )
    assert rep["configs"]["production"]["summary"]["synthetic"]["acceptance"] is None
    md = render_markdown(rep)
    assert "synthetic recordings never count toward acceptance" in md
    assert "NOT_EVALUATED" in md and "Real-audio evaluation has NOT been performed" in md


def stand_in_real(tmp_path, with_ids: bool):
    d = write_dir(
        tmp_path, ["rev_ok.wav", "rev_wrongly_suppressed.wav", "neg_clean.wav", "bell_ok.wav"]
    )
    p = d / "manifest.csv"
    p.write_text(
        "file,label,origin,onset_s,source_id,session,notes\n"
        f"rev_ok.wav,reversing_alarm,real,5.0,{'truckA' if with_ids else ''},day1,fixture\n"
        f"rev_wrongly_suppressed.wav,reversing_alarm,real,5.0,{'truckB' if with_ids else ''},,\n"
        "neg_clean.wav,negative,real,,,,\n"
        "bell_ok.wav,doorbell,real,2.0,doorA,,\n"
    )
    return d


def test_real_stand_ins_are_evaluated_but_only_independent_reversing_files_count(tmp_path) -> None:
    rep = evaluate(stand_in_real(tmp_path, with_ids=True), CFG, ToneClassifier())
    pos, ng = rep["acceptance"]["positive"], rep["acceptance"]["negative"]
    assert pos["status"] == "INDICATIVE" and pos["independent_units"] == 2  # doorbell not counted
    assert (pos["detected_units"], pos["missed_units"]) == (1, 1)
    by = {p["file"]: p for p in pos["per_recording"]}
    assert by["rev_ok.wav"]["metadata"] == {
        "source_id": "truckA",
        "session": "day1",
        "notes": "fixture",
    }
    assert by["rev_wrongly_suppressed.wav"]["outcome"] == "missed"
    assert by["rev_wrongly_suppressed.wav"]["miss_reason"] == "candidate_suppressed_by_classifier"
    assert ng["status"] == "INDICATIVE" and ng["false_backup_alarms"] == 0
    assert rep["real_audio_evaluated"] is True


def test_without_source_ids_the_report_states_the_independence_limitation(tmp_path) -> None:
    rep = evaluate(stand_in_real(tmp_path, with_ids=False), CFG, ToneClassifier())
    pos = rep["acceptance"]["positive"]
    assert pos["independent_units"] == 0 and pos["eligible_real_files"] == 2
    assert pos["independence_limitation"] and pos["status"] == "INDICATIVE"
    assert "independence limitation" in render_markdown(rep)


def test_production_is_primary_and_experimental_configs_keep_their_own_labeled_results(
    tmp_path,
) -> None:
    d = stand_in_real(tmp_path, with_ids=True)
    rep = evaluate(d, CFG, ToneClassifier(), parse_run_configs(["unverified_gate_only"], []))
    assert rep["acceptance"] is rep["configs"]["production"]["summary"]["real"]["acceptance"]
    exp = rep["configs"]["unverified_gate_only"]
    assert exp["experimental"] is True and exp["summary"]["real"]["acceptance"] is not None
    assert rep["configs"]["production"]["experimental_switches_enabled"] == []
    assert exp["experimental_switches_enabled"] == ["unverified_gate_only"]


def test_the_manifest_accepts_source_id_and_the_skeleton_lists_it(tmp_path) -> None:
    from sentinel.realaudio import OPTIONAL_COLUMNS

    assert "source_id" in OPTIONAL_COLUMNS
    d = stand_in_real(tmp_path, with_ids=True)
    assert [i["source_id"] for i in evaluate(d, CFG, ToneClassifier())["inputs"]][:2] == [
        "truckA",
        "truckB",
    ]


# ---- timing vocabulary ---------------------------------------------------------------------------
def test_the_report_names_each_kind_of_time_and_claims_no_end_to_end_latency(tmp_path) -> None:
    rep = evaluate(write_dir(tmp_path, ["rev_ok.wav"]), CFG, ToneClassifier())
    t = rep["time_terms"]
    assert set(t) == {
        "audio_stream_detection_time",
        "audio_stream_confirmation_time",
        "evaluation_processing_time",
        "software_display_adapter_call_time",
        "device_acknowledgement_time",
        "visible_display_time",
        "end_to_end_latency",
    }
    assert t["audio_stream_detection_time"].startswith("MEASURED")
    assert t["audio_stream_confirmation_time"].startswith("MEASURED")
    assert t["evaluation_processing_time"].startswith("NOT")
    assert t["device_acknowledgement_time"].startswith("UNKNOWN")
    assert t["visible_display_time"].startswith("NOT measured")
    assert t["end_to_end_latency"].startswith("NOT measured")
