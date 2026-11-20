# Evidence and limitations

What MySoundprint actually does, what has been checked, and what has not. Written for the hackathon pitch and for reviewers. If a statement here is stronger than the evidence column allows, the statement is wrong. Facts as of the repository state at HEAD `7a30b45` plus the uncommitted Milestone C to G working tree.

MySoundprint is an assistive awareness prototype. It is not a certified safety device.

## 1. Implemented in software (present in the code)

- **Sound pipeline:** microphone or file audio, a tonal gate for pulsed alarms, the YAMNet classifier (existing model, 10 catalog sounds), smoothing, one alert policy with cooldowns.
- **Reversing alarm confirmation:** a tonal gate hit is only a candidate. A BACKUP ALARM alert needs the classifier to report `reversing_alarm`. A confident contradictory classifier result suppresses it. A timeout with no positive evidence shows nothing. No classifier means no BACKUP ALARM.
- **Profiles and levels:** four profiles (Focus, Home, Sleep, Commute), four levels (interrupt, quiet, later, off), per-sound settings, a sensitivity setting, a protected rule that keeps fire and smoke at interrupt in every profile.
- **Simulated context triggers:** four buttons that switch the profile, labeled simulated, with a return to manual. No location or time detection exists.
- **Event log:** SQLite, with level, profile, source and an experimental marker, an API, CSV export and a saved-for-later list.
- **Phone page:** profile switching, per-sound levels, vibration on interrupt (browser dependent), a test alert, simulated context.
- **Displays:** console, browser glasses simulator, WebSocket bridge. The MicroLumin adapter is a stub that refuses to run.
- **Experimental gate-only mode:** off by default, opt-in, every alert labeled UNVERIFIED, events marked experimental.
- **Evaluation harness for real recordings:** manifest, source groups, holdout split, Wilson and Poisson statistics, acceptance statuses. No real recording has been run through it.
- **Hardware proof script:** `tools/hw_proof.py`, first test (HELLO, DOORBELL, clear), the 11 operations, `software_call` / `device_ack` / `observed_on_lens` / `verdict` / `failure_category` recording. Not run on glasses.
- **Preflight:** `tools/preflight.py` checks one computer (Python, config, experimental switch off, model, PortAudio and microphone, port, network address, leftover state, writable data, an in-process self-test). It marks phone, firewall, vibration, live sound and glasses checks as MANUAL, and has only run in a sandbox without PortAudio, where it correctly reported FAIL.

## 2. Automated tests passed (and reproducible local checks)

- `pytest`: **476 passed, 3 skipped, 0 failed, 0 xfailed** (the last full run, 108 s). The 3 skips are `tests/test_real_audio_dev.py`, which needs ESC-50 development clips that are not in the repository.
- `ruff check .` clean. `ruff format --check .` clean (87 files). `python -m sentinel check-config` ok. `python -m sentinel check-models` ok (521 classes).
- Policy, levels, profiles, the protected rule, event log migration, the simulated context trigger, gate confirmation (candidate, confirmed, suppressed, unconfirmed), the experimental marker, manifest validation, acceptance statuses and statistics, and the hardware proof reporting are all covered.
- **Important limit:** these tests use synthetic audio, scripted classifier results and fake displays. They prove the logic and the bookkeeping. They do not prove that the real classifier recognizes any real sound. The "same doorbell, three profiles" test injects the classification.
- Real-audio development check with ESC-50 clips (46 clips, dev only, not in the repository, run in an earlier milestone): the confirmation change reduced BACKUP ALARM shown from 21 to 0. That is a development check on non-target sounds, not an evaluation of real reversing alarms.
- Reproducible local checks done for this runbook: the demo started in replay mode, the simulated triggers, profile and level edits, protected fire effective level, event log fields and the test alert all answered as the runbook says. The browser phone page was checked with Playwright in an earlier milestone (simulated button, banner, back to manual, pill).

## 3. Demonstrated on real audio

**Nothing.** No real recording has been evaluated (acceptance: NOT_EVALUATED, evaluation NOT PERFORMED). The laptop microphone path could not be exercised in the checking sandbox (no PortAudio, no microphone). Software-only demonstrations exist (browser simulator, synthetic replay, the experimental UNVERIFIED mode) and are not real-audio evidence.

## 4. Demonstrated on physical M6

**Nothing. No physical M6 test has been performed.** At the Milestone H check there was no SDK documentation, no sample app, no API example, no glasses and no computer attached to the session. `tools/hw_proof.py --first-test` is ready for the first test and has only ever run against a fake lens and the console. Each row below is the evidence needed. Fill the evidence column from the proof logs and a video of the lens, never from software calls.

