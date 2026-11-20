# Recording guide for the real-audio evaluation

How to collect and label recordings so that `tools/eval_real.py` gives evidence you can defend. Read `docs/REAL_AUDIO_EVAL.md` for what the harness reports. Recordings stay local in `data/real_audio/` (gitignored). Never commit them, upload them or copy them elsewhere. Status today: **no real recording has been supplied, so real-audio evaluation has NOT been performed.**

The harness only counts what the manifest says. It cannot check that a label or a `source_id` is honest, so the honesty is yours.

## 1. Reversing-alarm positives

One file is one **test trial**: a complete, meaningful recording of one reversing alarm sounding, with the lead-in and tail around it (a few seconds of ordinary sound before the alarm starts and after it stops). Not a fragment, not a clip cut from the middle of something longer.

For each trial:
- **Source.** Record which physical alarm it was (vehicle make and model, or the speaker and file if you played a recording through a speaker, and say so: "speaker-played" is a different thing from a vehicle alarm).
- **Session.** One session is one continuous stretch of recording at one place and time. Name it (`session` column).
- **Environment and distance.** Where (garage, street, yard) and the approximate distance in meters, when you know them. Leave blank when you do not.
- **Level.** Avoid clipping (no flat-topped waveform, no red meter). The alarm must be clearly audible on the recording. Listen to it. A trial where you cannot hear the alarm is not a positive.
- **Timing.** If you know when the alarm starts and ends, fill `onset_s` and `end_s` (seconds from the start of the file). With `onset_s` the harness only counts an alert that falls inside that window, and can report detection time from onset. Never guess.
- **Independence.** Do **not** cut one continuous recording into many files and treat them as many examples. They are one example. Repeated recordings of the same alarm in the same session are also one example for acceptance purposes.

### Filling `source_id`

`source_id` is a grouping label you declare. It is **not** automatic proof of statistical independence. The harness trusts it.

Rules, at minimum:
1. Every file from the same **continuous session** shares one `source_id`.
2. Files from the **same physical alarm source** that are not meaningfully independent (same vehicle, same day, same spot) share one `source_id` too.
3. Use a different `source_id` only for a different source: a different vehicle, a different alarm unit, or the same unit at a clearly different time and place.
4. When in doubt, group. Over-grouping lowers the count of independent units, which is the safe direction. Under-grouping inflates it.
5. IDs are case sensitive: `TruckA` and `trucka` are two groups. The report warns when it sees IDs that differ only by case.

A `source_id` can cover files with different labels (one session can hold a positive stretch and a negative stretch). Acceptance only groups within the same label and the same origin.

### The all-files-detected rule

- A source group counts as **detected** only when **every eligible file** in that group is detected.
- A group with **any** missed file counts as a **missed positive unit**.
- Files **without** `source_id` are reported in `files_without_source_id` but **do not count** toward independent positive units.
- Files with identical audio (same SHA-256) are one recording and are counted once, whatever the manifest says.

This is deliberately conservative. A group's recall is the recall of the weakest file in it.

## 2. Ordinary negative recordings

Negatives are ordinary audio with **no** reversing alarm. They measure false BACKUP ALARM alerts. Label them `negative`, origin `real`. Collect **diverse** exposure, not one long recording of one room:

- Ordinary beeps and electronic tones (microwave, appliance chimes, phone and app notifications, timers, car door chime, seat belt chime, fridge door alarm).
- Appliances (washing machine, vacuum cleaner, kettle, fan, dishwasher, power tools).
- Voices and conversation (one person, a group, a TV, a call, a baby, laughing).
- Traffic and outdoor noise (road, horns, sirens, bicycles, construction, wind, rain).
- Music (several genres, including music with sustained or pulsed tones).
- Other alarms (fire and smoke alarms, car alarms, alarm clocks) that are **not** reversing alarms.
- Quiet and ordinary environments (silence, an office, a bedroom at night, a kitchen).

Put each distinct environment under its own `source_id` (for example `kitchen_day1`, `street_rush_hour`). Do not pile up many hours from one nearly identical environment and call it representative: the exposure number would grow while the diversity stayed the same. Prefer many different places and situations over one long take.

**30 hours is an initial exposure target, not proof of universal false-alert performance.** Zero false alerts in 30 hours gives an estimated one-sided 95% upper bound of about 0.1 false alerts per hour, and only for audio like what was recorded. The harness cannot verify that your negative audio represents real use, and says so in every report.

