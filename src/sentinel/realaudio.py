"""Evaluation of local recordings against the existing pipeline (tools/eval_real.py).

What it does: read a manifest that gives each recording a ground-truth label, run every recording
through the real Pipeline and the existing classifier on the deterministic virtual clock, and report
what the tonal gate, the classifier and the confirmation logic did. It does not train, tune or
replace anything. See docs/REAL_AUDIO_EVAL.md.

Measurement method (all times are AUDIO STREAM TIME, seconds from the start of the file):
  * A time is the end time of the audio frame in which the pipeline made the decision. Resolution
    is one frame (32 ms), and the classifier only decides once per hop (250 ms).
  * Compute time and display time are NOT included, so the numbers are reproducible but are not
    end to end latency. Nothing here measures photons on the glasses.
  * detection latency = first shown alert minus the manifest onset_s. It is only reported when the
    manifest gives onset_s. Otherwise the report says "from file start" and does not call it
    latency.
  * confirmation latency = time the classifier confirmed a tonal gate candidate minus the time the
    candidate appeared. It exists only for candidates that were confirmed.

Real recordings and synthetic fixtures are separated by the manifest's required origin column and
are never aggregated together.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import math
import platform
import statistics
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import yaml

from sentinel.adapters.audio_file import AudioFileSource, load_wav_16k
from sentinel.classify.yamnet import Classifier, ClassifierStage
from sentinel.config import ConfigError, SentinelConfig, config_from_dict
from sentinel.evaluation import MATCH_TAIL_S, window_scores
from sentinel.eventlog import EventLog
from sentinel.pipeline import Pipeline
from sentinel.profile import ProfileError, ProfileStore
from sentinel.types import Alert, AlertClass, Detection, DisplayCaps

NEGATIVE = "negative"
ORIGINS = ("real", "synthetic")
AUDIO_SUFFIXES = {".wav", ".flac", ".ogg"}
REQUIRED_COLUMNS = ["file", "label", "origin"]
OPTIONAL_COLUMNS = [
    "onset_s", "end_s", "session", "distance_m", "source", "source_id", "split", "notes",
]  # fmt: skip
SPLITS = ("dev", "holdout")
SMALL_SAMPLE = 20  # fewer positives than this: the report says "indicative only"
SCHEMA_VERSION = 3
# What each time in a report or log is. Only the first two are measured by the harness. None of
# them is end to end latency, and nothing here was measured on the glasses.
TIME_TERMS = {
    "audio_stream_detection_time": "MEASURED here: seconds from file start to the audio frame in "
    "which a detection or alert happened",
    "audio_stream_confirmation_time": "MEASURED here: seconds from a gate candidate to the "
    "classifier confirming it, in audio stream time",
    "evaluation_processing_time": "NOT recorded: how long the harness itself took to compute",
    "software_display_adapter_call_time": "NOT in this report: measured only by tools/hw_proof.py "
    "and tools/hw_smoke_test.py, as how long our call took to return",
    "device_acknowledgement_time": "UNKNOWN: no adapter or SDK reports an acknowledgement",
    "visible_display_time": "NOT measured: needs a measurement on the actual glasses",
    "end_to_end_latency": "NOT measured: needs the whole path from sound to visible text",
}
# Provisional engineering targets for the reversing alarm. They are goals to test against, not
# safety claims, and nothing is reported as met without enough real recordings behind it.
ACCEPT_MIN_POSITIVES = 100  # independent real reversing-alarm recordings
ACCEPT_RECALL = 0.95  # observed recall
ACCEPT_WILSON_LOWER = 0.95  # 95% Wilson lower bound for recall
ACCEPT_NEG_HOURS = 30.0  # representative real negative exposure
ACCEPT_FALSE_BACKUP_ALARMS = 0
ACCEPTANCE_NOTE = "provisional engineering criteria, not safety claims"


class ManifestError(ConfigError):
    """The manifest or a recording cannot be used. The message lists every problem found."""


@dataclass(frozen=True)
class Recording:
    path: Path
    rel: str
    label: str  # an alert class value, or "negative"
    origin: str  # "real" or "synthetic"
    onset_s: float | None
    end_s: float | None
    session: str
    distance_m: str
    source: str
    notes: str
    duration_s: float = 0.0
    source_id: str = ""  # same id means the same underlying sound event or take: not independent
    split: str = ""  # "dev" or "holdout", or empty when the manifest declares no split

    @property
    def positive(self) -> bool:
        return self.label != NEGATIVE


# ---- manifest ---------------------------------------------------------------------------------
def _number(text: str, name: str, row: int, problems: list[str]) -> float | None:
    if text.strip() == "":
        return None
    try:
        v = float(text)
    except ValueError:
        problems.append(f"row {row}: {name} is not a number: {text!r}")
        return None
    if v < 0 or v != v:
        problems.append(f"row {row}: {name} must be 0 or more: {text!r}")
        return None
    return v


def load_manifest(
    directory: Path, valid_labels: set[str], name: str = "manifest.csv"
) -> list[Recording]:
    """Read directory/manifest.csv. Every problem is collected and reported together. Audio files
    are only opened to read their length. They are never modified, moved or copied."""
    mpath = directory / name
    if not directory.is_dir():
        raise ManifestError(f"Recordings folder not found: {directory}")
    if not mpath.is_file():
        raise ManifestError(
            f"Manifest not found: {mpath}. It needs the columns {', '.join(REQUIRED_COLUMNS)} "
            f"(optional: {', '.join(OPTIONAL_COLUMNS)}). See docs/REAL_AUDIO_EVAL.md."
        )
    problems: list[str] = []
    recs: list[Recording] = []
    seen: set[str] = set()
    root = directory.resolve()
    with mpath.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        unknown = [h for h in header if h not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
        if missing:
            raise ManifestError(f"{mpath}: missing column(s): {', '.join(missing)}")
        if unknown:
            problems.append(f"unknown column(s): {', '.join(unknown)}")
        for i, raw in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            if not any(row.values()):
                continue
            rel, label, origin = row["file"], row["label"], row["origin"]
            if not rel:
                problems.append(f"row {i}: file is empty")
                continue
            if label not in valid_labels and label != NEGATIVE:
                problems.append(
                    f"row {i}: unknown label {label!r}. Use {NEGATIVE!r} or one of: "
                    f"{', '.join(sorted(valid_labels))}"
                )
            if origin not in ORIGINS:
                problems.append(f"row {i}: origin must be 'real' or 'synthetic', got {origin!r}")
            if rel in seen:
                problems.append(f"row {i}: duplicate file {rel!r}")
            seen.add(rel)
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                problems.append(
                    f"row {i}: file must be a path inside the recordings folder: {rel!r}"
                )
                continue
            path = (directory / rel).resolve()
            if root not in path.parents:
                problems.append(f"row {i}: file escapes the recordings folder: {rel!r}")
                continue
            if path.suffix.lower() not in AUDIO_SUFFIXES:
                problems.append(f"row {i}: unsupported format {path.suffix!r} ({rel})")
                continue
            if not path.is_file():
                problems.append(f"row {i}: audio file not found: {rel}")
                continue
            try:
                info = sf.info(str(path))
                duration = info.frames / info.samplerate if info.samplerate else 0.0
            except (RuntimeError, sf.LibsndfileError) as err:
                problems.append(f"row {i}: cannot read {rel}: {err}")
                continue
            if duration <= 0:
                problems.append(f"row {i}: {rel} contains no audio")
                continue
            onset = _number(row.get("onset_s", ""), "onset_s", i, problems)
            end = _number(row.get("end_s", ""), "end_s", i, problems)
            if label == NEGATIVE and (onset is not None or end is not None):
                problems.append(f"row {i}: a negative recording cannot have onset_s or end_s")
            if onset is not None and onset >= duration:
                problems.append(f"row {i}: onset_s {onset} is not inside the {duration:.2f} s file")
            if end is not None and onset is not None and end <= onset:
                problems.append(f"row {i}: end_s must be greater than onset_s")
            if end is not None and end > duration + 0.05:
                problems.append(
                    f"row {i}: end_s {end} is past the end of the {duration:.2f} s file"
                )
            split = row.get("split", "")
            if split and split not in SPLITS:
                problems.append(f"row {i}: split must be 'dev' or 'holdout', got {split!r}")
            recs.append(
                Recording(
                    path,
                    rel,
                    label,
                    origin,
                    onset,
                    end,
                    row.get("session", ""),
                    row.get("distance_m", ""),
                    row.get("source", ""),
                    row.get("notes", ""),
                    duration,
                    row.get("source_id", ""),
                    split,
                )
            )
    problems += _split_problems(recs)
    if not recs and not problems:
        problems.append("the manifest has no recordings")
    if problems:
        raise ManifestError(f"{mpath}:\n  " + "\n  ".join(problems))
    return recs


def _split_problems(recs: list[Recording]) -> list[str]:
    """A source group must never be split across development and holdout. If any row declares a
    split, every row must, so no file silently falls outside both sets."""
    if not any(r.split for r in recs):
        return []
    out = [
        f"split is declared for some rows but not for {r.rel!r}: declare it for every row or none"
        for r in recs
        if not r.split
    ]
    out += [
        f"{r.rel!r} has no source_id: when a split is declared every file needs one, so its "
        "group can be kept whole"
        for r in recs
        if not r.source_id
    ]
    by_id: dict[str, set[str]] = {}
    for r in recs:
        if r.source_id and r.split:
            by_id.setdefault(r.source_id, set()).add(r.split)
    out += [
        f"source_id {sid!r} appears in both dev and holdout: a source group must stay whole"
        for sid, splits in sorted(by_id.items())
        if len(splits) > 1
    ]
    return out


def write_manifest_skeleton(directory: Path, name: str = "manifest.csv") -> Path:
    """List audio files that have no manifest row yet, with empty labels for a human to fill in.
    Never guesses a label. Refuses to overwrite an existing manifest."""
    mpath = directory / name
    if mpath.exists():
        raise ManifestError(f"{mpath} already exists, not overwriting it")
    files = sorted(
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.suffix.lower() in AUDIO_SUFFIXES
    )
    with mpath.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
        for f in files:
            w.writerow([f] + [""] * (len(REQUIRED_COLUMNS + OPTIONAL_COLUMNS) - 1))
    return mpath


# ---- configurations -----------------------------------------------------------------------------
@dataclass(frozen=True)
class RunConfig:
    """One named way to run the pipeline. 'production' is the shipped default, unchanged."""

    name: str
    overrides: tuple[str, ...] = ()  # dotted "a.b=value" settings applied to a copy
    use_classifier: bool = True
    experimental: bool = False  # anything other than production is labeled EXPERIMENTAL


PRODUCTION = RunConfig("production")
PRESETS: dict[str, RunConfig] = {
    "production": PRODUCTION,
    "classifier_only": RunConfig("classifier_only", ("gate.enabled=false",), experimental=True),
    "unverified_gate_only": RunConfig(
        "unverified_gate_only", ("experimental.unverified_gate_only=true",), experimental=True
    ),
    "gate_without_classifier": RunConfig(
        "gate_without_classifier",
        ("experimental.unverified_gate_only=true",),
        use_classifier=False,
        experimental=True,
    ),
}


def apply_overrides(cfg: SentinelConfig, overrides: tuple[str, ...] | list[str]) -> SentinelConfig:
    """A validated COPY of the config with dotted overrides applied. The input is not modified."""
    data = copy.deepcopy(cfg.model_dump(mode="json"))
    for item in overrides:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"override must look like section.key=value, got {item!r}")
        node = data
        parts = key.strip().split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                raise ConfigError(f"override {item!r}: no config section {part!r}")
            node = node[part]
        if parts[-1] not in node:
            raise ConfigError(f"override {item!r}: no config key {parts[-1]!r}")
        node[parts[-1]] = yaml.safe_load(value)
    return config_from_dict(data, source="overrides")


def parse_run_configs(names: list[str], sets: list[str]) -> list[RunConfig]:
    """Presets by name, plus one custom config from --set overrides. Production is always first."""
    out: list[RunConfig] = [PRODUCTION]
    for n in names:
        if n not in PRESETS:
            raise ConfigError(f"unknown config {n!r}. Known: {', '.join(sorted(PRESETS))}")
        if PRESETS[n] not in out:
            out.append(PRESETS[n])
    if sets:
        out.append(RunConfig("custom", tuple(sets), experimental=True))
    return out


# ---- running one recording ----------------------------------------------------------------------
class _CaptureDisplay:
    """Stands in for the lens. Records what would have been shown and when, in stream time."""

    def __init__(self) -> None:
        self.pipe: Pipeline | None = None
        self.shown: list[dict[str, Any]] = []

    def capabilities(self) -> DisplayCaps:
        return DisplayCaps(supports_blink=True)

    def show(self, alert: Alert) -> None:
        t = self.pipe.stream_time if self.pipe else 0.0
        self.shown.append(
            {
                "event_id": alert.event_id,
                "alert_class": alert.alert_class.value,
                "line1": alert.line1,
                "line2": alert.line2,
                "tier": alert.tier,
                "t_shown_s": round(t, 3),
            }
        )

    def clear(self, event_id: str | None = None) -> None:
        pass

    def close(self) -> None:
        pass


@dataclass
class RecordingResult:
    file: str
    label: str
    origin: str
    duration_s: float
    config: str
    profile: str
    onset_s: float | None
    # tonal gate and confirmation
    gate_candidate: bool = False
    candidate_count: int = 0
    first_candidate_s: float | None = None
    candidate_fate: str = "none"  # none | confirmed | suppressed | unconfirmed (first candidate)
    candidate_fates: list[str] = field(default_factory=list)
    confirmed_s: float | None = None
    confirmation_latency_s: float | None = None
    # classifier (peak over the recording, from the existing classifier, nothing re-mapped)
    classifier_top: str | None = None
    classifier_peak_top_score: float | None = None
    peak_scores: dict[str, float] = field(default_factory=dict)
    # final decision
    events: list[dict[str, Any]] = field(default_factory=list)
    alerts_shown: list[dict[str, Any]] = field(default_factory=list)
    final_decision: str = "none"  # alert_shown | saved_for_later | none
    outcome: str = ""  # detected | missed | profile_off | clean | false_alert
    miss_reason: str | None = None
    correctly_suppressed: bool = False
    wrong_class_events: list[str] = field(default_factory=list)
    out_of_window_events: list[str] = field(default_factory=list)
    sha256: str = ""
    metadata: dict[str, str] = field(default_factory=dict)  # as supplied in the manifest
    first_alert_s: float | None = None
    detection_latency_s: float | None = None  # needs onset_s
    detect_from_file_start_s: float | None = None
    unverified: bool = False


def _round(v: float | None) -> float | None:
    return None if v is None else round(float(v), 3)


def run_recording(
    rec: Recording,
    audio: np.ndarray,
    cfg: SentinelConfig,
    classifier: Classifier | None,
    run: RunConfig,
    profile_name: str,
    peaks: dict[str, float] | None = None,
) -> RecordingResult:
    """Run one recording through the real pipeline on the virtual clock and score it."""
    profile = ProfileStore.from_config(cfg)
    try:
        profile.set_active(profile_name)
    except ProfileError as err:
        raise ConfigError(str(err)) from err
    display = _CaptureDisplay()
    log = EventLog(":memory:")
    trace: list[dict[str, Any]] = []
    pipe_ref: list[Pipeline] = []
    dets: list[tuple[float, Detection]] = []
    stage = ClassifierStage(classifier, cfg.classifier) if classifier is not None else None
    pipe = Pipeline(
        cfg,
        AudioFileSource(audio, cfg.audio.frame_ms, t0=0.0),
        display,
        eventlog=log,
        classifier=stage,
        source_label="synthetic" if rec.origin == "synthetic" else "replay",
        profile=profile,
        on_detection=lambda d: dets.append((pipe_ref[0].stream_time, d)),
        on_trace=lambda e: trace.append({**e, "t_stream": pipe_ref[0].stream_time}),
    )
    pipe_ref.append(pipe)
    display.pipe = pipe
    pipe.run_sync()

    meta = {
        k: v
        for k, v in (
            ("source_id", rec.source_id),
            ("split", rec.split),
            ("session", rec.session),
            ("distance_m", rec.distance_m),
            ("source", rec.source),
            ("notes", rec.notes),
        )
        if v
    }  # only what the manifest actually said
    res = RecordingResult(
        rec.rel,
        rec.label,
        rec.origin,
        round(rec.duration_s, 3),
        run.name,
        profile_name,
        rec.onset_s,
    )
    res.metadata = meta
    # gate candidates and their fate
    cand_t: float | None = None
    for e in trace:
        if e["kind"] == "candidate":
            res.candidate_count += 1
            cand_t = e["t_stream"]
            if res.first_candidate_s is None:
                res.first_candidate_s = _round(e["t_stream"])
        elif e["kind"] in ("confirmed", "suppressed", "unconfirmed"):
            res.candidate_fates.append(e["kind"])
            if e["kind"] == "confirmed" and cand_t is not None and res.confirmed_s is None:
                res.confirmed_s = _round(e["t_stream"])
                res.confirmation_latency_s = _round(e["t_stream"] - cand_t)
    res.gate_candidate = res.candidate_count > 0
    if res.candidate_fates:
        res.candidate_fate = res.candidate_fates[0]
    elif res.gate_candidate:
        res.candidate_fate = "pending"  # the file ended before the candidate was decided
    # classifier evidence
    obs = [e for e in trace if e["kind"] == "observation"]
    if obs:
        best = max(obs, key=lambda e: e["top_score"])
        res.classifier_top = best["top_class"]
        res.classifier_peak_top_score = _round(best["top_score"])
    res.peak_scores = {k: round(v, 4) for k, v in sorted((peaks or {}).items())}
    # final decision from the event log
    rows = sorted(log.list(limit=1_000_000), key=lambda r: r["t_detect"])
    shown_ids = {s["event_id"] for s in display.shown}
    # Stream time at which each event was created, taken from the pipeline's own trace.
    t_event = {e["event_id"]: e["t_stream"] for e in trace if e["kind"] == "event"}
    for r in rows:
        res.events.append(
            {
                "event_id": r["id"],
                "alert_class": r["alert_class"],
                "stage": r["stage"],
                "score": _round(r["score"]),
                "confirmed": bool(r["confirmed"]),
                "cancel_reason": r["cancel_reason"],
                "level": r["level"],
                "profile": r["profile"],
                "experimental": r["experimental"],
                "shown": r["id"] in shown_ids,
                "source": r["source"],
                "t_detect_s": _round(t_event.get(r["id"])),
            }
        )
    res.alerts_shown = display.shown
    res.unverified = any(e["event_id"] in pipe.unverified_events for e in res.events)
    live = [e for e in res.events if e["cancel_reason"] is None]
    if any(e["shown"] for e in live):
        res.final_decision = "alert_shown"
    elif live:
        res.final_decision = "saved_for_later"
    same = [e for e in live if e["alert_class"] == rec.label]
    target, out_of_window = same, []
    if rec.positive and rec.onset_s is not None:
        # The manifest gave the sound's time. An event of the right class that fires outside it
        # (before the onset, or after the end plus the usual 1 s tail) is not a detection.
        lo = rec.onset_s
        hi = (rec.end_s if rec.end_s is not None else rec.duration_s) + MATCH_TAIL_S
        target = [e for e in same if e["t_detect_s"] is not None and lo <= e["t_detect_s"] <= hi]
        out_of_window = [e for e in same if e not in target]
    res.out_of_window_events = [e["event_id"] for e in out_of_window]
    other = sorted({e["alert_class"] for e in live if e["alert_class"] != rec.label})
    if rec.positive:
        res.wrong_class_events = other
        if target:
            res.outcome = "detected"
            ids = {e["event_id"] for e in target}
            shown_t = [s["t_shown_s"] for s in display.shown if s["event_id"] in ids]
            res.first_alert_s = min(shown_t) if shown_t else None
            times = [e["t_detect_s"] for e in target if e["t_detect_s"] is not None]
            when = res.first_alert_s if res.first_alert_s is not None else min(times, default=None)
            res.detect_from_file_start_s = _round(when)
            if rec.onset_s is not None and when is not None:
                res.detection_latency_s = _round(when - rec.onset_s)
        elif (
            pipe.policy.effective_level(
                AlertClass(rec.label), profile.level_for(AlertClass(rec.label))
            ).value
            == "off"
        ):
            # The wearer's profile turns this sound off, so no event is expected. That is policy,
            # not a detection result, and it stays out of recall.
            res.outcome = "profile_off"
        else:
            res.outcome = "missed"
            fates = set(res.candidate_fates)
            if out_of_window:
                res.miss_reason = "event_outside_labeled_window"
            elif "suppressed" in fates:
                res.miss_reason = "candidate_suppressed_by_classifier"
            elif "unconfirmed" in fates or res.candidate_fate == "pending":
                res.miss_reason = "candidate_never_confirmed"
            else:
                res.miss_reason = "no_gate_candidate_and_no_classifier_detection"
    else:
        res.wrong_class_events = other
        res.outcome = "false_alert" if live else "clean"
        res.correctly_suppressed = not live and any(
            f in ("suppressed", "unconfirmed") for f in res.candidate_fates
        )
    return res


# ---- aggregation --------------------------------------------------------------------------------
def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": len(values),
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }
    # p95 of a handful of values is just the maximum. Only report it with enough samples.
    out["p95"] = round(float(np.percentile(values, 95)), 3) if len(values) >= SMALL_SAMPLE else None
    return out


def _wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """Unrounded 95% Wilson interval. Pass or fail decisions use it, never rounded values."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def wilson_interval(successes: int, n: int) -> list[float] | None:
    """95% Wilson score interval, rounded for display. It describes the units counted, and it
    assumes they are independent samples of the same situations."""
    w = _wilson(successes, n)
    return None if w is None else [round(w[0], 3), round(w[1], 3)]


