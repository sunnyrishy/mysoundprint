# Demo runbook

How to run the MySoundprint demo from this repository, what the audience will see, and what is simulated. Every command below was run against this repository unless it is marked **UNVERIFIED**. The sandbox used for checking has no microphone, no PortAudio and no glasses, so everything that needs those is marked.

MySoundprint is an assistive awareness prototype. It is not a certified safety device and the demo must never say it prevents accidents.

## What is real and what is simulated

| Part | Status in this demo |
|---|---|
| Sound filtering by profile and level (interrupt, quiet, later, off), protected fire and smoke rule, event log | Real code. Tested automatically. Shown live only with the sounds the classifier recognizes |
| Reversing alarm | The classifier must confirm it. A synthetic tone is **not** recognized, so a synthetic backup alarm shows nothing unless the labeled experimental switch is used. No real reversing-alarm recording has been evaluated |
| Context triggers (Leaving home, Arriving home, Starting focus, Going to sleep) | **Simulated.** Buttons that switch the profile. No GPS, no geofence, no time of day, no automatic detection |
| Glasses display | **Browser simulator and console only.** No M6 adapter exists and nothing has run on glasses. A software call is not proof that text appeared on a lens |
| Microphone | The laptop or USB microphone is a demonstration sensor. It is not phone-side audio processing. The phone page does not send microphone audio to the laptop |
| Phone | Only a browser on the same network that opens the page, switches profiles and levels, and vibrates on interrupt alerts if its browser supports it (**UNVERIFIED** on a real phone) |

## 1. Prerequisites

- Python 3.11 or newer. On Linux the microphone needs PortAudio: `sudo apt install libportaudio2`.
- A laptop. For the phone page, a phone on the same network (your own hotspot is best: the page has no password).
- For the live path, a microphone and a speaker to play sounds at it.
- Network is needed once, to download the model. After that everything runs offline.

The three-minute script, in a physical version and a software-only version, with what not to say, is in `docs/DEMO_NARRATIVE.md`.

## 1a. Preflight on the actual demo laptop

Run this on the machine you will present from, with the phone and network you will use:

```bash
python tools/preflight.py --self-test
```

It checks Python, the config, that the experimental gate-only switch is off, that the model loads, PortAudio and a microphone, that the dashboard port is free, that the laptop has a network address, leftover demo state (`data/profile.json`), that `data/` is writable, and (with `--self-test`) the pages, the profiles and the four simulated triggers in process. It prints MANUAL for what only a person can check and never marks those as passed: the phone opening the `Phone:` address, the firewall, phone vibration, a real sound heard by the microphone, a clean stop and restart, and the glasses. In the sandbox used to write this it reported FAIL for PortAudio, which is correct there. A passing preflight does not prove anything works on physical glasses.

## 2. Install and check

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,classifier]"
python tools/fetch_models.py        # one time, needs network
python -m sentinel check-config     # prints "config ok" and a hash
python -m sentinel check-models     # prints "model ok" and the class mapping
```

If `check-models` fails, nothing classifier-based will work. Fix it before the demo.

## 3. Start the demo

Pick one path. Stop with Ctrl+C.

**A. Software-only, honest default (no sound recognized from a synthetic clip)**

```bash
python tools/demo.py --mode replay --lan
```

Opens the glasses simulator in kiosk mode and serves the phone page. The replay is labeled RECORDING. The synthetic backup alarm in it is not confirmed by the classifier, so the simulator stays blank and the banner says so. Use it to show the interface and profile controls.

**B. Engineering diagnostic only, never the stage demo (experimental, labeled)**

```bash
python tools/demo.py --mode replay --no-classifier --experimental-unverified-gate-only --lan
```

The tonal gate alerts on its own and every alert reads `BACKUP ALARM` over `UNVERIFIED`. This is the experimental test mode: it is not reliable alarm detection and not recognition of a real alarm. **Do not use it as the primary stage demonstration, and do not use it to produce an impressive alert.** It is acceptable only as a clearly labeled engineering diagnostic to check that the profile levels reach the display, and you must say so out loud. The replay loops about every 50 s with two synthetic alarms at roughly 6 s and 30 s into each loop.

**C. Live laptop or USB microphone (UNVERIFIED in the check sandbox: no microphone)**

```bash
python tools/run_live.py --list-devices
python tools/demo.py --lan                       # default microphone
python tools/demo.py --lan --device <index or name>
```

The classifier listens to the room. It can only show what it recognizes (sounds in the catalog). Add `--no-fallback` if you would rather it fail than silently fall back to a labeled replay when the microphone cannot be opened. Real-world recognition of any sound, including a doorbell, a fire alarm and a reversing alarm, is **not yet verified**.

Useful flags (all checked in `--help`): `--no-browser`, `--no-kiosk`, `--duration N`, `--db PATH`, `--keep-log`, `--strict`, `--record`.

## 4. Open the phone interface

The startup banner prints two addresses:

- `Page:` the glasses simulator, `http://127.0.0.1:8765/?kiosk=1`. Put this on the projector.
- `Phone:` `http://<laptop-ip>:8765/phone`. Open it on the phone. Only printed with `--lan`. Without `--lan` the server answers only on this computer.

