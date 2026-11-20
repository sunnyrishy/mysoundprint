# Real-audio evaluation

How to record and label the audio: `docs/REAL_AUDIO_RECORDING_GUIDE.md`.

Purpose: measure what the existing tonal gate, the existing classifier and the confirmation logic actually do on **your own recordings** of a doorbell, a reversing alarm and anything else in the catalog. It adds no classifier, no class mapping and no threshold change. Status: the harness exists and is tested on small synthetic fixtures. **No real recording has been evaluated yet.** Any report without real recordings says so at the top.

## Where recordings go

```
data/real_audio/
  manifest.csv
  doorbell_front_door_01.wav
  reversing_truck_01.flac
  quiet_kitchen_01.wav
  ...
```

- The folder is local and gitignored (`data/*`). Never commit recordings. Nothing is uploaded: the harness makes no network calls.
- The harness only **reads** the files. It does not copy, move, convert in place or edit them, and it writes reports only under `reports/real_audio/` (also gitignored).
- Formats: `.wav`, `.flac`, `.ogg` (whatever `soundfile` reads). Subfolders are fine.
- Preprocessing, exactly this: mix all channels to mono by averaging, resample to 16 kHz if needed, clip to [-1, 1]. No gain, no normalization, no filtering, no noise reduction.

## The manifest

`data/real_audio/manifest.csv`, UTF-8, one row per recording.

| Column | Required | Meaning |
|---|---|---|
| `file` | yes | Path relative to the folder. No `..`, not absolute. |
| `label` | yes | A catalog sound (`doorbell`, `reversing_alarm`, `knock`, ...) or `negative` (the file contains none of the catalog sounds you test for). |
| `origin` | yes | `real` (recorded in the world, or played through a speaker and re-recorded) or `synthetic` (generated). Never guessed. |
| `onset_s` | no | When the sound starts, in seconds from file start. Needed for detection latency. |
| `end_s` | no | When it ends. |
| `split` | no | `dev` or `holdout`, for a whole source group. If any row has one, every row needs one and a `source_id`. A group in both sets is rejected. See the recording guide. |
| `source_id` | no, but needed for acceptance | Same id means the same underlying sound event, take or source, so those files are one independent unit. Never filled in for you. |
| `session`, `distance_m`, `source`, `notes` | no | Your own bookkeeping. `source` should record the license for anything not recorded by you. |

Run `python tools/eval_real.py --init-manifest` to list the audio files with empty label and origin cells. It never guesses a label from a file name and never overwrites an existing manifest. Every manifest problem is reported at once and nothing is evaluated until the manifest is valid.

Several recordings per category are expected. To add a category that the catalog already has (for example `knock`), add rows. A sound that is **not** in the catalog is rejected on purpose: adding catalog sounds is a product decision, not a harness change.

Use `negative` recordings of ordinary life (speech, music, kitchen, traffic, tools, TV). Without them, false alerts and correct suppression cannot be measured.

## Running it

```bash
python tools/eval_real.py                      # production behavior, active profile
python tools/eval_real.py --profile focus      # under another profile
python tools/eval_real.py --compare classifier_only unverified_gate_only
python tools/eval_real.py --set gate.confirm_timeout_s=2.4   # custom EXPERIMENTAL config
```

Output in `reports/real_audio/eval-<timestamp>.{json,csv,md}`: JSON (everything), CSV (one row per recording and config), Markdown (summary). Production is always run first. Every other config is labeled EXPERIMENTAL in the report and never changes the shipped defaults. Presets: `classifier_only` (gate off), `unverified_gate_only` (gate alerts alone, labeled UNVERIFIED), `gate_without_classifier`. `--set` takes dotted overrides applied to a validated copy of the config.

## What is recorded per recording

The tonal gate candidate (count, first time, fate of each: `confirmed`, `suppressed` by a contradicting classifier, `unconfirmed` after the 1.6 s timeout), the classifier's evidence (the top class and score, and the peak score of every catalog class), the events the pipeline raised (class, stage, level, profile, whether it showed), the final decision (`alert_shown`, `saved_for_later`, `none`), the selected profile, and the timings below.

## Outcomes, and why miss and suppression differ