WILSON_METHOD = "Wilson score interval, two-sided 95% (z = 1.959964), assumes independent units"
POISSON_METHOD = (
    "exact (Garwood) one-sided 95% upper bound for a Poisson rate: chi-square quantile 0.95 with "
    "2(k+1) degrees of freedom, divided by 2 and by the exposure in hours"
)


def poisson_upper_bound(false_alerts: int, hours: float, confidence: float = 0.95) -> float | None:
    """One-sided upper confidence bound on a rate of events per hour, or None without exposure.
    Assumes events arrive independently at a constant rate, the audio is representative of the
    intended use, and no audio is counted twice. An estimate, not proof of the true rate."""
    if hours <= 0:
        return None
    from scipy.stats import chi2

    return float(chi2.ppf(confidence, 2 * (false_alerts + 1)) / 2.0 / hours)


def positive_acceptance(real_positives: list[RecordingResult]) -> dict[str, Any]:
    """Acceptance of the reversing alarm positives. Only real recordings of the sound count.

    Independence: a recording counts toward the independent total only if the manifest gave it a
    source_id, and all files sharing a source_id are ONE unit. A unit is detected only if every
    file in it was detected (the conservative reading). Files with no source_id are listed and not
    counted, because nothing says they are independent.
    """
    candidates = [
        r
        for r in real_positives
        if r.origin == "real" and r.label == "reversing_alarm" and r.outcome != "profile_off"
    ]
    # Identical audio (same SHA-256) is one recording, whatever the manifest says. Without this a
    # file copied under a second source_id would count as a second independent unit.
    seen: dict[str, str] = {}
    eligible, duplicates = [], []
    for r in candidates:
        if r.sha256 and r.sha256 in seen:
            duplicates.append(r.file)
            continue
        if r.sha256:
            seen[r.sha256] = r.file
        eligible.append(r)
    groups: dict[str, list[RecordingResult]] = {}
    unknown: list[RecordingResult] = []
    for r in eligible:
        sid = r.metadata.get("source_id", "")
        (groups.setdefault(sid, []) if sid else unknown).append(r)
    units = len(groups)
    detected_units = sum(1 for g in groups.values() if all(x.outcome == "detected" for x in g))
    w = _wilson(detected_units, units)
    recall = detected_units / units if units else None
    requirements = {
        "independent_units_at_least": ACCEPT_MIN_POSITIVES,
        "observed_recall_at_least": ACCEPT_RECALL,
        "wilson_lower_bound_at_least": ACCEPT_WILSON_LOWER,
    }
    met = {
        "enough_independent_units": units >= ACCEPT_MIN_POSITIVES,
        "observed_recall": recall is not None and recall >= ACCEPT_RECALL,
        "wilson_lower_bound": w is not None and w[0] >= ACCEPT_WILSON_LOWER,
    }
    if not eligible:
        status = "NOT_EVALUATED"
    elif all(met.values()):
        status = "MET"
    else:
        status = "INDICATIVE"
    unmet = []
    if eligible and not met["enough_independent_units"]:
        unmet.append(f"{units} independent unit(s), need at least {ACCEPT_MIN_POSITIVES}")
    if eligible and units and not met["observed_recall"]:
        unmet.append(f"observed recall {recall:.3f}, need at least {ACCEPT_RECALL}")
    if eligible and units and not met["wilson_lower_bound"]:
        unmet.append(f"Wilson lower bound {w[0]:.3f}, need at least {ACCEPT_WILSON_LOWER}")
    multi = [sid for sid, g in groups.items() if len(g) > 1]
    folded: dict[str, set[str]] = {}
    for sid in groups:
        folded.setdefault(sid.casefold(), set()).add(sid)
    lookalikes = sorted(sorted(v) for v in folded.values() if len(v) > 1)
    sessions = {r.metadata.get("session", "") for r in eligible if r.metadata.get("session")}
    return {
        "scope": "real recordings labeled reversing_alarm only (not doorbell, not synthetic)",
        "status": status,
        "requirements": requirements,
        "requirements_met": met,
        "unmet": unmet,
        "eligible_real_files": len(eligible),
        "independent_units": units,
        "detected_units": detected_units,
        "missed_units": units - detected_units,
        "observed_recall": None if recall is None else round(recall, 4),
        "recall_ci95": None if w is None else [round(w[0], 4), round(w[1], 4)],
        "recall_ci_method": WILSON_METHOD,
        "files_without_source_id": [r.file for r in unknown],
        "independence_limitation": (
            None
            if not unknown
            else f"{len(unknown)} file(s) have no source_id, so independence is not established "
            "and they are not counted"
        ),
        "source_ids_with_several_files": sorted(multi),
        "duplicate_files_not_counted": duplicates,
        "source_ids_differing_only_by_case": lookalikes,
        "distinct_sessions_reported": len(sessions),
        "per_recording": [
            {
                "file": r.file,
                "outcome": r.outcome,
                "miss_reason": r.miss_reason,
                "confirmation_latency_s": r.confirmation_latency_s,
                "detection_latency_s": r.detection_latency_s,
                "counted_as_independent": bool(r.metadata.get("source_id")),
                "metadata": r.metadata,
            }
            for r in eligible
        ],
        "note": ACCEPTANCE_NOTE,
    }