The full dashboard is `http://127.0.0.1:8765/`. If the phone cannot connect, see troubleshooting.

## 5. Profiles

Four profiles exist: Focus, Home, Sleep, Commute. Home is the default.

- **Phone page:** tap a profile chip. The header pill shows the active profile.
- **Command line (verified):**

```bash
curl -s -XPOST localhost:8765/api/profiles/active -H 'content-type: application/json' -d '{"name":"focus"}'
curl -s localhost:8765/api/profiles          # shows every profile, level and the effective levels
```

Edits are saved to `data/profile.json` and survive a restart. To return to the shipped defaults, stop the demo and delete `data/profile.json`.

## 6. The four simulated context triggers

On the phone page, in the dashed box "Simulated context (demo only)": **Leaving home** (Commute), **Arriving home** (Home), **Starting focus time** (Focus), **Going to sleep** (Sleep). A banner then reads "Simulated context ... set by a demo button, not by real detection" and the pill shows "(simulated)". "Back to manual" keeps the profile and removes the simulated label. Choosing a profile by hand also returns to manual.

Command line (verified):

```bash
curl -s -XPOST localhost:8765/api/context/simulate -H 'content-type: application/json' -d '{"trigger":"leave_home"}'
curl -s -XPOST localhost:8765/api/context/manual
```

Trigger ids: `leave_home`, `arrive_home`, `start_focus`, `go_to_sleep`. Say clearly that these buttons only pretend. Nothing detects a place or the time of day.

## 7. Sound-priority levels

Each profile gives every sound one of four levels:

| Level | What the wearer sees |
|---|---|
| interrupt | Shown on the glasses simulator, tier 1, asks to blink, the phone vibrates |
| quiet | Shown, tier 2, no blink, no phone vibration |
| later | Not shown. Saved as an event. Listed by `GET /api/events?level=later` |
| off | Nothing shown, nothing saved |

Change a level on the phone page (the per-sound selector) or by command line (verified):

```bash
curl -s -XPOST localhost:8765/api/profiles/home -H 'content-type: application/json' -d '{"levels":{"doorbell":"later"}}'
```

**What the engineering diagnostic (path B, verified) shows with the synthetic backup alarm:** the backup alarm level differs by profile. Home interrupt (tier 1 alert shown), Focus quiet (tier 2 alert shown), Sleep off (no alert, no event), Commute interrupt. Switch profile between loops with the buttons or the command above and watch the simulator and the event table.

**What path B cannot show:** `later`. No profile gives the backup alarm `later`, and a gate-only hit at that level is ignored. Show `later` with a real doorbell or horn through the classifier (**UNVERIFIED**, doorbell recognition on real audio has not been measured), or show the automated test below.

**Same doorbell, three outcomes (automated test, injected classification, verified):**

```bash
pytest tests/test_context_demo.py
```

It injects an identical doorbell result into Focus (later, nothing on the lens, in the later-event list), Home (interrupt, tier 1, phone vibration) and Sleep (off). This shows profile policy. It does **not** show that the real classifier recognizes a real doorbell.

## 8. Protected fire and smoke alarms

Fire and smoke always interrupt, in every profile, including Sleep, whatever the wearer sets.

- On the phone page the fire alarm row is locked and says "Always interrupts".
- Verified by command: set it off, then read the effective levels.

```bash
curl -s -XPOST localhost:8765/api/profiles/home -H 'content-type: application/json' -d '{"levels":{"fire_alarm":"off"}}'
curl -s localhost:8765/api/profiles          # "protected": ["fire_alarm"], and "effective" still shows interrupt
```

The stored choice stays `off` and the effective level is `interrupt`, so the override is visible. Remember to delete `data/profile.json` afterwards.

- Automated check (verified): `pytest tests/test_catalog_levels.py -k protected` and `pytest tests/test_context_demo.py -k fire`.
- A real fire alarm sound through the classifier is **UNVERIFIED**.

## 9. Event log and profile attribution

Every event records its level, the active profile at that moment, its stage, its source (`live`, `replay` or `synthetic`) and whether it is experimental. Verified:

```bash
curl -s localhost:8765/api/events                 # newest first, JSON
curl -s "localhost:8765/api/events?level=later"   # the saved-for-later list
curl -s localhost:8765/api/events.csv
curl -s localhost:8765/api/stats
```

