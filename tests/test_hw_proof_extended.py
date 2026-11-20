"""Extended hardware-proof cases (docs/M6_INTEGRATION_PLAN.md section 5). They run against a
fake lens that remembers what is on it. They prove the script logs each case honestly. They say
nothing about the M6: no SDK is known, no adapter exists, no glasses were used."""

from __future__ import annotations

import time

import pytest

from test_realaudio import FakeLens, load_tool

proof = load_tool("hw_proof")
FAST = {"hold_s": 0.0, "soak_minutes": 0.02, "soak_interval_s": 0.05, "sleep": time.sleep}


def cases_by_name(lens) -> dict:
    return {c["case"]: c for c in proof.run_extended(lens, **FAST)}


@pytest.fixture(scope="module")
def standard():
    """One run on a plain fake lens, shared by the tests that only read it (the delayed-show
    case takes about 2.5 s of real time)."""
    lens = FakeLens()
    return lens, cases_by_name(lens)


def test_all_extended_cases_are_present_and_leave_observation_to_a_human(standard) -> None:
    _, cases = standard
    assert list(cases) == [
        "rapid_alternating",
        "stale_clear",
        "delayed_old_show",
        "short_and_long",
        "soak",
    ]
    for c in cases.values():
        assert c["device_ack"] == "UNKNOWN" and c["observed_on_lens"] is None
        assert c["expected_on_lens"] and c["software_errors"] == 0


def test_each_call_separates_software_result_from_what_was_seen() -> None:
    log = proof.run_proof(FakeLens(), updates=2, hold_s=0, sleep=lambda s: None)
    for c in log["calls"]:
        assert c["software_call"] == "ok" and c["device_ack"] == "UNKNOWN"
        assert c["observed_on_lens"] is None
    assert "UNKNOWN" in log["device_ack_note"] and "not time to photons" in log["note"]


def test_rapid_alternating_makes_40_back_to_back_calls(standard) -> None:
    c = standard[1]["rapid_alternating"]
    assert len(c["calls"]) == 40 and [x["text"] for x in c["calls"][:3]] == [
        "HELLO",
        "DOORBELL",
        "HELLO",
    ]


def test_a_stale_clear_is_sent_after_a_newer_message_and_logged_as_such(standard) -> None:
    c = standard[1]["stale_clear"]
    assert [(x["call"], x["event_id"]) for x in c["calls"]] == [
        ("show", "proof_a"),
        ("show", "proof_b"),
        ("clear", "proof_a"),
    ]
    assert "must not erase" in c["expected_on_lens"]


def test_the_delayed_old_show_exposes_an_adapter_with_no_sequence_check(standard) -> None:
    lens, cases = standard  # last write wins, like an adapter without a sequence check
    c = cases["delayed_old_show"]
    assert c["old_show_finished_after_newer"] is True
    order = [w for _, w in c["inner_call_order"]]
    assert order.index("show proof_b done") < order.index("show proof_a done")
    first_a = lens.history.index("HELLO", lens.history.index("DOORBELL", 44))
    assert first_a > 0  # the stale A is in the history after B, before later cases ran
    assert "sequence check" in c["note"]


def test_short_and_long_messages_use_the_declared_limit_and_say_it_is_assumed(standard) -> None:
    lens, cases = standard
    c = cases["short_and_long"]
    limit = lens.capabilities().max_chars_per_line
    texts = [x["text"] for x in c["calls"]]
    assert len(texts[0]) == 1 and len(texts[1]) == limit and len(texts[2]) == limit - 1
    assert c["caps_are_assumed"] is True and "ASSUMED" in c["note"]
    assert all(len(line) <= limit for line in c["software_truncation_of_an_over_limit_line"])


def test_the_soak_runs_for_the_requested_time_and_counts_errors() -> None:
    lens = FakeLens(fail_on=50)  # the 50th show is inside the soak (47 shows come before it)
    t0 = time.perf_counter()
    c = cases_by_name(lens)["soak"]
    assert time.perf_counter() - t0 >= 1.2  # 0.02 minutes
    assert c["calls_made"] == len(c["calls"]) >= 10 and c["errors"] >= 1
    assert c["slowest_call_ms"] is not None