def negative_acceptance(real_negatives: list[RecordingResult]) -> dict[str, Any]:
    """Acceptance of false BACKUP ALARM behavior on real negative audio.

    Identical files (same SHA-256) are counted once so the same audio is never exposure twice.
    Whether the audio is representative of intended use cannot be checked here and is not claimed.
    """
    negs = [r for r in real_negatives if r.origin == "real" and r.label == NEGATIVE]
    seen: dict[str, str] = {}
    unique, duplicates = [], []
    for r in negs:
        if r.sha256 and r.sha256 in seen:
            duplicates.append(r.file)
            continue
        if r.sha256:
            seen[r.sha256] = r.file
        unique.append(r)
    seconds = sum(r.duration_s for r in unique)
    hours = seconds / 3600.0
    alerts = [
        (r.file, e["event_id"])
        for r in unique
        for e in r.events
        if e["alert_class"] == "reversing_alarm" and (e["shown"] or e["cancel_reason"] is None)
    ]
    k = len(alerts)
    bound = poisson_upper_bound(k, hours)
    if not unique:
        status = "NOT_EVALUATED"
    elif k > ACCEPT_FALSE_BACKUP_ALARMS:
        status = "NOT_MET"
    elif hours >= ACCEPT_NEG_HOURS:
        status = "MET"
    else:
        status = "INDICATIVE"
    unmet = []
    if unique and k > ACCEPT_FALSE_BACKUP_ALARMS:
        unmet.append(f"{k} false BACKUP ALARM alert(s), need {ACCEPT_FALSE_BACKUP_ALARMS}")
    if unique and hours < ACCEPT_NEG_HOURS:
        unmet.append(f"{hours:.3f} h of negative exposure, need at least {ACCEPT_NEG_HOURS:g} h")
    return {
        "scope": "real recordings labeled negative only",
        "status": status,
        "requirements": {
            "false_backup_alarms": ACCEPT_FALSE_BACKUP_ALARMS,
            "negative_exposure_hours_at_least": ACCEPT_NEG_HOURS,
        },
        "unmet": unmet,
        "negative_recordings": len(negs),
        "unique_recordings_counted": len(unique),
        "duplicate_files_not_counted": duplicates,
        "negative_exposure_seconds": round(seconds, 3),
        "negative_exposure_hours": round(hours, 4),
        "false_backup_alarms": k,
        "false_backup_alarm_events": [{"file": f, "event_id": e} for f, e in alerts],
        "false_backup_alarms_per_hour": round(k / hours, 4) if hours > 0 else None,
        "rate_upper_bound_per_hour_95": None if bound is None else round(bound, 4),
        "rate_upper_bound_method": POISSON_METHOD,
        "rate_upper_bound_assumptions": [
            "false alerts occur independently at a constant rate",
            "the negative audio represents the intended use (NOT verified by this harness)",
            "no audio is counted twice (identical files are detected and not counted again)",
        ],
        "rate_upper_bound_note": "an estimated statistical bound, not proof that the true rate is "
        "zero. With zero false alerts over 30 hours it is about 0.1 per hour.",
        "note": ACCEPTANCE_NOTE,
    }