## 3. Doorbell recordings (separate)

Doorbell recordings are labeled `doorbell` and are evaluated **separately**. They never count toward reversing-alarm acceptance. The same trial rules apply (complete trial, session, source, environment, no clipping, audible). A doorbell positive is a real doorbell or chime recorded in a real place, or a speaker-played recording labeled as such. There is no acceptance threshold for doorbells yet. The report shows counts, recall with a Wilson interval, and misses, marked indicative when the sample is small.

## 4. The manifest

`data/real_audio/manifest.csv`, one row per file. Required: `file,label,origin`. Optional: `onset_s,end_s,session,distance_m,source,source_id,split,notes`. `python tools/eval_real.py --init-manifest` lists your files with empty cells. It never guesses a label or an ID. Leave a cell empty rather than inventing a value: empty means unknown, and the report shows it as unknown.

Origin is `real` for sound recorded in the world (including a speaker-played recording that you re-recorded, noted in `notes`) and `synthetic` for generated audio. Synthetic files never count toward acceptance and are reported separately.

The harness rejects, with a message naming the row: a missing file, an unreadable or zero-length file, an unknown or empty label, an origin other than real or synthetic, a negative, non-numeric or out-of-range `onset_s` or `end_s`, a duplicate row, and a file outside the folder. It reports without repairing: missing `source_id` (not counted), repeated `source_id` (one unit), duplicate audio by SHA-256 (counted once), `source_id` values that differ only by case.

## 5. Development and holdout sets

Keep an untouched holdout set so the final number is not tuned to the data used to look at failures.

1. When you have enough recordings, assign **whole `source_id` groups** to `dev` or `holdout` in the `split` column. Never put files of one group in both. The manifest is rejected if you do.
2. If any row declares a `split`, **every** row must declare one and have a `source_id`, so no file escapes both sets.
3. Use `dev` for diagnosis: inspect misses and false alerts, understand the failures. If tuning is ever approved, tune on `dev` only. This milestone changes no threshold or logic.
4. Keep `holdout` for the final evaluation. Run it once, at the end: `python tools/eval_real.py --split holdout`. Do not look at its failures and then change things to fix them. If you do, it has become a development set and you need a new holdout.
5. Do not create fake holdout recordings. If there are not enough real recordings, a holdout evaluation is **not possible yet**. Every report states this in `split_overview`. A holdout can only reach `MET` if it alone has at least 100 independent reversing-alarm groups and 30 hours of negatives.

Use `python tools/eval_real.py --split dev` for the development set.

## 5a. First hackathon collection (a diagnostic target)

A manageable first set to learn how the system behaves, **not** a replacement for the acceptance criteria below:

- 10 to 20 independent real reversing-alarm source groups.
- 30 to 60 minutes of diverse ordinary negative audio.
- 5 to 10 separate doorbell trials.

A set this small is preliminary and indicative only. It cannot reach `MET` (that needs at least 100 independent positive source groups and 30 hours of representative negative exposure), and it must not be described as safety validation or as a recall or false-alert rate. Use it to find failures (for example whether the classifier ranks another beep above reversing beeps) and to practice the manifest and the `source_id` grouping. With this few recordings a holdout evaluation is not yet possible, so do not claim one.

## 6. Acceptance (unchanged from Milestone E)

Provisional engineering criteria, not safety claims. Real recordings only. Production config first.
- **Positive (reversing alarm):** `NOT_EVALUATED` (no eligible real positives), `INDICATIVE` (fewer than 100 independent units, or recall or the unrounded Wilson 95% lower bound below 95%), `MET` (at least 100 independent units, observed recall at least 95%, and Wilson lower bound at least 95%).
- **Negative:** `NOT_EVALUATED`, `INDICATIVE` (zero false BACKUP ALARMs, under 30 hours), `MET` (zero and at least 30 hours), `NOT_MET` (any false BACKUP ALARM).
- Rate bound: exact one-sided 95% Poisson (Garwood) upper bound, with assumptions listed in the report.

## 7. When you have recordings

1. Fill the manifest and check it loads (`python tools/eval_real.py` stops with every problem listed if not).
2. Count independent source groups and negative hours in the report before reading any recall number.
3. Run production first, then experimental comparisons if wanted (labeled EXPERIMENTAL).
4. Open every missed positive and every false BACKUP ALARM and look at the gate fate and the classifier's top class.
5. Do not change the classifier, thresholds or confirmation to improve the numbers. Report the evidence and let the product owner decide.
