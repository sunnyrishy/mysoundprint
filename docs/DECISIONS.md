# Decisions

Every choice the spec left open. Format: what, why, date.

## D1. SPEC.md is a transcription of the PDF (Nov 13)
The brief arrived as a PDF, not `SPEC.md`. `docs/SPEC.md` is a hand transcription. The PDF wins if they differ.

## D2. Config paths resolve against the project root (Nov 13)
Relative paths in the YAML (`models/...`, `data/...`) resolve from the project root, not the cwd, so tools work from any directory.

## D3. `python -m sentinel` is a thin CLI (Nov 13)
It has `--help`, `--version` and `check-config`. Real work lives in `tools/*.py` as the spec lays out.

## D4. Build backend is hatchling, venv is `.venv` via uv (Nov 13)
Plain `pip install -e ".[dev]"` also works.

## D5. Git is local only (Nov 13)
Commits on the working branch. Nothing is pushed, per the operator rules.

## D6. Gate emits only reversing_alarm (Nov 13)
The tonal gate targets pulsed backup alarms, so every gate detection has class `reversing_alarm`. Horns and fire alarms come from the classifier.

## D7. Gate needs a rising edge (Nov 13)
A burst only counts if at least 2 non-tonal frames came before it. A tone already on at the start of a stream is ignored, which keeps the gate silent on a continuous tone. Side effect: an alarm whose first 2 frames are at the very start of a file loses its first pulse.

## D8. Confirmation needs the same frequency (Nov 13)
The spec confirms on "a second valid burst" 0.6 to 1.5 s after the first. It does not say the frequency must match. I require the two bursts to be within `freq_tolerance_hz`, which rejects unrelated sounds that happen to be spaced right. Revisit if real alarms alternate between two tones.

## D9. Known limit: a continuous tone that starts mid-stream (Nov 13)
The spec fires a provisional detection at `burst_min_s` (0.25 s), before the gate can know the tone outlasts `burst_max_s`. So a tone that starts mid-stream raises one provisional alert, which policy cancels on timeout and counts as a shown-but-false alert. The gate never confirms it. The same happens at the turning points of a slow siren wail. Tests cover both: provisional is allowed, confirmation is not.

## D10. Gate time model (Nov 13)
`gate.tonal.process` is pure. It maps sample positions to times from the `t_end` of each block. `t_detect` is the end of the analysis frame that fired. The pipeline overwrites `t_detect` with the wall clock so compute time counts in latency.

## D11. Scenario module and extra event kinds (Nov 13)
`sim/scenario.py` (not in the spec layout) loads and renders scenario YAML. Besides `tonal_alarm`, `broadband_alarm`, `horn`, `fire_alarm_t3` and `clip`, I added decoy kinds `tone`, `beep` and `click` with `label: null`, used for negative tests. A scenario with `clip` events must state `origin: real` or `origin: synthetic`, so reports never guess.

## D12. Burst length and onset (Nov 13)
Burst length is (last tonal frame - first tonal frame + 1) * hop. Onset is the start of the first tonal frame, which can sit up to a window (64 ms) before the true onset.

## D13. Two pipeline modes (Nov 13)
`Pipeline.run()` is threaded and wall-clock (live mic, real-time replay): reader, gate worker, policy worker and a display thread, all connected by queues. `Pipeline.run_sync()` runs the same stage objects on one thread with a virtual clock (`StreamClock`: last frame time plus compute elapsed). Tests and `tools/evaluate.py` use sync mode so results are deterministic and fast. In sync mode latency is virtual stream time plus real compute time.

## D14. Display isolation (Nov 13)
`AsyncDisplay` runs every `show` and `clear` on a helper thread with a timeout (default 1 s). A call that times out is abandoned (daemon thread), counted in metrics, and the next command proceeds. If the command queue fills, the oldest command is dropped. Blink is emulated with show and clear cycles when `supports_blink` is false.