class CutLink(FakeLens):
    """Fails from call n until restored, imitating a dropped link. Not a model of the glasses."""

    def __init__(self, cut_after: int) -> None:
        super().__init__()
        self.cut_after, self.up = cut_after, True

    def show(self, alert) -> None:
        self.n += 1
        if self.n > self.cut_after and not self.up:
            raise ConnectionError("link down")
        super().show(alert)


def test_manual_cases_record_when_errors_start_and_when_calls_succeed_again() -> None:
    lens = CutLink(cut_after=0)
    answers: list[str] = []

    def ask(prompt: str) -> str:
        answers.append(prompt)
        lens.up = "RECONNECT" in prompt  # cut during the first case, restored for the second
        return ""

    cases = proof.run_manual_cases(lens, ask=ask, sleep=lambda s: None)
    names = [c["case"] for c in cases]
    assert names == ["disconnect_during_update", "reconnect"]
    assert cases[0]["first_error_index"] == 0  # the link was already down
    assert cases[1]["first_success_index"] == 0  # the second prompt restored it
    assert cases[0]["device_ack"] == "UNKNOWN" and cases[1]["observed_on_lens"] is None
    assert "cannot see the loss" in cases[0]["note"] and "restart" in cases[1]["note"]


def test_extended_flags_exist_and_default_to_off() -> None:
    import inspect

    src = inspect.getsource(proof.main)
    assert "--extended" in src and "--manual" in src and "--soak-minutes" in src
    assert 'action="store_true", help="also run the extra cases"' in src


# ---- Milestone E: the eleven operations, verdicts and guidance ----
def test_the_procedure_lists_the_eleven_operations_in_order() -> None:
    assert [p[0] for p in proof.PROCEDURE] == list(range(1, 12))
    ops = [p[1] for p in proof.PROCEDURE]
    assert ops[0] == "show one message" and ops[10] == "clear the final message"
    assert "disconnect during an update" in ops[6] and ops[7].startswith("reconnect")


def test_procedure_status_marks_what_ran_and_never_invents_a_verdict(standard) -> None:
    _, cases = standard
    base = proof.run_proof(FakeLens(), updates=20, hold_s=0, sleep=lambda s: None)
    base["extended_cases"] = list(cases.values())
    base["final_clear"] = proof.final_clear(FakeLens())
    status = {p["step"]: p for p in proof.procedure_status(base)}
    assert all(status[n]["ran"] for n in (1, 2, 3, 4, 5, 6, 9, 10, 11))
    assert not status[7]["ran"] and not status[8]["ran"]  # manual cases were not requested
    assert status[7]["needs_flag"] == "--manual" and status[4]["needs_flag"] == "--extended"
    assert all(p["verdict"] is None for p in status.values())


def test_every_case_has_pass_and_fail_guidance_and_an_empty_human_verdict(standard) -> None:
    _, cases = standard
    for name, c in cases.items():
        assert c["pass_if"] and c["fail_if"], name
        assert c["verdict"] is None and c["failure_category"] is None
        assert c["device_ack"] == "UNKNOWN" and c["observed_on_lens"] is None
    assert set(proof.FAILURE_CATEGORIES) == {
        "stale_message",
        "unexpected_clear",
        "failed_update",
        "no_recovery",
    }
    g = proof.GUIDANCE
    assert "stale_message" in g["delayed_old_show"]["fail_if"]
    assert "unexpected_clear" in g["stale_clear"]["fail_if"]
    assert "NOT_SUPPORTED" in g["reconnect"]["fail_if"]  # no reconnect is not a pass