Example from a checked run: `reversing_alarm, level interrupt, profile home, experimental true` (path B). In a normal run `experimental` is false. An experimental event is one created through the gate-only path, which also reads UNVERIFIED on the display. The dashboard at `/` shows the same table. When the demo ends it prints "Events this demo". The log is `data/demo_events.sqlite` and is cleared at each start unless you pass `--keep-log`.

The "Send a test alert to the glasses" button on the phone page (`POST /api/test-alert`, verified) pushes a labeled test alert through the real display path. In this repository that path ends in the browser simulator. It is not a glasses test.

## 10. Stop and restart

- Stop: Ctrl+C in the terminal (it ends the pipeline and prints the events). Or start with `--duration 60` to stop by itself.
- Restart: run the same command again. The event log starts empty unless `--keep-log`. Profiles are kept (`data/profile.json`).
- Reset everything: stop the demo, then delete `data/profile.json` and `data/demo_events.sqlite`.

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `sounddevice could not load PortAudio` (checked, exact message) | Linux: `sudo apt install libportaudio2`. macOS and Windows wheels bundle it. Or use path A or B |
| "Could not open the microphone", or the demo says it fell back to a replay | The page then shows RECORDING and events are not `live`. Check `python tools/run_live.py --list-devices`, pick a device with `--device`, and on macOS or Windows allow the terminal in the system microphone privacy settings (**UNVERIFIED** here). Use `--no-fallback` to fail instead |
| No sound is ever shown on the live path | The classifier only shows catalog sounds it recognizes, and a synthetic tone is not one. Play a real sound. A reversing alarm needs the classifier to confirm it. Check `check-models` |
| `check-models` fails or model not found | Run `python tools/fetch_models.py` (needs network once) |
| Server will not start or the page does not load | Another process may hold port 8765 (`dashboard.port` in `config/default.yaml`). Close the other copy and restart. The exact port-in-use message is **UNVERIFIED** |
| Phone cannot open the page | Start with `--lan`. Same network, no VPN. Hotspots or guest Wi-Fi with client isolation block it. Use the `Phone:` address printed at startup. The page has no password, so use your own hotspot |
| Phone does not vibrate | The page uses the browser vibrate call for interrupt alerts. Support depends on the phone and browser (**UNVERIFIED** here). The alert on the simulator still appears |
| Simulator blank in path A | Expected. The synthetic alarm is not confirmed by the classifier |
| Old profile settings appear | `data/profile.json` persists. Delete it |

## 12. What the audience should see, stage by stage

1. **Startup banner.** "RECORDING" for replays, "live microphone" for live, and "Assistive alert, not a certified safety device." Path B also says "EXPERIMENTAL UNVERIFIED gate alerts are ON".
2. **Simulator.** A small dark display. Blank means nothing needed attention.
3. **Phone page.** Profile chips, a pill with the active profile, per-sound level selectors, a locked fire row, the simulated context box.
4. **Context button.** The profile switches, a banner says it is simulated.
5. **Sound arrives.** Interrupt: bold two-line alert, tier 1. Quiet: a calmer tier 2 alert. Later or off: nothing on the display. Later appears in the event list.
6. **Event table.** Each row shows class, level and profile.
7. **Honest limits slide.** Software simulator only, no glasses test, no real-audio evaluation yet, simulated context.

## 13. Recommended order (about 3 minutes)

**Primary (needs the actual laptop to pass preflight, a sound the classifier really confirms, and, for the glasses step, a working M6 adapter that does not exist yet):** Path C. 1. Problem and concept (20 s). 2. Phone page and profile selection (20 s). 3. The four levels (20 s). 4. Fire row locked, "Always interrupts" (10 s). 5. Play a real sound the classifier confirms: it appears on the simulator, and on the glasses only if the physical proof has passed (30 s). 6. Replacement and clearing (20 s). 7. Tap a simulated context button, say it is simulated (20 s). 8. Event table with level and profile (20 s). 9. State the limits (30 s).

**Software-only fallback (always label it that way):** Path A. Show the banner (15 s), the phone page and profile chips (20 s), the four levels in the selectors (20 s), the locked fire row (10 s), the simulated context buttons (20 s), "Send a test alert" to show the simulator (labeled test alert, not a detection) (15 s), `pytest tests/test_context_demo.py` for the same doorbell in three profiles (injected, policy only) (30 s), then the limits (30 s). Say "this is a software simulator, not the glasses".

Do not say the context buttons detect anything, do not call the UNVERIFIED alert a real detection, do not call the simulator the glasses, and do not quote recall or false-alert numbers: none exist yet.