## D15. Microphone path is unverified here (Nov 13)
The build container has no microphone and no PortAudio. `AudioMicSource` is tested with a fake stream (16 kHz, native-rate fallback with resampling, overflow counting, open failure message). The first real-microphone check is a manual step listed in the README checklist. Input hardware latency is not included in software latency.

## D16. Mic opens at 16 kHz, falls back to native rate (Nov 13)
Opening at 16 kHz lets the OS or PortAudio resample. If that fails, the device default rate is used with `StreamResampler` (6th order Butterworth low-pass plus linear interpolation, stateful across blocks).

## D17. Interim alert handling in pipeline (Nov 13)
M2 uses a minimal show-once-per-cooldown handler inside `pipeline.py` so the console path works. M4 replaces it with `policy.py` as the spec describes.

## D18. Classifier onset estimate and stream position (Nov 13)
YAMNet needs a 0.975 s window, so a classifier detection cannot say exactly when the sound began. The onset estimate is the middle of the first window of the current positive streak. Estimated onsets are only used for the event log columns; `tools/evaluate.py` measures alarm-to-display latency against the scenario's ground-truth onset instead. The stage counts samples itself and reads the window that ends at the frame it is processing (`RingBuffer.window`), so results do not depend on how far ahead the capture thread has run.

## D19. Model source (Nov 13)
tfhub.dev and Kaggle were not reachable from the build environment. The model is Google's MediaPipe-hosted YAMNet float32 TFLite (classification variant, 521 classes), the class map is from `tensorflow/models`. URLs and SHA-256 are pinned in `tools/fetch_models.py` and listed in `models/README.md`. Input and output shapes were verified from the file.

## D20. Smoke detector class added to fire_alarm (Nov 13)
The spec says to consider it. `Smoke detector, smoke alarm` exists in the class map (index 393), so `fire_alarm.names` in `config/default.yaml` now lists it with `Fire alarm`. Remove it if it causes false alerts on real negatives.

## D21. Classifier fires on every qualifying hop (Nov 13)
While the hits-of-windows condition holds, the stage emits a Detection on every hop. The policy treats repeats as TTL extensions, so no extra alerts appear.

## D22. Policy edge cases the spec leaves open (Nov 13)
- **Lower priority while a higher alert is on screen.** The event is logged immediately with `cancel_reason='superseded'`, never shown, and its class enters cooldown.
- **Replacement.** When a higher priority alert replaces a shown one, the replaced event gets `cancel_reason='superseded'` even if it was confirmed, and its class enters cooldown. The display receives one `show` that replaces the old alert, not a clear first.
- **Cooldown start.** Cooldown starts when the alert clears (TTL end, timeout, or replacement), measured from that moment, not from the first detection. A cancelled provisional also starts cooldown, so a flapping source cannot retrigger every second.
- **Stage label.** Union of the stages that contributed while the event was active: `gate`, `classifier` or `both`. It can upgrade from `gate` to `both` after confirmation.
- **A non-provisional detection with no active alert** (classifier, or a gate pulse that arrives after cooldown) creates a confirmed event and shows the alert.
- **TTL shorter than the confirm timer** still ends an unconfirmed alert as `timeout`.

## D23. Event ids (Nov 13)
Ids use the spec format `e_000123` and continue from the highest stored id, so a database shared across runs never collides.

## D24. End of a source (Nov 13)
When a replay source ends on its own, the pipeline fast-forwards the policy clock by `max(confirm_timeout_s, ttl_s)` so every provisional alert ends as confirmed or cancelled in the log. On Ctrl+C it does not, and it clears the display instead, so an interrupted run never invents a `timeout`.

## D25. Log writes are asynchronous (Nov 13)
Inserts and updates go through `AsyncEventLog` on their own thread, in order, so the policy worker never waits for SQLite. Reads (dashboard) use the locked connection directly. `Pipeline` flushes the writer at the end of a run.

## D26. Event log defaults (Nov 13)
`run_replay` and `run_live` write to `eventlog.path` from the config. Replays are tagged `source=synthetic` or `source=replay`, never `live`. Use `--db PATH` for a throwaway database.