def summarize(results: list[RecordingResult]) -> dict[str, Any]:
    """Per origin and per class counts, recall, precision, false alerts and timing."""
    out: dict[str, Any] = {}
    for origin in ORIGINS:
        rs = [r for r in results if r.origin == origin]
        pos_all = [r for r in rs if r.label != NEGATIVE]
        pos = [r for r in pos_all if r.outcome != "profile_off"]
        neg = [r for r in rs if r.label == NEGATIVE]
        classes: dict[str, Any] = {}
        for label in sorted({r.label for r in pos_all}):
            mine = [r for r in pos_all if r.label == label]
            off = [r for r in mine if r.outcome == "profile_off"]
            mine = [r for r in mine if r.outcome != "profile_off"]
            det = [r for r in mine if r.outcome == "detected"]
            fp_files = [
                r for r in rs if r.label != label and any(
                    e["alert_class"] == label and e["cancel_reason"] is None for e in r.events
                )
            ]  # fmt: skip
            reasons: dict[str, int] = {}
            for r in mine:
                if r.miss_reason:
                    reasons[r.miss_reason] = reasons.get(r.miss_reason, 0) + 1
            tp, fp = len(det), len(fp_files)
            classes[label] = {
                "profile_off_excluded": len(off),
                "positives": len(mine),
                "detected": tp,
                "missed": len(mine) - tp,
                "miss_reasons": dict(sorted(reasons.items())),
                "recall": round(tp / len(mine), 3) if mine else None,
                "recall_ci95": wilson_interval(tp, len(mine)),
                "recall_ci_method": WILSON_METHOD,
                "false_alert_files": fp,
                "precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
                "indicative_only": len(mine) < SMALL_SAMPLE,
                "detection_latency_s": _stats(
                    [r.detection_latency_s for r in det if r.detection_latency_s is not None]
                ),
                "detect_from_file_start_s": _stats(
                    [
                        r.detect_from_file_start_s
                        for r in det
                        if r.detect_from_file_start_s is not None
                    ]
                ),
                "confirmation_latency_s": _stats(
                    [r.confirmation_latency_s for r in mine if r.confirmation_latency_s is not None]
                ),
            }
        neg_hours = sum(r.duration_s for r in neg) / 3600.0
        false_files = [r for r in neg if r.outcome == "false_alert"]
        # a false alert event is any event raised on a negative recording that was shown or kept
        false_events = sum(
            1 for r in neg for e in r.events if e["shown"] or e["cancel_reason"] is None
        )
        false_backup_neg = sum(
            1
            for r in neg
            for e in r.events
            if e["alert_class"] == "reversing_alarm" and (e["shown"] or e["cancel_reason"] is None)
        )
        false_backup_any = sum(
            1
            for r in rs
            if r.label != "reversing_alarm"
            and any(
                e["alert_class"] == "reversing_alarm" and (e["shown"] or e["cancel_reason"] is None)
                for e in r.events
            )
        )
        wrong_class = [r for r in pos if r.wrong_class_events]
        out[origin] = {
            "recordings": len(rs),
            "positives": len(pos),
            "negatives": len(neg),
            "classes": classes,
            "negative_audio_hours": round(neg_hours, 4),
            "negative_audio_seconds": round(sum(r.duration_s for r in neg), 3),
            "negative_false_alert_files": len(false_files),
            "negative_false_alert_events": false_events,
            "negative_false_alerts_per_hour": (
                round(false_events / neg_hours, 2) if neg_hours > 0 else None
            ),
            "false_backup_alarm_events_on_negatives": false_backup_neg,
            "files_not_labeled_reversing_with_a_backup_alarm": false_backup_any,
            "acceptance": (
                {
                    "positive": positive_acceptance(pos_all),
                    "negative": negative_acceptance(neg),
                }
                if origin == "real"
                else None  # synthetic recordings never count toward acceptance
            ),
            "positive_files_with_wrong_class_alert": len(wrong_class),
            "candidates": {
                "files_with_candidate": sum(1 for r in rs if r.gate_candidate),
                "correctly_suppressed_negatives": sum(1 for r in neg if r.correctly_suppressed),
                "confirmed_on_positives": sum(1 for r in pos if "confirmed" in r.candidate_fates),
                "wrongly_suppressed_positives": sum(
                    1 for r in pos if r.miss_reason == "candidate_suppressed_by_classifier"
                ),
                "never_confirmed_positives": sum(
                    1 for r in pos if r.miss_reason == "candidate_never_confirmed"
                ),
                "false_confirmed_on_negatives": sum(
                    1 for r in neg if "confirmed" in r.candidate_fates
                ),
            },
        }
    return out