| Recording | Outcome | Meaning |
|---|---|---|
| positive | `detected` | An event of the labeled class was raised |
| positive | `missed` + reason | `candidate_suppressed_by_classifier` (gate saw it, classifier contradicted it), `candidate_never_confirmed` (gate saw it, nothing confirmed it), `no_gate_candidate_and_no_classifier_detection` |
| positive | `profile_off` | The selected profile turns the sound off. A policy result, kept out of recall and counted separately |
| negative | `clean` | No event. If a candidate was suppressed or unconfirmed, `correctly_suppressed` is true |
| negative | `false_alert` | An event was raised |

A suppressed candidate is **correct** on a negative recording and a **miss** on a positive one. The same logic that protects a baby cry recording would hide a real reversing alarm, which is why the report keeps the two apart. A positive file that also raises an alert of a different class is listed under `wrong_class_events`, and counts as a false alert for that other class.

## Acceptance (reversing alarm, provisional engineering criteria, not safety claims)

Computed from **real** recordings only, from the production config (the first and primary result). Experimental configs get their own labeled copy. Synthetic recordings never count. Doorbell and other classes are never combined with reversing alarms.

**Positive acceptance** (real recordings labeled `reversing_alarm`):

| Status | Meaning |
|---|---|
| `NOT_EVALUATED` | no eligible real reversing-alarm recordings |
| `INDICATIVE` | real recordings exist, but a requirement below is not met |
| `MET` | all three: at least 100 independent units, observed recall at least 95%, 95% Wilson lower bound at least 95% |

99 detections out of 100 has a Wilson lower bound of about 94.6%, so it is `INDICATIVE`, not `MET`. Only 100 of 100 (lower bound about 96.3%) passes at that size. The decision uses the unrounded bound.

**Independence.** 100 files are not 100 independent recordings. Add a `source_id` column to the manifest: files with the same `source_id` come from the same underlying sound event, take or source and form **one unit**. A unit is detected only if every file in it was detected (the conservative reading). A file with no `source_id` is listed under `files_without_source_id`, is **not** counted, and the report states the independence limitation. The harness never invents a `source_id`. It also reports how many distinct sessions were supplied. It cannot check that a `source_id` was assigned honestly. That is on whoever makes the manifest.

**Negative acceptance** (real recordings labeled `negative`):

| Status | Meaning |
|---|---|
| `NOT_EVALUATED` | no eligible real negative recordings |
| `INDICATIVE` | zero false BACKUP ALARM alerts, but under 30 h of exposure |
| `MET` | zero false BACKUP ALARM alerts and at least 30 h of unique real negative exposure |
| `NOT_MET` | one or more false BACKUP ALARM alerts, at any exposure. The actual count and rate are always printed |

A false BACKUP ALARM alert is a `reversing_alarm` event on a negative recording that was shown or kept. Identical files (same SHA-256) count once. Whether the negative audio is representative of intended use is **not** verified by the harness and is not claimed.

**Statistics.**
- Recall interval: Wilson score interval, two-sided 95% (z = 1.959964), computed over independent units. It assumes the units are independent samples of the same situations.
- False-alert rate: alerts divided by exposure hours. Upper bound: exact (Garwood) one-sided 95% bound for a Poisson rate, chi-square quantile 0.95 with 2(k+1) degrees of freedom, divided by 2 and by hours. With zero alerts over 30 h it is about 0.1 per hour. It assumes independent alerts at a constant rate, representative audio, and no double counting. It is an estimated statistical bound, **not proof** that the true rate is zero. With no exposure it is not reported.

## Provisional targets (reversing alarm)

Goals to test against, **not safety claims**: recall at least 95%, zero false BACKUP ALARM events in the negative set, at least 100 independently recorded positives before the recall number means anything, and the negative exposure time reported. The report prints a status for each origin: `not_applicable_synthetic_audio`, `no_data`, `indicative_only_fewer_than_100_positives`, `cannot_assess_no_negative_recordings`, `met_on_this_set_provisional` or `not_met`. "Met" needs at least 100 real positives, recall of 0.95 or more, no false BACKUP ALARM on any negative and some negative exposure, and it is still only "on this set". The report also says whether the lower end of the recall interval clears 0.95. Nothing about thresholds or confirmation was changed to move these numbers.