## D27. Who emits which WebSocket message (Nov 13)
The display adapter (`display_sim.py`, later `display_bridge.py`) emits `alert` and `clear`. The pipeline emits `event` messages (create, confirm, cancel, latencies, acknowledge) through listeners. The dashboard server adds `stats` every 2 s. This keeps the architecture rule that the display adapter is the only thing that draws, so a phone bridge receives exactly what the glasses would.

## D28. Dashboard runs inside the tool process (Nov 13)
`tools/run_live.py` and `tools/run_replay.py` start uvicorn on a background thread (`sentinel.runtime.start_runtime`) when a `sim` or `bridge` display is chosen, or with `--serve`. The default config keeps `display.adapter: console` as in the spec; use `--sim` (adds the simulator to the display list) or `--display console,sim`. `--serve` implies real-time pacing so the browser timers match the audio.

## D29. Simulator behaviour (Nov 13)
- The simulator blinks natively, so `SimDisplay.capabilities().supports_blink` is always true and the pipeline does not emulate blink for it.
- Clicking an alert acknowledges the event but does not clear it. It stays until `clear` or its `ttl_ms`.
- A page opened mid-alert receives the active alert with its remaining time.
- The `RECORDING` badge is driven by `GET /api/status` (`source` is not `live`). The same value is stored in every event's `source` column.
- `GET /api/per_hour` and `GET /api/events.csv` are additions to the endpoints in the spec, for the near-miss view and CSV export.
- Alert text is drawn at 12 characters per line (the assumed display size) and never wraps.

## D30. Browser check (Nov 13)
The page was driven with the preinstalled Chromium against a live replay: alert text, RECORDING badge, row update, click to acknowledge, kiosk layout and no console errors. This is a manual check, not part of the pytest suite.

## D31. Evaluation library and test-set library (Nov 13)
Logic lives in `src/sentinel/evaluation.py` and `src/sentinel/testset.py` so it can be unit tested; `tools/evaluate.py` and `tools/make_test_set.py` are thin command line wrappers. These two modules are not in the spec's repo layout.

