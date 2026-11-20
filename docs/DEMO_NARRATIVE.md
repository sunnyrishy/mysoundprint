# Demo narrative

A short, accurate script for the three minutes on stage, in two versions. Pick the version that the evidence supports **at the moment you present**. Do not mix them, and do not read the physical version unless an actual MySoundprint alert has been observed on physical M6 glasses. Commands and setup are in `docs/DEMO_RUNBOOK.md`. What is and is not verified is in `docs/DEMO_EVIDENCE_AND_LIMITATIONS.md`.

## Which version to use

| Condition | Version |
|---|---|
| A normal, classifier-confirmed MySoundprint alert has been seen on physical M6 glasses, on the actual demo laptop, in a rehearsal, and the proof log has `observed_on_lens` and `verdict` filled in by a person | **Physical** |
| Anything else (no SDK, no glasses, adapter not proven, or only the official sample app ran) | **Software-only** (the default today) |

An official phone app showing text is not proof that MySoundprint is integrated with the glasses.

## The problem and the idea (both versions, about 30 s)

People who cannot rely on hearing (earmuffs and earplugs on a worksite, headphones on a commute, deep sleep, deep focus) miss sounds that matter. A phone that buzzes at everything is no help. MySoundprint decides which sounds deserve to interrupt you, using a profile you choose, and shows a short text alert. It is an assistive awareness prototype. It is not a certified safety device.

## How it decides (both versions, about 40 s)

1. A microphone feeds a pipeline. A fast tonal detector looks for pulsed alarm tones, and a sound classifier (the existing YAMNet model) recognizes ten catalog sounds.
2. A tonal hit alone is only a candidate. A BACKUP ALARM needs the classifier to confirm a reversing alarm. A confident contradictory classifier result suppresses it. No confirmation means no alert. This is deliberately conservative.
3. Your profile (Focus, Home, Sleep, Commute) gives each sound a level: interrupt, quiet, later or off. Fire and smoke always interrupt, in every profile.
4. Every event is logged with its level and the profile that was active.

## The profile and context part (both versions, about 40 s)

Show the phone page: choose a profile, show the per-sound levels, show the locked fire row ("Always interrupts"). Tap a context button and say it out loud: **these buttons are simulated. Nothing detects where you are or what time it is.**

## The alert (this is where the versions differ, about 40 s)

**Physical version.** Play a real sound the classifier confirms. Say: "this alert was produced by the normal MySoundprint pipeline with the default configuration." Point at the glasses. Show it replace with the next alert and clear. Show the event row with level and profile. Do not quote latency: it was not measured end to end.

**Software-only version.** Say plainly: "Physical M6 integration was not demonstrated. What you are seeing is a software simulator of the glasses display." Then either play a real sound through Path C, if Path C passed preflight and a real sound on this laptop, or use Path A with the labeled test alert (a test alert, not a detection) and show `pytest tests/test_context_demo.py`, which injects the same doorbell result into three profiles (policy only, not recognition). Show the documented adapter boundary (`DisplayAdapter`, the stub, the hardware proof tool and its logs) and say what is missing: the SDK documentation and a physical test.

## What has actually been tested (both versions, about 20 s)

State only what the evidence file says: the logic is covered by automated tests on synthetic audio and scripted classifier results (read the current count from the evidence file), the software simulator runs, and the phone page and profile controls work. Say that **no real-audio evaluation has been done**, that reversing-alarm recall and the false-alert rate are unknown, and that the doorbell has not been tested on real audio. Add the physical status honestly: either "observed on glasses on <date>, once, in rehearsal" or "not yet tested on glasses".

## Do not say

- That it is safe, certified, or prevents accidents.
- That it recognizes reversing alarms (or any sound) reliably, or quote a recall, false-alert rate or latency. None are measured.
- That the context buttons are GPS, geofencing or automatic detection.
- That it knows where a sound comes from. It has no direction finding.
- That the simulator, a software call, a test alert, a passing test or the official SDK sample is MySoundprint working on glasses.
- That the UNVERIFIED gate-only mode is a real detection. It is an engineering diagnostic and is never the stage demo.

## If a judge asks

- **"Does it work on the glasses?"** Physical version: "One alert was observed on the glasses in rehearsal. We have not measured latency or reliability." Software-only version: "Not demonstrated yet. The adapter boundary is built and the proof procedure is ready. The blocker is SDK access."
- **"How accurate is it?"** "Unknown. No real-audio evaluation yet. We have the protocol and the evaluation harness, and we need real recordings."
- **"Why so conservative on backup alarms?"** "A tonal beep alone caused false alerts on ordinary sounds in a development check, so a backup alarm now needs classifier confirmation. The cost is that real recall is unmeasured."