def test_base_stages_carry_guidance_and_each_call_has_the_three_levels() -> None:
    log = proof.run_proof(FakeLens(), updates=3, hold_s=0, sleep=lambda s: None)
    assert set(log["stage_guidance"]) == {"1_show", "2_replace", "3_updates", "4_clear"}
    assert set(log["failure_categories"]) == set(proof.FAILURE_CATEGORIES)
    for c in log["calls"]:
        assert {"software_call", "device_ack", "observed_on_lens", "verdict"} <= set(c)
        assert (c["software_call"], c["device_ack"], c["observed_on_lens"]) == (
            "ok",
            "UNKNOWN",
            None,
        )


def test_the_final_clear_is_a_logged_case_that_records_failures() -> None:
    lens = FakeLens()
    lens.on_lens = "DOORBELL"
    c = proof.final_clear(lens)
    assert c["case"] == "final_clear" and lens.on_lens is None and c["software_errors"] == 0

    class BrokenClear(FakeLens):
        def clear(self, event_id=None):
            raise ConnectionError("link down")

    bad = proof.final_clear(BrokenClear())
    assert bad["software_errors"] == 1 and bad["calls"][0]["software_call"] == "error"


def test_the_assumed_limits_stay_labeled_assumed(standard) -> None:
    _, cases = standard
    c = cases["short_and_long"]
    assert c["caps_are_assumed"] is True and "ASSUMED" in c["note"]
    assert "ASSUMED" in c["fail_if"] and "DOCUMENTED" in c["pass_if"]


# ---- Milestone I: the very first physical test (HELLO, DOORBELL, clear) ----
def test_first_test_is_hello_then_doorbell_then_clear_in_order() -> None:
    lens = FakeLens()
    log = proof.run_first_test(lens, hold_s=0, sleep=lambda s: None)
    assert [x["stage"] for x in log["first_test"]] == ["1_hello", "2_replace", "3_clear"]
    assert lens.history == ["HELLO", "DOORBELL", None]  # the replace had no clear before it
    assert lens.on_lens is None and log["software_errors"] == 0


def test_first_test_uses_single_line_messages_and_assumes_no_limit() -> None:
    texts = [t[3] for t in proof.FIRST_TEST if t[3]]
    assert texts == ["HELLO", "DOORBELL"]  # short, one line each, no forced two-line split
    log = proof.run_first_test(FakeLens(), hold_s=0, sleep=lambda s: None)
    assert "No line or character limit is assumed" in log["note"]
    assert "official SDK sample unchanged" in log["note"]
    assert "MySoundprint Ready" not in str(log)


def test_first_test_never_claims_a_device_ack_or_a_visible_result() -> None:
    log = proof.run_first_test(FakeLens(), hold_s=0, sleep=lambda s: None)
    for x in log["first_test"]:
        assert x["software_call"] == "ok" and x["device_ack"] == "UNKNOWN"
        assert x["observed_on_lens"] is None and x["verdict"] is None
        assert x["failure_category"] is None
    assert "UNKNOWN" in log["device_ack_note"] and len(log["human_must_check"]) == 3


def test_first_test_records_a_failing_call_and_keeps_going() -> None:
    log = proof.run_first_test(FakeLens(fail_on=2), hold_s=0, sleep=lambda s: None)
    assert log["software_errors"] == 1 and len(log["first_test"]) == 3
    assert log["first_test"][1]["software_call"] == "error"


def test_first_test_skips_cleanly_on_the_stub_and_writes_nothing(capsys, tmp_path) -> None:
    rc = proof.main(["--display", "microlumin", "--first-test", "--log", str(tmp_path / "f.json")])
    assert rc == 0 and "SKIPPED" in capsys.readouterr().out and not (tmp_path / "f.json").exists()


def test_the_proof_messages_are_short_single_lines() -> None:
    log = proof.run_proof(FakeLens(), updates=2, hold_s=0, sleep=lambda s: None)
    assert log["calls"][0]["expected_on_lens"] == "HELLO"
    assert "DOORBELL only" in log["calls"][1]["expected_on_lens"]


def test_message_limits_stay_assumed_unless_the_human_says_they_are_documented() -> None:
    assert proof.run_extended is not None
    import inspect

    assert "caps_documented" in inspect.signature(proof.run_extended).parameters
    assert "--caps-documented" in inspect.getsource(proof.main)