## Metrics

Per origin (real and synthetic are **never** combined) and per class: positives, detected, missed with reasons, recall with a 95% **Wilson score interval** (it assumes the recordings are independent samples, so many takes of one recording inflate confidence), precision (file level: detected over detected plus files of other labels that raised this class), false-alert files, false alert events, the negative exposure in seconds and hours, and false alerts per hour (alert events on negative recordings divided by negative hours; no rate is given without negative audio). Also counted: false BACKUP ALARM events on negatives, and files not labeled reversing that raised one. Confirmation time is reported as min, median and max, and p95 only with 20 or more values. Classes with fewer than 20 positives are marked "indicative only". A p95 appears only with 20 or more samples. Precision and recall are `n/a` when the denominator is zero. Nothing is extrapolated.

## Kinds of time (the report lists them under `time_terms`)

| Term | Status |
|---|---|
| Audio-stream detection time | measured here |
| Audio-stream confirmation time | measured here |
| Evaluation processing time | not recorded |
| Software display-adapter call time | not in this report: measured only by `tools/hw_proof.py` and `tools/hw_smoke_test.py` |
| Device acknowledgement time | UNKNOWN: no adapter or SDK reports one |
| Visible display time | not measured, needs the actual glasses |
| End-to-end latency | not measured |

## Timing: method and limits

All times are **audio stream time** in seconds from the start of the file: the end of the audio frame (32 ms) in which the decision happened, and the classifier only decides every 250 ms. Compute time and display time are excluded, which makes runs reproducible and means this is **not** end to end latency, and says nothing about time to photons on the glasses.

- `detection_latency_s` = first shown alert minus `onset_s`. Only when the manifest gives `onset_s`. Otherwise `detect_from_file_start_s` is shown and is not called latency.
- `confirmation_latency_s` = time the classifier confirmed a gate candidate minus the time the candidate appeared. Only for confirmed candidates.

## Splits and duplicates

`python tools/eval_real.py --split dev` or `--split holdout` evaluates one set. Without a declared split the report says a holdout evaluation is NOT possible. Files with identical audio (same SHA-256) are counted once for positives and negatives. `source_id` values that differ only by case are counted as separate groups and flagged in the report.

## Labeled windows

If the manifest gives `onset_s` (and optionally `end_s`), an event of the right class only counts as a detection if it fires between `onset_s` and `end_s` (or the end of the file) plus a 1 s tail, the same rule as the synthetic evaluator. Otherwise the recording is a miss with reason `event_outside_labeled_window`. Without `onset_s` any event in the file counts, and no latency is reported.

## Reproducing a run

Each report holds `run_info` (the full config and its hash, the classifier model and class map paths with SHA-256, Python, numpy, scipy and soundfile versions, the git commit and whether the tree was clean, the platform) and `inputs` (every recording with its SHA-256, duration and manifest row). Re-running on the same files, model and config gives the same JSON. Missing facts are reported as missing, never filled in.

## Experimental settings

- `experimental.unverified_gate_only` (default `false`): a tonal gate hit alerts alone, with line 2 replaced by `UNVERIFIED`. For display timing with synthetic tones and gate experiments. **Not reliable alarm detection.** It is separate from the normal sections of the config and is never on in `config/default.yaml`. The demo, replay and live tools turn it on only with `--experimental-unverified-gate-only`, and the latency probe refuses to run without it.
- With no classifier and the switch off, a tonal gate hit never shows `BACKUP ALARM`.
- An event has `experimental = true` only if the decision that created it was experimental: today, a BACKUP ALARM created through the unverified gate-only path (D69). A normal classifier event in a run with the switch on is `false`. The marker is set where the gate-only detection is delivered, never from the sound, the profile or the run's config. Which switches were on for a whole run is recorded once per config in the report as `experimental_switches_enabled`.

## Limitations

- The synthetic fixtures in the tests prove the bookkeeping, not recognition quality.
- Speaker-played recordings are not the same as the sound in the world. Say so in the pitch.
- Results on a handful of files are anecdotes. The report marks them as such.