## D32. Scoring details (Nov 13)
- "Detected" uses the spec rule on `Detection` objects: a non-provisional detection of the right class with `t_detect` in [start, end + 1.0 s] of the scenario's ground-truth event.
- "Shown" means the display's `show` returned (the event has a `t_display`). A lower priority event that was logged but never shown is not an alert and is never a false alert.
- An alert is matched to an event if its class equals the event label and its `t_detect` falls in the same window. An alert of the wrong class at the right time is a false alert.
- Alarm-to-display latency in reports is measured from the scenario's ground-truth onset to the first display. The estimated-onset figure that the event log stores is shown next to it.
- Latency groups: tonal path means the first alert came from the gate (stage `gate` or `both`); classifier only means stage `classifier`.
- False alerts per hour are reported for negative-only scenarios (the spec's R3 test) and, separately, for time outside labelled events in positive scenarios.
- CPU is process CPU time divided by audio duration, measured with the pipeline running as fast as possible on one thread.

## D33. One report per audio origin (Nov 13)
`tools/evaluate.py` never mixes synthetic and real scenarios in one table. It writes one report per origin, named `<stem>_synthetic.md` or `<stem>_real.md`, each with a banner at the top. The "Goals from the spec" table only says "meets goal" or "below goal" for real audio; for synthetic audio every row says "n/a (synthetic, not a claim)".

## D34. Noise levels are signal-to-noise settings (Nov 13)
The spec's easy, loud and very loud noise levels map to a signal-to-noise ratio per scenario (`easy=10, loud=5, very_loud=0` dB by default, with the noise bed fixed at -30 dBFS). Scenarios carry a `level` tag and a `split` (`dev` or `heldout`). A held-out session is chosen with `--heldout-session`, matched against the manifest's `session` column.

## D35. Threshold sweep approximations (Nov 13)
The sweep reads class scores at exact hop boundaries and applies the stage's hits-of-windows smoothing offline, per class and threshold. Precision is the share of detection episodes that overlap an event of that class (event window plus 1.0 s). Recall is the share of events with a detection inside their window. This can differ slightly from a live run because the stage aligns to 32 ms frame boundaries.

## D36. Python is fast enough (Nov 13)
A full synthetic evaluation (55 scenarios, 2.3 hours of audio, gate and classifier) runs in 80 s and costs about 0.9% of one core. Gate processing latency is about 0.1 ms per event. No profiling or C++ port is needed on the laptop. This says nothing about a phone or other target device.

## D37. Bridge adapter vs simulator adapter (Nov 13)
Both publish the spec's `alert` and `clear` messages through the dashboard WebSocket hub, so the browser page and a phone bridge subscribe to the same `/ws`. The difference is capabilities: the simulator adapter blinks natively (`supports_blink` always true), while the bridge adapter uses `display.caps` exactly as configured. With the default caps (no native blink) the pipeline emulates blinking with show and clear cycles and the bridge payload says `blink: false`, so a subscriber never blinks twice. Use one of them at a time. When the bridge is active the browser simulator shows exactly what the bridge sends.

## D38. Console display never emulates blink (Nov 13)
`make_display` forces `supports_blink=True` for the console adapter. A text terminal cannot blink, and emulating it printed a line every half second.

## D39. Stub behaviour (Nov 13)
`AudioGlassesSource` and `MicroLuminDisplay` raise `NotImplementedError` with a message that says what to use instead and points at `docs/SDK_NOTES.md`. They are placed behind `--input glasses` and `--display microlumin`. The tools turn that exception into a one-line message and exit code 2, never a traceback. `hw_smoke_test.py` is the exception: with a stub it prints `SKIPPED: display adapter 'microlumin' is not implemented.` and exits 0, because the spec says it must exit cleanly.

## D40. Latency probe design (Nov 13)
`tools/latency_probe.py` plays a known tonal burst on a fixed schedule (1 s of silence, then bursts 3 s long at least 8 s apart) and prints wall-clock times for sound start, `show` called, `show` returned and `clear` called, plus the software alarm-to-display from the known sound start. `--mode simulate` feeds the probe straight into the pipeline (no hardware, software path only). `--mode live` plays it through the speaker with `sounddevice` while the microphone listens. True end-to-end latency to photons is read from slow-motion video, with instructions in the tool help. Live mode could not be run in the build container (no audio hardware).

## D41. No new config keys for hardware (Nov 13)
The audio input is chosen with `--input mic|glasses` and the display with `--display`. The config schema is unchanged, because the spec's `config/default.yaml` rejects unknown keys and every unknown already has a slot.

## D42. Demo mode behaviour (Nov 13)
- `tools/demo.py` starts the pipeline, dashboard, simulator and console display, and opens `/?kiosk=1` unless `--no-browser`.
- Live is the default mode. If the microphone cannot be opened it falls back to a replay of `scenarios/demo.yaml`, prints a `NOT LIVE` banner, and the page shows the RECORDING badge. `--no-fallback` makes it fail instead.
- Replay mode loops the audio at wall-clock speed until Ctrl+C. A synthetic scenario is tagged `synthetic`; a wav or a real-origin scenario is tagged `replay`. Neither is ever tagged `live`.
- The demo uses its own event log, `data/demo_events.sqlite`, wiped at start unless `--keep-log`, so the counters on stage start at zero and demo events never mix with other logs.
- Classifier model missing: the demo prints a warning and runs gate-only instead of refusing to start.
- `/api/status` carries `mode: demo`, `strict` and `input` (a description of the source) as additions to `source` and `recording_badge`.

## D43. Demo-mode toggle (Nov 13)
The risk register asks for a demo-mode toggle to cut false alerts from speech and music. Without real negatives to tune on, I did not invent new thresholds. `--strict` only changes classifier smoothing from 2 of 3 to 3 of 3 windows. It changes the config hash, so events recorded in strict mode are distinguishable. Tune real thresholds with `evaluate.py --sweep-thresholds` on your negatives.

## D44. Demo scenario is synthetic (Nov 13)
`scenarios/demo.yaml` has two backup alarms in babble with a lone beep and a click that must not alert. It has no horn or fire alarm because synthetic stand-ins are not recognised by YAMNet and the spec forbids passing synthetic audio off as real. Use `--input` with your own clips for a three-class demo.

## D45. Threads and signals (Nov 13)
Tools install a SIGINT handler only on the main thread, so they can also be driven from a test or another program.

## D46. Phone companion and profile (Nov 19)
After the organizers published the scoring rubric (Understands you, Adaptability, Ergonomics, Works on the glasses, Impact), I added a phone page (`/phone`), a per-person profile and a test-alert button. They need no SDK facts. The profile has two settings only: which of the three sounds are enabled, and a sensitivity from 0.5 to 1.5. A disabled sound produces no alert and no event. Sensitivity divides the gate's peak-to-median threshold and every classifier threshold, so higher catches more. The profile is saved to `data/profile.json` (gitignored); an unreadable file is ignored. It is not part of the config hash.

## D47. LAN access (Nov 19)
`--lan` makes the dashboard listen on all interfaces so a phone can open `/phone`. It has no authentication, so it is off by default and the tools print a reminder. Use your own hotspot. `/api/test-alert` and `/api/profile` are unauthenticated for the same reason.

## D48. Test alert (Nov 19)
`POST /api/test-alert` shows an alert with event id `e_test` through the real display adapter and clears it after 3 s. It writes nothing to the event log.

## D49. Project locked as MySoundprint (Nov 19)
After the scoring rubric and an idea comparison, the project is MySoundprint (`docs/MYSOUNDPRINT.md`): a personal sound attention layer built on the Sentinel engine. The spec's three-class limit for version 1 is lifted for this project; the sound catalog is config-defined. Spec rules that stay: offline at runtime, no raw audio stored by default, honest numbers only, no direction claims, hardware behind the two adapters. New capabilities (levels, named profiles, context trigger, feedback, sound memory, privacy screen, experimental Sound DNA) are built before the event. Sound DNA ships only if it passes a real test.

## D50. Sound catalog (Nov 20)
The catalog is the existing `AlertClass` enum, extended from 3 to 10 sounds, with everything else in `config/default.yaml` under `classifier.classes`: class map names, threshold, display labels (`label1`, `label2`, at most 12 characters) and `default_level`. The policy has no sound specific code. The 13 sounds in `docs/MYSOUNDPRINT.md` map to 10 catalog entries because three pairs share one entry: Doorbell and Ding-dong (`doorbell`), Siren and Police or Ambulance siren (`siren`), Smoke detector and Fire alarm (`fire_alarm`, as it already was in D20). Merging keeps one alert and one event when two close AudioSet classes fire together, and keeps the protected rule simple. Thresholds are still the 0.30 placeholders. Being in the catalog does not mean a sound is detected reliably. Line 2 is `NEARBY` for every sound, so no label claims a direction or a place. `ALERT_WORDING` in `types.py` stays as the fallback for the original three.

## D51. Four levels (Nov 20)
`interrupt`: shown, tier 1, asks for blink, the phone vibrates. `quiet`: shown, tier 2, never blinks, no phone vibration. `later`: saved as a confirmed event, never shown, one event per cooldown, unconfirmed gate hits are ignored. `off`: dropped, no alert and no event. The policy only asks for blink. Whether the display can blink is the display adapter's business, and a display that cannot blink emulates it with show and clear cycles (`AsyncDisplay`). We do not know if the M6 supports blink, and the emulation is a risk on a slow link: set `policy.blink: false` if it misbehaves on the lens. On the display, interrupt beats quiet, then the class priority order decides.

## D52. Named profiles and their defaults (Nov 20)
`profiles.definitions` in the config holds Focus, Home, Sleep and Commute, each with a level for every sound and a sensitivity (0.5 to 1.5, applied to the gate and to every classifier threshold). One is active (`profiles.active`, default Home). The levels in the shipped config are starting assumptions about each situation, not findings. Home keeps `reversing_alarm` at interrupt because the stock demo, the replay tools and the tests all use a synthetic backup alarm and must keep alerting. See D58 for why that default is a real compromise. In YAML an unquoted `off` is read as the boolean false, so the config loader accepts it as the level off. A config with no `profiles` section still loads: four profiles using each sound's catalog default.

## D53. Protected sounds (Nov 20)
`policy.protected` (default `[fire_alarm]`, so smoke and fire) always resolve to interrupt, whatever the active profile asks, including the Sleep profile. It is one pure function, `policy.effective_level`, used by the policy and covered by tests. The wearer's stored choice is never rewritten: if they set fire alarm to off, the store keeps off, the phone page shows the row locked as "Always interrupts", `/api/profiles` reports `effective`, and the event log records `interrupt`. So the override is visible, not silent.

## D54. Event log columns and migration (Nov 20)
Two nullable columns were added to `events`: `level` (the effective level that decided what happened) and `profile` (the active profile at detection). The spec's schema was binding, so this is a deliberate extension. An existing database is upgraded in place by `ALTER TABLE ... ADD COLUMN` when it is opened, old rows keep NULL and stay readable, and opening twice is safe. Both columns are insert only (not in `UPDATABLE`). `GET /api/events?level=later` returns the saved for later digest.

## D55. Profile store compatibility (Nov 20)
`ProfileStore.get()`, `update()` with `enabled` and `sensitivity`, `is_enabled()`, `sensitivity` and the `/api/profile` endpoints keep their old shape and now act on the active profile: `enabled: false` sets the level off, and `enabled: true` on an off sound restores its catalog default (not always interrupt). New: `levels`, `active`, `set_active`, `update_profile`, `snapshot`, `view` and the `/api/profiles` endpoints. A saved `data/profile.json` in the old single profile format is read as overrides to the active profile. The new format stores every profile and the active one. A damaged or invalid file is ignored.

## D56. Existing tests that had to change (Nov 20)
Six existing tests encoded "only three sounds exist" and were edited, none weakened. `test_config` unknown class: used `siren`, now a real sound, so it uses `klaxon`. `test_profile` invalid updates: same change. `test_every_alert_class_has_wording`: asserted the spec wording table covers every class; it now pins the exact spec wording of the original three, and wording for every catalog sound is tested against the config. `test_wording_matches_spec`: limited to the three spec sounds. `test_eventlog` schema: expects the two new columns. `test_classifier` fake class map: gained the new class names. All other tests pass unchanged.

## D57. ESC-50 as development audio (Nov 20)
ESC-50 is used only as local development audio. It is believed to be non-commercial licensed (check the dataset page), so the clips are never committed, never shown at the event and never used for a claim. `tests/test_real_audio_dev.py` reads them from `data/dev_audio/esc50/` (gitignored) or `SENTINEL_DEV_AUDIO_DIR` and skips with a clear reason when they are absent. The normal suite does not depend on them. ESC-50 has no doorbell class, so doorbell is still untested on real audio and needs your recordings.

## D58. Finding: the tonal gate fires on tonal everyday sounds (Nov 20, OPEN)
On real clips (dev only, 4 clips per category, a smell test and not a measurement) the classifier path looked reasonable: knock 4 of 4, siren 4 of 4, baby cry 3 of 4, alarm clock 3 of 4, car horn 3 of 4 detected as their own sound, and no classifier events on clapping, footsteps, typing or washing machine. But the tonal gate raised a provisional `BACKUP ALARM` on crying baby (4 of 4), vacuum cleaner (2 of 4, one confirmed), laughing (2 of 4), car horn, siren and dog. Most are cancelled after 1.6 s, but they flash on the lens first, and with Home's `reversing_alarm: interrupt` a crying baby shows `BACKUP ALARM`. This is a real false alert risk for the home story. Not fixed in Milestone A (it changes gate behaviour). Options for the human to choose: (a) Focus, Home and Sleep set `reversing_alarm` to `later` or `off` and only Commute interrupts, with the default profile changed; (b) show a gate hit only after the classifier does not contradict it; (c) tighten the gate. A known-issue test documents it (`xfail`).

## D59. Phone controls (Nov 20)
The three on/off switches on the phone page were replaced by a profile selector (four chips) and a level selector per sound (Interrupt, Quiet, Later, Off). Off covers the old switch, so nothing was lost. The sensitivity slider now edits the active profile. Fire and smoke show as locked. The phone only vibrates for tier 1 (interrupt). The dashboard feed shows level later events as "Saved for later". No history view, feedback or privacy screen was added.

## D60. Gate hits need the classifier (Nov 20, resolves D58)
A tonal gate hit for `reversing_alarm` is now only a candidate. It is never shown and never logged by itself. The candidate lives for `gate.confirm_timeout_s` (1.6 s, reused, no new constant) and ends one of three ways. (1) **Confirmed**: the classifier reports `reversing_alarm` itself (merged detection, stage `both`), which is the only way BACKUP ALARM can appear. (2) **Suppressed**: a classifier window ending after the candidate appeared contradicts it. Contradiction means the best of all 521 scores is not a reversing class, scores above the reversing score, and is at least the reversing catalog threshold (0.30 divided by the profile sensitivity). This reuses the catalog entry and adds no class mapping, classifier or confidence model. A window that also contradicts can no longer count as a reversing hit. (3) **Unconfirmed**: the timer runs out with no positive evidence. An inconclusive result is not confirmation, so nothing is shown. Every other classifier sound goes through the normal policy and profile path untouched. Setting `gate.confirm_with_classifier: false` restores the old behavior. With no classifier loaded the gate still alerts alone and logs an UNVERIFIED warning, because otherwise a gate-only run would never alert. That fallback needs the human's approval. The stock demo and the latency probe use gate-only synthetic audio, so they switch confirmation off and print a note.
Measured on real dev clips (ESC-50, 46 clips, dev only): BACKUP ALARM shown 21 before, 0 after. Cost: synthetic reversing recall went from 6 of 6 to 0 of 6 under confirmation, because a synthetic beep is not what the classifier calls reversing beeps. That figure is synthetic and says little about real alarms. Genuine reversing alarm detection rate and latency with confirmation are NOT MEASURED, there are no real recordings. The main risk: YAMNet may rank "Beep, bleep" above "Reversing beeps" on real alarms, which would suppress them. Real recordings are needed. Confirmed alerts arrive at the classifier's pace (2 of 3 smoothing, 0.25 s hop), not the gate's 0.3 s.

## D61. Simulated context trigger (Nov 20)
`profiles.triggers` in the config defines four buttons: Leaving home (Commute), Arriving home (Home), Starting focus (Focus), Going to sleep (Sleep). Pressing one calls `ProfileStore.simulate`, which switches the active profile and marks the context as simulated with the trigger's label. `return_to_manual` keeps the current profile and drops the simulated mark. Choosing a profile by hand also returns to manual. Endpoints: `POST /api/context/simulate` and `POST /api/context/manual`, both broadcast the profile message. The phone page shows the active profile pill, a "simulated" banner with "Back to manual" and a dashed "Simulated context (demo only)" box saying the buttons only pretend. The simulation mark is not saved: after a restart the profile is kept and the context reads manual. No GPS, geofence, background location, automatic location or automatic time switching exists, and a test scans the source for those APIs. Later detections use the new profile because the pipeline reads the store on every detection.

## D62. Same doorbell, three profiles (Nov 20)
`tests/test_context_demo.py` injects an identical scripted doorbell classification into Focus, Home and Sleep. Focus: saved as `later`, no lens alert, returned by `GET /api/events?level=later`. Home: interrupt, tier 1, blink, the phone vibrates (tier 1 only). Sleep: off, no alert, no event. Fire alarm in Sleep still interrupts. The event log carries the effective level and profile. The doorbell is injected, not heard, so this shows profile policy and not real doorbell recognition. Real doorbell recordings are still needed.