def build_report(
    recordings: list[Recording],
    results: dict[str, list[RecordingResult]],
    runs: list[RunConfig],
    cfg_hash: str,
    profile: str,
    directory: Path,
) -> dict[str, Any]:
    """The machine-readable report. Everything under 'configs' is reproducible: stream time only."""
    n_real = sum(1 for r in recordings if r.origin == "real")
    return {
        "schema_version": SCHEMA_VERSION,
        "recordings_dir": str(directory),
        "profile": profile,
        "production_config_hash": cfg_hash,
        "real_recordings": n_real,
        "synthetic_recordings": len(recordings) - n_real,
        "real_audio_evaluated": n_real > 0,
        "time_basis": "audio stream time, seconds from file start; compute and display excluded",
        "time_terms": TIME_TERMS,
        "configs": {
            run.name: {
                "experimental": run.experimental,
                "overrides": list(run.overrides),
                "classifier": run.use_classifier,
                "summary": summarize(results[run.name]),
                "recordings": [asdict(r) for r in results[run.name]],
            }
            for run in runs
        },
    }


def _pct(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{100.0 * n / d:.0f}% ({n}/{d})"


def _ci(ci: list[float] | None) -> str:
    return "n/a" if ci is None else f"[{ci[0]}, {ci[1]}]"


def _lat(s: dict[str, Any]) -> str:
    if not s.get("n"):
        return "n/a"
    p95 = f", p95 {s['p95']}" if s.get("p95") is not None else ", p95 n/a (n<20)"
    return f"median {s['median']} s (min {s['min']}, max {s['max']}{p95}, n={s['n']})"


def render_markdown(report: dict[str, Any]) -> str:
    """Concise human summary. The first lines always say whether real audio was evaluated."""
    lines = ["# Real-audio evaluation", ""]
    if report["real_audio_evaluated"]:
        lines.append(
            f"Real recordings evaluated: {report['real_recordings']} "
            f"(plus {report['synthetic_recordings']} synthetic, reported separately)."
        )
    else:
        lines += [
            "**Real-audio evaluation has NOT been performed.** This run contains "
            f"{report['synthetic_recordings']} synthetic fixture(s) only. Nothing below says "
            "anything about real-world performance.",
        ]
    ov = report["split_overview"]
    lines += [
        "",
        f"Split evaluated: **{report['split_evaluated']}**. {ov['note']}",
        f"Profile: `{report['profile']}`. "
        f"Production config hash: `{report['production_config_hash']}`.",
        f"Time basis: {report['time_basis']}. This is not end to end latency and nothing here "
        "was measured on the glasses.",
        "",
    ]
    for name, block in report["configs"].items():
        tag = "EXPERIMENTAL, not the production behavior" if block["experimental"] else "production"
        lines.append(f"## Config `{name}` ({tag})")
        if block["overrides"]:
            lines.append("Overrides: " + ", ".join(f"`{o}`" for o in block["overrides"]))
        if not block["classifier"]:
            lines.append("No classifier was used in this config.")
        if any(r["unverified"] for r in block["recordings"]):
            lines.append(
                "Some alerts here are UNVERIFIED gate alerts. That is an experimental test "
                "mode and not reliable alarm detection."
            )
        for origin in ORIGINS:
            s = block["summary"][origin]
            lines += ["", f"### {origin.upper()} audio ({s['recordings']} recordings)"]
            if origin == "real" and s["recordings"] == 0:
                lines.append("No real recordings. Not performed.")
                lines += _acceptance_lines(s["acceptance"])
                continue
            for label, c in s["classes"].items():
                note = " (indicative only, fewer than 20)" if c["indicative_only"] else ""
                lines += [
                    f"- **{label}**: recall {_pct(c['detected'], c['positives'])}{note}, "
                    f"95% CI {_ci(c['recall_ci95'])} ({c['recall_ci_method']}), "
                    f"precision {'n/a' if c['precision'] is None else c['precision']}, "
                    f"false-alert files {c['false_alert_files']}",
                    f"  - misses: {c['miss_reasons'] or 'none'}",
                    f"  - detection latency: {_lat(c['detection_latency_s'])}",
                    f"  - from file start: {_lat(c['detect_from_file_start_s'])}",
                    f"  - confirmation latency: {_lat(c['confirmation_latency_s'])}",
                ]
            cand = s["candidates"]
            fa = s["negative_false_alerts_per_hour"]
            lines += [
                f"- negatives: {s['negatives']} files, {s['negative_audio_hours']} h of exposure, "
                f"false alert events {s['negative_false_alert_events']} in "
                f"{s['negative_false_alert_files']} files" + ("" if fa is None else f" ({fa}/h)"),
                f"- false BACKUP ALARM events on negatives: "
                f"{s['false_backup_alarm_events_on_negatives']}",
                f"- gate candidates: correctly suppressed on negatives "
                f"{cand['correctly_suppressed_negatives']}, wrongly suppressed on positives "
                f"{cand['wrongly_suppressed_positives']}, never confirmed on positives "
                f"{cand['never_confirmed_positives']}, confirmed on positives "
                f"{cand['confirmed_on_positives']}, confirmed on negatives "
                f"{cand['false_confirmed_on_negatives']}",
            ]
            lines += _acceptance_lines(s["acceptance"])
        lines.append("")
    return "\n".join(lines)


def _acceptance_lines(acc: dict[str, Any] | None) -> list[str]:
    if acc is None:
        return ["- acceptance: synthetic recordings never count toward acceptance"]
    pos, neg = acc["positive"], acc["negative"]
    lines = [
        f"- **positive acceptance (reversing alarm, real only): {pos['status']}** ({pos['note']})",
        f"  - independent units {pos['independent_units']} of at least "
        f"{pos['requirements']['independent_units_at_least']}, detected {pos['detected_units']}, "
        f"missed {pos['missed_units']}, observed recall {pos['observed_recall']}, "
        f"95% CI {_ci(pos['recall_ci95'])} ({pos['recall_ci_method']})",
    ]
    if pos["duplicate_files_not_counted"]:
        lines.append(f"  - duplicate files not counted: {pos['duplicate_files_not_counted']}")
    if pos["source_ids_differing_only_by_case"]:
        lines.append(
            "  - WARNING: source_ids that differ only by case are counted as different "
            f"groups: {pos['source_ids_differing_only_by_case']}"
        )
    if pos["independence_limitation"]:
        lines.append(f"  - independence limitation: {pos['independence_limitation']}")
    lines += [f"  - not met: {u}" for u in pos["unmet"]]
    rate = neg["false_backup_alarms_per_hour"]
    bound = neg["rate_upper_bound_per_hour_95"]
    lines += [
        f"- **negative acceptance (false BACKUP ALARM, real only): {neg['status']}**",
        f"  - {neg['unique_recordings_counted']} unique negative recordings, "
        f"{neg['negative_exposure_seconds']} s ({neg['negative_exposure_hours']} h) of exposure, "
        f"{neg['false_backup_alarms']} false BACKUP ALARM alert(s)"
        + ("" if rate is None else f", {rate}/h"),
        "  - one-sided 95% upper bound on the rate: "
        + ("n/a (no exposure)" if bound is None else f"{bound}/h")
        + f". Method: {neg['rate_upper_bound_method']}. An estimate, not proof.",
        "  - representativeness of the negative audio is NOT verified by this harness",
    ]
    if neg["duplicate_files_not_counted"]:
        lines.append(f"  - duplicate files not counted: {neg['duplicate_files_not_counted']}")
    lines += [f"  - not met: {u}" for u in neg["unmet"]]
    return lines


def csv_rows(report: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    cols = [
        "config", "file", "origin", "label", "profile", "outcome", "miss_reason",
        "correctly_suppressed", "gate_candidate", "candidate_fate", "first_candidate_s",
        "confirmed_s", "confirmation_latency_s", "final_decision", "first_alert_s",
        "detection_latency_s", "detect_from_file_start_s", "classifier_top",
        "classifier_peak_top_score", "unverified", "source_id", "session",
    ]  # fmt: skip
    rows = [
        [name] + [r[c] if c in r else r["metadata"].get(c, "") for c in cols[1:]]
        for name, block in report["configs"].items()
        for r in block["recordings"]
    ]
    return cols, rows


# ---- driver -------------------------------------------------------------------------------------
def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def run_info(cfg: SentinelConfig) -> dict[str, Any]:
    """What is needed to reproduce a run: the exact config, the model files and the software. Facts
    only. A model file that is missing is reported as missing, not guessed."""
    from sentinel.config import PROJECT_ROOT, resolve_path

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5,
                check=False,
            )  # fmt: skip
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    versions: dict[str, str] = {"python": platform.python_version()}
    for mod in ("numpy", "scipy", "soundfile"):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:  # a missing version string is not worth failing a run
            versions[mod] = "unknown"
    model, class_map = (
        resolve_path(cfg.classifier.model_path),
        resolve_path(cfg.classifier.class_map_path),
    )
    status = git("status", "--porcelain")
    return {
        "config_hash": cfg.config_hash(),
        "config": cfg.model_dump(mode="json"),
        "classifier_model": {
            "path": cfg.classifier.model_path,
            "sha256": _sha256(model),
            "found": model.is_file(),
        },
        "class_map": {
            "path": cfg.classifier.class_map_path,
            "sha256": _sha256(class_map),
            "found": class_map.is_file(),
        },
        "software": versions,
        "git_commit": git("rev-parse", "--short", "HEAD"),
        "git_working_tree_clean": None if status is None else status == "",
        "platform": platform.platform(),
    }


def split_overview(recordings: list[Recording]) -> dict[str, Any]:
    """What the manifest says about development and holdout sets, from facts only. A holdout
    evaluation is possible only if the manifest declares one and it has real reversing alarm
    source groups and real negative audio of its own. It does not create or infer a split."""
    declared = any(r.split for r in recordings)
    per: dict[str, dict[str, Any]] = {}
    for sp in SPLITS:
        mine = [r for r in recordings if r.split == sp and r.origin == "real"]
        ids = {r.source_id for r in mine if r.label == "reversing_alarm" and r.source_id}
        hours = sum(r.duration_s for r in mine if r.label == NEGATIVE) / 3600.0
        per[sp] = {
            "real_files": len(mine),
            "real_reversing_alarm_source_groups": len(ids),
            "real_negative_hours": round(hours, 4),
        }
    ho = per["holdout"]
    possible = (
        declared
        and ho["real_reversing_alarm_source_groups"] > 0
        and (ho["real_negative_hours"] > 0)
    )
    if not declared:
        note = (
            "No split is declared in the manifest, so a holdout evaluation is NOT possible. "
            "Results from this data must not be described as holdout results."
        )
    elif not possible:
        note = (
            "A split is declared but the holdout set lacks real reversing alarm source groups "
            "or real negative audio, so a holdout evaluation is NOT yet possible."
        )
    else:
        note = (
            "A holdout set exists. Evaluate it once, at the end. Tuning on it makes it a "
            "development set."
        )
    return {
        "declared": declared,
        "per_split": per,
        "holdout_evaluation_possible": possible,
        "holdout_could_reach_MET": bool(
            possible
            and ho["real_reversing_alarm_source_groups"] >= ACCEPT_MIN_POSITIVES
            and ho["real_negative_hours"] >= ACCEPT_NEG_HOURS
        ),
        "note": note,
    }


def evaluate(
    directory: Path,
    cfg: SentinelConfig,
    classifier: Classifier | None,
    runs: list[RunConfig] | None = None,
    profile: str | None = None,
    split: str | None = None,
) -> dict[str, Any]:
    """Load the manifest, run every recording under every config, return the report dict."""
    runs = list(runs or [PRODUCTION])
    if runs[0].name != "production":  # production always runs first
        runs = [PRODUCTION, *[r for r in runs if r.name != "production"]]
    recordings = load_manifest(directory, {c.value for c in AlertClass})
    overview = split_overview(recordings)
    if split is not None:
        if split not in SPLITS:
            raise ManifestError(f"split must be 'dev' or 'holdout', got {split!r}")
        if not overview["declared"]:
            raise ManifestError(
                f"--split {split} needs a split column in the manifest. None is declared, so no "
                "development or holdout set exists. Nothing was evaluated."
            )
        recordings = [r for r in recordings if r.split == split]
        if not recordings:
            raise ManifestError(f"the manifest has no recordings in the {split} split")
    profile_name = profile or cfg.profiles.active
    audio = {r.rel: load_wav_16k(r.path) for r in recordings}
    peaks: dict[str, dict[str, float]] = {}
    if classifier is not None:
        for r in recordings:
            _t, per = window_scores(audio[r.rel], classifier, cfg.classifier.hop_s)
            peaks[r.rel] = {c.value: float(v.max()) for c, v in per.items() if len(v)}
    hashes = {r.rel: _sha256(r.path) or "" for r in recordings}
    results: dict[str, list[RecordingResult]] = {}
    switches: dict[str, list[str]] = {}
    for run in runs:
        run_cfg = apply_overrides(cfg, run.overrides) if run.overrides else cfg
        # Run-level provenance lives here, not on events: which experimental switches were on.
        switches[run.name] = sorted(k for k, v in run_cfg.experimental.model_dump().items() if v)
        clf = classifier if run.use_classifier else None
        results[run.name] = []
        for r in recordings:
            res = run_recording(r, audio[r.rel], run_cfg, clf, run, profile_name, peaks.get(r.rel))
            res.sha256 = hashes[r.rel]
            results[run.name].append(res)
    report = build_report(recordings, results, runs, cfg.config_hash(), profile_name, directory)
    for name, on in switches.items():
        report["configs"][name]["experimental_switches_enabled"] = on
    # The primary result is production on real recordings. Experimental configs have their own.
    report["acceptance"] = report["configs"]["production"]["summary"]["real"]["acceptance"]
    report["split_evaluated"] = split or "all"
    report["split_overview"] = overview
    report["run_info"] = run_info(cfg)
    report["inputs"] = [
        {
            "file": r.rel, "label": r.label, "origin": r.origin,
            "duration_s": round(r.duration_s, 3), "sha256": hashes[r.rel], "source_id": r.source_id,
            "split": r.split,
            "onset_s": r.onset_s, "end_s": r.end_s, "session": r.session,
            "distance_m": r.distance_m, "source": r.source, "notes": r.notes,
        }
        for r in recordings
    ]  # fmt: skip
    return report