| Item | Evidence | Result |
|---|---|---|
| Official sample app built and run, text visible | video or photo, SDK version | **NOT PERFORMED** |
| Physical display initialization | `observed_on_lens`, sample app log | **NOT PERFORMED** |
| Message display (HELLO) | `first_test` stage `1_hello`, video | **NOT PERFORMED** |
| Message replacement (DOORBELL) | stage `2_replace`, video | **NOT PERFORMED** |
| Message clearing | stage `3_clear`, video | **NOT PERFORMED** |
| A new message after clearing | `--extended`, video | **NOT PERFORMED** |
| Stale clear and delayed old show cannot overwrite a newer message | `--extended` cases `stale_clear`, `delayed_old_show`, video | **NOT PERFORMED** |
| Repeated updates and soak | `calls`, `soak`, video | **NOT PERFORMED** |
| Disconnect and reconnect | `--manual`, video | **NOT PERFORMED** |
| Device acknowledgement | SDK documentation | **UNKNOWN**, `device_ack` stays `UNKNOWN` |
| End-to-end MySoundprint alert on the glasses (a normal, classifier-confirmed sound with the default configuration shown on the lens) | video of sound, detection and lens | **NOT PERFORMED** |
| Phone-side forwarding, if the SDK requires a phone | five conditions in `docs/M6_INTEGRATION_PLAN.md`, then a working forwarder | **NOT NEEDED OR BUILT YET**, conditions unestablished |
| Actual demo laptop and phone checks (microphone, network, firewall, vibration, restart) | `tools/preflight.py` on that laptop plus the manual items | **NOT PERFORMED** |
| End-to-end latency | whole path timed from video | **NOT MEASURED** |

The project **cannot honestly claim to work on physical glasses** until the rows above are filled from real observations. The software-only fallback is the browser simulator and must always be called that.

## 5. Not yet verified

| Item | Status | What would verify it |
|---|---|---|
| Real reversing-alarm recall | **Unknown.** Acceptance status NOT_EVALUATED. Synthetic recall under confirmation is near zero because the classifier does not recognize synthetic tones, which says nothing about real alarms. Risk: the model may rank "Beep, bleep" above "Reversing beeps" and suppress genuine alarms | At least 100 independent real source groups, run with `tools/eval_real.py` |
| False BACKUP ALARM rate | **Unknown.** NOT_EVALUATED. Only the 46-clip development check exists | At least 30 hours of diverse real negative audio, zero false alerts |
| Doorbell recognition performance | **Unknown.** Doorbell has never been tested on real audio | 5 to 10 separate real doorbell trials as a first look, more for any claim |
| Real-world confirmation time | **Unknown.** Only audio-stream time on synthetic fixtures exists | Real recordings with onset times |
| End-to-end latency | **Not measured.** Software figures (about 0.3 s from synthetic onset to the display call returning) are not glasses latency and are not claimed | Full path measured with video on the glasses |
| Physical M6 display integration | **Not done.** No adapter, no SDK documentation, no hardware access | SDK access, glasses, the 11-operation proof |
| Device acknowledgement and update behavior | **UNKNOWN.** The adapter protocol returns nothing from a device. Replace, stale clear and disconnect behavior are untested on a lens | The hardware proof, with the SDK's documented acknowledgement if any |
| Phone-side production audio processing | **Not built.** The microphone is on the laptop. The phone page does not send audio to the laptop. A browser microphone over plain HTTP on a LAN is not claimed | A separate decision and design |
| Phone vibration on a real phone | **Unverified.** Depends on browser support | Try it on the demo phone |
| Message length limits (12 characters, 2 lines) | **Assumed** config placeholders | SDK documentation or a test on a lens |

## 6. Claims we do not make

Not claimed, and not allowed in the pitch unless evidence appears:

- Guaranteed safety, or a certified safety device.
- Guaranteed reversing-alarm recognition.
- Automatic GPS or geofence context detection. The context buttons are simulated.
- Proven real-world recall or false-alert rates.
- Physical glasses integration.
- Directional sound localization. The alert never says where a sound is.
- That a software display call means text appeared on a lens.
- That the synthetic backup alarm or the UNVERIFIED mode is real-world validation.

## 7. Final demo path and exact configuration (Milestone J)

Recorded so the demo can be reproduced and judged against what was actually run.

| Item | Value |
|---|---|
| Branch and commit | `main`, `7a30b45`, with Milestones C to J uncommitted in the working tree |
| Configuration | `config/default.yaml` unmodified. Config hash `f97bc65e61e8` (`python -m sentinel check-config`). `experimental.unverified_gate_only` false |
| Primary software path | Path C (live USB microphone, `python tools/demo.py --lan`) **only if** `tools/preflight.py` passes on the actual demo laptop and a genuine classifier-confirmed sound has been seen. **Not yet tested on any demo laptop** |
| Fallback | Path A (`python tools/demo.py --mode replay --lan`), software-only, labeled as such |
| Never the stage demo | Path B (experimental gate-only, UNVERIFIED) |
| Narrative | `docs/DEMO_NARRATIVE.md`, software-only version unless a MySoundprint alert has been seen on physical glasses |
| Preflight output kept | `reports/preflight_2026-11-20_sandbox.txt` (gitignored). It is from the **checking sandbox, not the demo laptop**: 12 PASS, 1 FAIL (no PortAudio there), 6 MANUAL. It is not evidence about the demo laptop |

## 8. Wording for the pitch

Safe to say: "an assistive prototype that shows two-word alerts, filtered by a profile you choose, tested in software; real-recording and glasses testing are next." Safe to say: "the context buttons are simulated". Not safe: any recall, false-alert or latency number from the current evidence.
