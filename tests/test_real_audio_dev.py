"""Optional real-audio checks. DEVELOPMENT ONLY.

These tests use clips from the ESC-50 dataset, which is believed to be non-commercial licensed
(check the dataset page before relying on that). The clips are never committed and never shown at
the event. The normal test suite does not need them: every test here skips, with the reason, when
the clips or the model are missing.

To run them, put ESC-50 wav files in data/dev_audio/esc50/ (data/ is gitignored) or point the
SENTINEL_DEV_AUDIO_DIR environment variable at a folder that holds them. ESC-50 file names end in
the class number, for example 1-100038-A-30.wav. Class 30 is a door knock.

These are a smell test on a handful of clips. They say nothing about overall accuracy, they do not
cover doorbells (ESC-50 has no doorbell class), and no number from them may be quoted as a result.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pytest

from sentinel.adapters.audio_file import AudioFileSource, load_wav_16k
from sentinel.classify.yamnet import build_classifier_stage
from sentinel.config import load_config, resolve_path
from sentinel.eventlog import EventLog
from sentinel.pipeline import Pipeline
from sentinel.profile import ProfileStore
from sentinel.types import SAMPLE_RATE, Alert, DisplayCaps

CFG = load_config()
DEV_DIR = Path(os.environ.get("SENTINEL_DEV_AUDIO_DIR") or resolve_path("data/dev_audio/esc50"))
KNOCK = 30  # door knock
NEGATIVE_CLASSES = {22, 25, 26, 32, 35, 36}  # clapping, footsteps, laughing, typing, washer, vacuum
TONAL_EVERYDAY = {20, 26, 36}  # crying baby, laughing, vacuum cleaner
MAX_PER_GROUP = 6


def clips(targets: set[int]) -> list[Path]:
    if not DEV_DIR.is_dir():
        return []
    found = []
    for p in sorted(DEV_DIR.glob("*.wav")):
        m = re.search(r"-(\d+)\.wav$", p.name)
        if m and int(m.group(1)) in targets and p.stat().st_size > 10_000:
            found.append(p)
    return found[:MAX_PER_GROUP]


def need(targets: set[int]) -> list[Path]:
    if not resolve_path(CFG.classifier.model_path).exists():
        pytest.skip("run tools/fetch_models.py first")
    found = clips(targets)
    if not found:
        pytest.skip(f"ESC-50 dev clips not found in {DEV_DIR} (see the docstring of this file)")
    return found


class Rec:
    def __init__(self) -> None:
        self.shown: list[Alert] = []

    def capabilities(self):
        return DisplayCaps(supports_blink=True)

    def show(self, a):
        self.shown.append(a)

    def clear(self, event_id=None): ...
    def close(self): ...


def run_clip(path: Path, profile: str):
    audio = load_wav_16k(path)
    audio = np.concatenate([np.zeros(SAMPLE_RATE, dtype=np.float32), audio])  # one second lead-in
    store = ProfileStore.from_config(CFG)
    store.set_active(profile)
    log, display = EventLog(), Rec()
    Pipeline(CFG, AudioFileSource(audio, t0=0.0), display, eventlog=log,
             classifier=build_classifier_stage(CFG.classifier), profile=store,
             source_label="replay").run_sync()  # fmt: skip
    return display.shown, log.list()


def test_real_knock_clips_follow_the_active_profile() -> None:
    """Same real sound, three profiles. Home interrupts, Focus saves for later, Sleep drops it."""
    found = need({KNOCK})
    home = [run_clip(p, "home") for p in found]
    detected = [
        (shown, rows) for shown, rows in home if any(r["alert_class"] == "knock" for r in rows)
    ]
    assert len(detected) >= max(1, len(found) // 2), "knock was not recognised in most clips"
    for shown, rows in detected:
        assert any(a.line1 == "KNOCK" and a.tier == 1 for a in shown)
        assert {r["profile"] for r in rows} == {"home"}
    for p in found:
        shown_f, rows_f = run_clip(p, "focus")
        assert shown_f == [] or all(a.line1 != "KNOCK" for a in shown_f)
        assert all(r["level"] == "later" for r in rows_f if r["alert_class"] == "knock")
        shown_s, rows_s = run_clip(p, "sleep")
        assert not any(r["alert_class"] == "knock" for r in rows_s)
        assert all(a.line1 != "KNOCK" for a in shown_s)


def test_everyday_clips_raise_no_classifier_alert() -> None:
    """Classifier path only: typing, clapping, footsteps, laughing and appliances must not be
    mistaken for a catalog sound. (The tonal gate is checked separately below.)"""
    found = need(NEGATIVE_CLASSES)
    wrong = [
        (p.name, r["alert_class"])
        for p in found
        for r in run_clip(p, "home")[1]
        if r["stage"] != "gate"
    ]
    assert len(wrong) <= max(1, len(found) // 10), f"classifier events on negative clips: {wrong}"


@pytest.mark.xfail(
    strict=False,
    reason="KNOWN ISSUE found on real clips (docs/DECISIONS.md D58): the tonal gate raises a "
    "provisional BACKUP ALARM on tonal everyday sounds such as a crying baby, a vacuum cleaner "
    "or laughing. Not fixed in Milestone A.",
)
def test_known_issue_gate_does_not_flash_backup_alarm_on_tonal_everyday_sounds() -> None:
    found = need(TONAL_EVERYDAY)
    flashed = [
        p.name for p in found if any(a.line1 == "BACKUP ALARM" for a in run_clip(p, "home")[0])
    ]
    assert not flashed, f"BACKUP ALARM shown for {flashed}"
