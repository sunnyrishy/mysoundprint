"""Manifest and acceptance validation checks (docs/REAL_AUDIO_RECORDING_GUIDE.md).

Everything here is a small synthetic fixture in a temp folder, with a scripted classifier. Where a
manifest says origin real it is only a stand-in used to test the bookkeeping. No real recording
was evaluated and none of this says anything about real-world performance."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import soundfile as sf

from sentinel.realaudio import (
    ManifestError,
    evaluate,
    load_manifest,
    negative_acceptance,
    positive_acceptance,
    render_markdown,
    split_overview,
)
from sentinel.types import SAMPLE_RATE, AlertClass
from test_acceptance import neg, rr
from test_realaudio import CFG, FIXTURES, ToneClassifier, quiet

LABELS = {c.value for c in AlertClass}


def folder(tmp_path: Path, rows: str, names=("rev_ok.wav", "neg_clean.wav", "bell_ok.wav")) -> Path:
    d = tmp_path / "rec"
    d.mkdir(parents=True)
    for n in names:
        sf.write(d / n, FIXTURES[n][0](), SAMPLE_RATE, subtype="FLOAT")
    (d / "manifest.csv").write_text(rows)
    return d


# ---- inputs the harness must reject or report, never repair ----
def test_a_negative_onset_is_rejected(tmp_path) -> None:
    d = folder(tmp_path, "file,label,origin,onset_s\nrev_ok.wav,reversing_alarm,real,-1\n")
    with pytest.raises(ManifestError, match=r"onset_s must be 0 or more"):
        load_manifest(d, LABELS)


def test_nan_and_infinite_onsets_are_rejected(tmp_path) -> None:
    for bad in ("nan", "inf"):
        d = folder(
            tmp_path / bad, f"file,label,origin,onset_s\nrev_ok.wav,reversing_alarm,real,{bad}\n"
        )
        with pytest.raises(ManifestError, match=r"onset_s"):
            load_manifest(d, LABELS)


def test_end_before_onset_and_past_the_end_are_rejected(tmp_path) -> None:
    d = folder(tmp_path, "file,label,origin,onset_s,end_s\nrev_ok.wav,reversing_alarm,real,6,5\n")
    with pytest.raises(ManifestError, match=r"end_s must be greater than onset_s"):
        load_manifest(d, LABELS)
    d = folder(
        tmp_path / "b", "file,label,origin,onset_s,end_s\nrev_ok.wav,reversing_alarm,real,1,99\n"
    )
    with pytest.raises(ManifestError, match=r"past the end"):
        load_manifest(d, LABELS)


def test_a_corrupt_or_zero_duration_file_is_rejected_not_given_a_made_up_length(tmp_path) -> None:
    d = folder(tmp_path, "file,label,origin\nbad.wav,negative,real\nempty.wav,negative,real\n", ())
    (d / "bad.wav").write_bytes(b"RIFF....not really a wav")
    sf.write(d / "empty.wav", quiet()[:0], SAMPLE_RATE)
    with pytest.raises(ManifestError) as err:
        load_manifest(d, LABELS)
    assert "cannot read bad.wav" in str(err.value) and "no audio" in str(err.value)


def test_a_manifest_row_for_a_missing_file_names_the_file(tmp_path) -> None:
    d = folder(tmp_path, "file,label,origin\nnothere.wav,negative,real\n", ())
    with pytest.raises(ManifestError, match=r"audio file not found: nothere\.wav"):
        load_manifest(d, LABELS)


def test_unknown_labels_and_a_blank_label_are_rejected_not_guessed(tmp_path) -> None:
    d = folder(tmp_path, "file,label,origin\nrev_ok.wav,reversing,real\nneg_clean.wav,,real\n")
    with pytest.raises(ManifestError) as err:
        load_manifest(d, LABELS)
    assert "unknown label 'reversing'" in str(err.value) and "unknown label ''" in str(err.value)


def test_a_missing_source_id_is_left_empty_not_invented(tmp_path) -> None:
    d = folder(tmp_path, "file,label,origin\nrev_ok.wav,reversing_alarm,real\n")
    rec = load_manifest(d, LABELS)[0]
    assert rec.source_id == "" and rec.session == "" and rec.split == ""
    rep = evaluate(d, CFG, ToneClassifier())
    pos = rep["acceptance"]["positive"]
    assert pos["independent_units"] == 0 and pos["files_without_source_id"] == ["rev_ok.wav"]
    assert by_name(rep)["rev_ok.wav"]["metadata"] == {}  # nothing made up for the report either


def by_name(rep):
    return {r["file"]: r for r in rep["configs"]["production"]["recordings"]}


# ---- real and synthetic stay apart ----
def test_synthetic_files_never_count_toward_real_acceptance(tmp_path) -> None:
    d = folder(
        tmp_path,
        "file,label,origin,source_id\nrev_ok.wav,reversing_alarm,synthetic,x\n"
        "neg_clean.wav,negative,synthetic,\n",
    )
    rep = evaluate(d, CFG, ToneClassifier())
    assert rep["acceptance"]["positive"]["status"] == "NOT_EVALUATED"
    assert rep["acceptance"]["negative"]["status"] == "NOT_EVALUATED"
    assert "Real-audio evaluation has NOT been performed" in render_markdown(rep)


def test_mixed_real_and_synthetic_results_are_reported_in_separate_blocks(tmp_path) -> None:
    d = folder(
        tmp_path,
        "file,label,origin,source_id\nrev_ok.wav,reversing_alarm,real,r1\n"
        "neg_clean.wav,negative,synthetic,\nbell_ok.wav,doorbell,real,d1\n",
    )
    s = evaluate(d, CFG, ToneClassifier())["configs"]["production"]["summary"]
    assert set(s["real"]["classes"]) == {"reversing_alarm", "doorbell"}
    assert s["synthetic"]["negatives"] == 1 and s["real"]["negatives"] == 0
    assert s["real"]["acceptance"]["positive"]["independent_units"] == 1  # the doorbell is not one


# ---- repeated source_id, duplicate audio ----
def test_repeated_source_id_is_one_unit(tmp_path) -> None:
    d = folder(
        tmp_path,
        "file,label,origin,source_id\nrev_ok.wav,reversing_alarm,real,same\n"
        "rev_wrongly_suppressed.wav,reversing_alarm,real,same\n",
        ("rev_ok.wav", "rev_wrongly_suppressed.wav"),
    )
    pos = evaluate(d, CFG, ToneClassifier())["acceptance"]["positive"]
    assert (
        pos["independent_units"] == 1 and pos["missed_units"] == 1
    )  # one file missed: unit missed
    assert pos["source_ids_with_several_files"] == ["same"]


def test_identical_audio_under_two_source_ids_is_one_recording_not_two_units() -> None:
    a = rr("a.wav", source_id="A", sha="same-bytes")
    b = rr("copy_of_a.wav", source_id="B", sha="same-bytes")  # copied, then given a new id
    pos = positive_acceptance([a, b, rr("c.wav", source_id="C", sha="different")])
    assert pos["independent_units"] == 2 and pos["duplicate_files_not_counted"] == ["copy_of_a.wav"]


def test_duplicate_audio_found_on_disk_by_hash_is_not_counted_twice(tmp_path) -> None:
    d = folder(
        tmp_path,
        "file,label,origin,source_id\nrev_ok.wav,reversing_alarm,real,A\n"
        "rev_copy.wav,reversing_alarm,real,B\n",
        ("rev_ok.wav",),
    )
    shutil.copy(d / "rev_ok.wav", d / "rev_copy.wav")
    pos = evaluate(d, CFG, ToneClassifier())["acceptance"]["positive"]
    assert pos["independent_units"] == 1 and pos["duplicate_files_not_counted"] == ["rev_copy.wav"]


def test_duplicate_negative_audio_is_not_counted_as_extra_exposure() -> None:
    acc = negative_acceptance([neg("a", 20.0, sha="s"), neg("b", 20.0, sha="s")])
    assert acc["negative_exposure_hours"] == 20.0 and acc["duplicate_files_not_counted"] == ["b"]


def test_a_duplicate_manifest_row_is_rejected(tmp_path) -> None:
    d = folder(
        tmp_path, "file,label,origin\nrev_ok.wav,reversing_alarm,real\nrev_ok.wav,negative,real\n"
    )
    with pytest.raises(ManifestError, match=r"duplicate file 'rev_ok\.wav'"):
        load_manifest(d, LABELS)


def test_source_ids_that_differ_only_by_case_are_flagged_not_merged() -> None:
    pos = positive_acceptance([rr("a.wav", source_id="TruckA"), rr("b.wav", source_id="trucka")])
    assert pos["independent_units"] == 2  # not silently merged
    assert pos["source_ids_differing_only_by_case"] == [["TruckA", "trucka"]]


# ---- development and holdout split ----
SPLIT_ROWS = (
    "file,label,origin,source_id,split\n"
    "rev_ok.wav,reversing_alarm,real,A,dev\n"
    "rev_wrongly_suppressed.wav,reversing_alarm,real,B,holdout\n"
    "neg_clean.wav,negative,real,envA,dev\n"
    "neg_suppressed.wav,negative,real,envB,holdout\n"
)
SPLIT_FILES = ("rev_ok.wav", "rev_wrongly_suppressed.wav", "neg_clean.wav", "neg_suppressed.wav")


def test_a_source_group_cannot_straddle_dev_and_holdout(tmp_path) -> None:
    rows = SPLIT_ROWS.replace(
        "rev_wrongly_suppressed.wav,reversing_alarm,real,B",
        "rev_wrongly_suppressed.wav,reversing_alarm,real,A",
    )
    with pytest.raises(ManifestError, match=r"'A' appears in both dev and holdout"):
        load_manifest(folder(tmp_path, rows, SPLIT_FILES), LABELS)


def test_if_any_row_declares_a_split_every_row_must(tmp_path) -> None:
    rows = SPLIT_ROWS.replace(
        "neg_clean.wav,negative,real,envA,dev", "neg_clean.wav,negative,real,envA,"
    )
    with pytest.raises(ManifestError, match=r"not for 'neg_clean\.wav'"):
        load_manifest(folder(tmp_path, rows, SPLIT_FILES), LABELS)


def test_a_bad_split_value_and_a_file_without_a_source_id_are_rejected(tmp_path) -> None:
    rows = SPLIT_ROWS.replace(",A,dev", ",A,train").replace(
        "neg_suppressed.wav,negative,real,envB,holdout",
        "rev_unconfirmed.wav,reversing_alarm,real,,holdout",
    )
    d = folder(tmp_path, rows, (*SPLIT_FILES, "rev_unconfirmed.wav"))
    with pytest.raises(ManifestError) as err:
        load_manifest(d, LABELS)
    assert "split must be 'dev' or 'holdout'" in str(err.value)
    assert "'rev_unconfirmed.wav' has no source_id" in str(err.value)


def test_split_evaluation_keeps_each_set_separate(tmp_path) -> None:
    d = folder(tmp_path, SPLIT_ROWS, SPLIT_FILES)
    dev = evaluate(d, CFG, ToneClassifier(), split="dev")
    ho = evaluate(d, CFG, ToneClassifier(), split="holdout")
    assert dev["split_evaluated"] == "dev" and ho["split_evaluated"] == "holdout"
    assert {i["file"] for i in dev["inputs"]} == {"rev_ok.wav", "neg_clean.wav"}
    assert {i["file"] for i in ho["inputs"]} == {"rev_wrongly_suppressed.wav", "neg_suppressed.wav"}
    assert dev["acceptance"]["positive"]["detected_units"] == 1
    assert ho["acceptance"]["positive"]["detected_units"] == 0  # a miss stays in the holdout
    assert "Split evaluated: **holdout**" in render_markdown(ho)


def test_asking_for_a_split_that_does_not_exist_fails_and_evaluates_nothing(tmp_path) -> None:
    d = folder(tmp_path, "file,label,origin\nrev_ok.wav,reversing_alarm,real\n")
    with pytest.raises(ManifestError, match=r"needs a split column"):
        evaluate(d, CFG, ToneClassifier(), split="holdout")


def test_without_a_declared_split_a_holdout_evaluation_is_reported_as_not_possible(
    tmp_path,
) -> None:
    d = folder(tmp_path, "file,label,origin,source_id\nrev_ok.wav,reversing_alarm,real,A\n")
    rep = evaluate(d, CFG, ToneClassifier())
    ov = rep["split_overview"]
    assert ov["declared"] is False and ov["holdout_evaluation_possible"] is False
    assert "NOT possible" in ov["note"] and rep["split_evaluated"] == "all"


def test_a_tiny_holdout_is_possible_but_cannot_reach_met(tmp_path) -> None:
    d = folder(tmp_path, SPLIT_ROWS, SPLIT_FILES)
    ov = split_overview(load_manifest(d, LABELS))
    assert ov["holdout_evaluation_possible"] is True and ov["holdout_could_reach_MET"] is False
    assert ov["per_split"]["holdout"]["real_reversing_alarm_source_groups"] == 1
    assert "once, at the end" in ov["note"]


def test_a_holdout_without_negatives_or_groups_is_not_possible(tmp_path) -> None:
    rows = (
        "file,label,origin,source_id,split\n"
        "rev_ok.wav,reversing_alarm,real,A,dev\n"
        "neg_clean.wav,negative,real,envA,holdout\n"
    )
    d = folder(tmp_path, rows, ("rev_ok.wav", "neg_clean.wav"))
    ov = split_overview(load_manifest(d, LABELS))
    assert ov["declared"] is True and ov["holdout_evaluation_possible"] is False
    assert "NOT yet possible" in ov["note"]
