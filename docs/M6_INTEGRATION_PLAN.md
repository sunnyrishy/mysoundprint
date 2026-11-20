# M6 integration plan (readiness audit)

Status: **audit only. No M6 adapter exists.** `display_microlumin.py` is a stub that raises `NotImplementedError`. Nothing in this repository has run on the physical glasses. No SDK documentation is in the repository, so no package name, API, protocol, display size, refresh rate or capability below is a fact about the M6. Everything not read from our own code is `UNKNOWN`. The only hardware facts we hold are the unconfirmed press-review rows in `docs/SDK_NOTES.md`.

Audited at commit `7a30b45` plus the Milestone C changes. Code references are to this repository.

## Status (Milestone F)

SDK documentation, a sample app or verified API examples: **none available**. No M6 adapter was written. The proof in section 5 remains executable against a test adapter (`tools/hw_proof.py` with any `DisplayAdapter`) and is covered by tests on fake lenses only. Physical glasses tests: **not performed**. Missing information needed before an adapter can be written: the SDK form and version, supported connection methods, the show, replace and clear operations, any acknowledgement or completion guarantee, documented text and display limits, and disconnect and reconnect behavior (items 1 to 12 in section 6, all UNKNOWN).

### Status at Milestone J (Nov 20)

Rechecked: still no SDK documentation, sample app or API example in the repository or on this machine, no glasses, no computer attached to the session. The adapter is still a stub, `device_ack` is `UNKNOWN`, no physical test and no phone-side forwarder exists. The conditional forwarder below stays unbuilt until its five conditions are established and a background-restriction question is answered: whether the SDK connection stays alive on the phone while it receives WebSocket messages (for example when the screen is off or the app is in the background). Record that answer in `docs/SDK_NOTES.md`. If the SDK, the phone or the connection fails any condition, the honest alternatives, in order, are: an official phone app demonstration (clearly not MySoundprint integration), the laptop software demonstration with the adapter boundary explained, and the software-only fallback.

### Hardware-day order (Milestone H)

Nothing here has happened yet. When the glasses and SDK are handed out:

1. **Read first, code later.** Read all supplied SDK documentation. Find the official sample app. Note the supported host device, operating system and connection requirements. Write every answer, with its source and the time, into `docs/SDK_NOTES.md`. Quote, do not paraphrase from memory.
2. **Build and run the official sample app before touching MySoundprint.** If text appears on the lens from the sample app, the hardware path is proven. If not, stop and ask the organizers or the device provider. Do not invent an integration.
3. **Find the documented calls** for initialization, show, replace and clear, the update-rate and message-length limits, and whether any call acknowledges a physical update. No acknowledgement documented means `device_ack` stays `UNKNOWN`.
4. **Write the smallest adapter** in `src/sentinel/adapters/display_microlumin.py` (initialize, show, replace, clear, close) using only documented calls, keeping the `DisplayAdapter` protocol and the existing `AsyncDisplay` wrapper. It must treat `show` as replace, ignore a clear for an event that is no longer current, and not let a delayed old show overwrite a newer one. No blink, no effects, no new architecture, no pipeline rewrite.
5. **First physical test:** `python tools/hw_proof.py --first-test --display microlumin`. It shows `HELLO`, replaces it with `DOORBELL`, then clears. Short single-line messages, and no character or line limit is assumed: the SDK documentation or this test on the lens must establish it. Film the lens. Fill `observed_on_lens`, `verdict` and `failure_category` by hand. A returned call is not proof. Run the official SDK sample unchanged first, and initialize with documented calls only.
6. **Only after step 5 succeeds on the lens,** run `--extended` (repeated updates, rapid alternation, stale clear after a newer message, delayed old show after a newer message, longer messages within DOCUMENTED limits, soak) and `--manual` (disconnect, reconnect if supported). Pass `--caps-documented` only when `display.caps` matches limits the SDK documents. No blink, animation or other optional display behavior before display, replacement and clearing are reliable.
7. **Only after step 5 and 6 pass,** connect the real alert path: trigger one normal, classifier-confirmed sound alert with the default (non-experimental) configuration, see it on the lens, and check replacement and clearing through the real application. Profile priority, confirmation, thresholds and event provenance must be unchanged, and `experimental.unverified_gate_only` must still be off. Do not use the experimental UNVERIFIED mode as the stage demo. It may be used as a labeled engineering diagnostic only.
8. Record the results in `docs/DEMO_EVIDENCE_AND_LIMITATIONS.md` section 4 from observations only.

The config's 2 lines of 12 characters is an assumption. The first test does not depend on it. Record how each message actually rendered and what the SDK documents, then set `display.caps` to the documented values.

### If the SDK needs a phone (conditional architecture, not built)

```
laptop MySoundprint pipeline -> existing WebSocket bridge -> phone-side forwarder -> documented SDK -> glasses
```

Do **not** build this until all five are established, from documentation or a working sample, and written into `docs/SDK_NOTES.md`:

1. The supported phone platform (Android, iOS, which versions).
2. The SDK runs on that phone.
3. A documented interface lets an external app or service send display updates (not only the SDK's own app).
4. The phone can receive the laptop's WebSocket messages (same network, reachable address, no client isolation).
5. The phone can forward show, replace and clear reliably.

If all five hold: the forwarder is a **thin display forwarder** and nothing else. The pipeline stays on the laptop, no audio is ported to the phone, and **no raw audio goes over the bridge**. This repository's hub carries only `alert`, `clear`, `event`, `stats` and `profile` messages (checked: nothing publishes audio). The forwarder uses only:

- `alert`: `event_id`, `line1`, `line2`, `tier`, `blink`, `ttl_ms`. Show it, replacing whatever is there. Ignore `blink` until replacement is reliable.
- `clear`: `event_id` or null. Clear only if `event_id` is the message currently shown, or null.

Everything else is ignored. Requirements it must meet itself, because the messages carry no sequence number: process messages in the order received on one connection; never let a late show finish after a newer one (serialize calls to the SDK, or check a counter after each call); ignore a clear for an event that is not current; on reconnect to the laptop, clear the lens, since its state is unknown. `tools/bridge_echo.py` is the template and `forward_to_glasses()` the empty slot. `tools/hw_proof.py` can be pointed at the forwarder's path to prove the same cases.

If any of the five is unsupported, document the exact blocker in `docs/SDK_NOTES.md` and ask the organizers for the smallest viable alternative (for example a documented laptop connection, or an organizer-provided relay). Do not build a speculative bridge.

### Integration status checklist (Milestone G)

| Item | Status |
|---|---|
| SDK documentation, sample app or verified API example in the repository | **No** |
| SDK name and version | UNKNOWN |
| Connection method (phone app, BLE, Wi-Fi, laptop direct) | UNKNOWN |
| Initialize, show, replace and clear operations | UNKNOWN |
| Acknowledgement or completion guarantee | UNKNOWN. `device_ack` stays `UNKNOWN` |
| Documented text limits (lines, characters, color, blink) | UNKNOWN. 2 x 12 is an assumed placeholder |
| Disconnect and reconnect behavior | UNKNOWN |
| Access to a physical M6 | **No** |
| M6 adapter | Not implemented. The stub is kept |
| Hardware proof run on glasses | **No** |
| Hardware proof runnable on a test adapter | Yes (`tools/hw_proof.py`, fake lens tests) |

**What to ask the organizers or MicroLumin:** the SDK package or document with its version, a working sample app that puts text on the lens, the connection method and whether a phone is mandatory, the calls to show, replace and clear text, whether any call acknowledges completion and what it guarantees, the text limits (lines, characters per line, color, native blink, brightness), whether the display clears itself, what happens on disconnect and whether reconnect is supported, and a pair of glasses to test on. See the 13 unknowns in section 6.

**Minimum integration sequence once access exists** (do not skip ahead, no blink and no visual extras until step 4 is reliable):
1. Run the organizers' sample app and confirm text appears on the lens by itself. That proves the hardware path.
2. Initialize the connection from our code and show one message.
3. Replace it with another, then clear it.
4. Prove stale and delayed updates cannot overwrite newer messages: a clear for an old event after a newer message, and a delayed old show finishing after a newer one (`tools/hw_proof.py --extended`).
5. Repeat updates (20, then the multi-minute soak) and, where the SDK supports it, test recovery after a disconnect (`--manual`).
6. Only then connect real alerts. Record every result as `software_call`, `device_ack` and `observed_on_lens`.

A queued or returned software request is never treated as proof that a message is visible on the glasses. Only a person looking at the lens can set `observed_on_lens`.

## 1. Where the display adapter is defined

| What | Where |
|---|---|
| The contract (a `Protocol`): `capabilities()`, `show(alert)`, `clear(event_id=None)`, `close()` | `sentinel.types.DisplayAdapter` (`src/sentinel/types.py`) |
| The alert a display receives: `event_id`, `alert_class`, `tier`, `line1`, `line2`, `blink`, `ttl_s` | `sentinel.types.Alert` |
| Declared display capabilities: `lines`, `max_chars_per_line`, `color`, `supports_blink` | `sentinel.types.DisplayCaps`, set from `display.caps` in `config/default.yaml` (`CapsConfig`, `src/sentinel/config.py`) |
| The stub for the glasses | `src/sentinel/adapters/display_microlumin.py` (`MicroLuminDisplay`, raises in `__init__`) |
| Name to adapter factory (lazy imports) | `make_display()` and `KNOWN_DISPLAYS` in `src/sentinel/adapters/__init__.py` |
| Selection | `display.adapter` in the config (`console`, `sim`, `bridge`, `microlumin`) or `--display` on the tools |
| Existing working adapters | `display_console.py`, `display_sim.py` (browser), `display_bridge.py` (WebSocket for a phone side script) |
| Shared helpers | `fit_lines()` truncates text to the caps. `AsyncDisplay` and `MultiDisplay` in `src/sentinel/adapters/base.py` |

## 2. How alerts are sent and cleared today

1. `Policy` (`src/sentinel/policy.py`) returns actions: `ShowAlert`, `ClearAlert`, `CreateEvent`, `UpdateEvent`.
2. `Pipeline._apply()` (`pipeline.py`) calls `self.display.show(alert)` or `self.display.clear(event_id)`.
3. `self.display` is `_SyncDisplay` in deterministic runs, or `AsyncDisplay` in live runs. `AsyncDisplay` runs each call on a worker thread with a 1 s timeout, drops the oldest queued command when 16 are waiting, and emulates blink with show and clear cycles every 0.5 s when the adapter says `supports_blink=False`.
4. `MultiDisplay` fans one call out to several adapters (for example console plus sim). `runtime.start_runtime()` builds the display and wraps it.
5. When a clear happens: the alert time to live (`policy.ttl_s`) ends, an unconfirmed provisional alert times out, or the run ends (`clear(None)`).
6. **A newer, higher priority alert replaces the one on screen with a plain `show()` and no `clear()` in between.** The adapter's `show()` must therefore mean "replace whatever is there".
7. `POST /api/test-alert` (phone page "Send a test alert to the glasses") pushes a labeled test alert through the real display path.

## 3. What a real M6 adapter must implement

Only the four protocol methods. Behavior the rest of the code already relies on, which the adapter must honor:

- `show(alert)`: draw `fit_lines(alert, caps)` and **replace** any text already shown. Truncate, never wrap.
- `clear(event_id)`: remove the text. If `event_id` is not the alert currently on the lens it must do **nothing**, so a late clear for an old alert can never erase a newer message. `None` clears everything.
- Calls must be idempotent and safe to repeat. `AsyncDisplay` may repeat show and clear for blink emulation.
- A call that times out is abandoned, but its thread may still finish later. The adapter must not let a late, abandoned `show()` overwrite a newer message. This needs a per-call sequence check inside the adapter.
- `capabilities()` must report the real values. They must also be set in `display.caps` in the config. If the lens can blink natively, set `supports_blink: true` so the emulation switches off.
- `close()` releases the connection.
- Each call should stay short. The adapter is wrapped, so a slow call never blocks detection, but it will be abandoned after 1 s.

If the SDK is a phone app, the likely route is **no new adapter**: use `BridgeDisplay` (already built) and write a small phone-side subscriber that forwards messages. `tools/bridge_echo.py` is the template and `forward_to_glasses()` is the empty slot. Which route applies depends on SDK answers 1, 4 and 5 below.

## 4. Existing components that would change to connect an adapter

| Component | Change | When |
|---|---|---|
| `adapters/display_microlumin.py` | Replace the stub with the real adapter | Only after SDK docs exist in the repo |
| `config/default.yaml` `display.caps` | Real lines, characters, color, blink | After capabilities are confirmed on hardware |
| `adapters/__init__.py` `make_display()` | Pass any connection settings the adapter needs | Only if the adapter needs them |
| `tools/bridge_echo.py` `forward_to_glasses()` | Only for the phone-bridge route | After SDK docs |
| `docs/SDK_NOTES.md` | Record every confirmed fact with its source | As facts arrive |

**Nothing else changes:** the pipeline, policy, gate, classifier and event log never see the adapter's internals. That separation is the point of the protocol.

## 5. First hardware proof (deliberately small)

Do this before anything else touches the glasses. It uses `tools/hw_proof.py`, which works with any adapter and skips cleanly with the stub.

1. **Show** one message (`PROOF A / ONE`). Look at the lens. Is it readable?
2. **Replace** it with another (`PROOF B / TWO`) with no clear in between. Does any of A remain?
3. **Update repeatedly**: 20 alternating updates. Does each show the newest message? Any skipped, stuck or flickering update? Does the display clear by itself between updates?
4. **Clear**, then confirm the lens is empty (no stale text).
5. **Measure on the hardware**: the script logs each call's start time and duration. Record a slow-motion video of the lens and compare. Fill `observed_on_lens` in the JSON log by hand. The script cannot see the lens, so it never reports success on its own.

Only after this passes do we connect real alerts. Record results in the Measurements table of `docs/SDK_NOTES.md`.

### 5-0. The eleven operations

`python tools/hw_proof.py --extended --manual` runs all of them and writes `reports/hw_proof.json`, whose `procedure` list says which ran and where each result is logged. Without `--extended` or `--manual` the others are listed as not run.

| # | Operation | Logged in | Needs |
|---|---|---|---|
| 1 | Show one message | `calls` stage `1_show` | |
| 2 | Replace it with another | `calls` stage `2_replace` | |
| 3 | Perform 20 updates | `calls` stage `3_updates` | |
| 4 | Rapidly alternate messages | case `rapid_alternating` | `--extended` |
| 5 | Clear an old event after a newer message appeared | case `stale_clear` | `--extended` |
| 6 | Delay an old show until after a newer message | case `delayed_old_show` | `--extended` |
| 7 | Disconnect during an update | case `disconnect_during_update` | `--manual`, a person |
| 8 | Reconnect, if the SDK supports it | case `reconnect` | `--manual`, a person |
| 9 | Short and long messages within documented limits | case `short_and_long` | `--extended` |
| 10 | Multi-minute update soak | case `soak` | `--extended` |
| 11 | Clear the final message | `final_clear` (always run last) | |

Every call and case has three separate fields: `software_call` (`ok` or `error`, measured), `device_ack` (the string `UNKNOWN` until an SDK provides an acknowledgement and an adapter reports it) and `observed_on_lens` (empty until a person fills it in). Each case also carries `verdict` (empty, then `PASS` or `FAIL` by hand), `failure_category`, `pass_if` and `fail_if`.

### 5-1. How to record pass and fail

Watch the lens (slow-motion video helps), then fill in `observed_on_lens` in words and `verdict`. A verdict is only ever written by a person.

| If you see | Record |
|---|---|
| Older text on the lens when a newer message should be there, or old text returning after a newer message appeared (including after a stale clear or a delayed old show) | `FAIL`, `stale_message` |
| The lens blank, or missing part of its text, when a message should be visible | `FAIL`, `unexpected_clear` |
| The newest message never appears, is wrong, skipped or frozen, or our call raised for an update that should have worked | `FAIL`, `failed_update` |
| After the link comes back, the newest message does not appear without restarting the program | `FAIL`, `no_recovery`. If the SDK has no reconnect, write `NOT_SUPPORTED`, never `PASS` |
| Everything on the case's `pass_if` line | `PASS` |

The disconnect case has no pass without a documented expectation: record what the lens did and from which update our calls started to raise. A call that never raises after the link is cut means the adapter cannot see the loss, which is a finding to report, not a pass. The message length limits used in operation 9 are **ASSUMED** (12 characters, 2 lines, from the config) until the SDK documents them or someone verifies them on a lens. Re-run operation 9 with the documented limits and record both.

A software error with a visible correct result, or a visible correct result with a software error, are both worth recording: they mean software success and visible success disagree.

### 5a. Extended cases (after the small proof passes)

`python tools/hw_proof.py --extended [--soak-minutes 5]` and, with a person at the glasses, `--manual`. Each case is logged on its own and keeps three things apart:

| Level | Meaning | Who knows |
|---|---|---|
| `software_call` | Our call returned (`ok`) or raised (`error`), with its duration | the script |
| `device_ack` | The SDK or device confirmed it. **UNKNOWN**: the `DisplayAdapter` protocol returns nothing from the device and no SDK is known. Stays `null` until an adapter can report it | the SDK, if it offers one |
| `observed_on_lens` | What a person actually saw. Always `null` in the log until filled in by hand from the video | a human |

| Case | What it does | What to look for on the lens |
|---|---|---|
| `rapid_alternating` | 40 shows, A and B alternating, no pause | Only the last message, no flicker or ghost of older text |
| `stale_clear` | show A, show B, then `clear(A)` | B must still be there. A stale clear must not erase a newer message |
| `delayed_old_show` | show A is delayed 1.2 s on the link and times out at 0.4 s, then show B | B. If A appears, the old show overwrote the newer one and the adapter needs a sequence check. The log records the order the inner calls finished (`old_show_finished_after_newer`) |
| `short_and_long` | 1, N and N-1 characters per line, N from `display.caps` | Each message complete and unwrapped. N is **ASSUMED** (12) until the SDK documents it. Re-run with the documented limits |
| `soak` | One update every 2 s for several minutes | Always the newest message, no freeze, drift, dimming, flicker or disconnect. The log gives call count, errors and the slowest call |
| `disconnect_during_update` (manual) | 20 updates while a person cuts the link | Whether the lens keeps the last message or blanks, and from which update our calls start to raise. A call that does not raise after the cut means the adapter cannot see the loss |
| `reconnect` (manual, only if the SDK supports it) | 10 shows after the link is restored | Whether messages resume without restarting the program |

Durations in the log are call times on this computer. They are **not** end to end latency. That needs the whole path (sound, detection, display) measured with a slow-motion video of the lens.

## 6. Unknowns (all `UNKNOWN` until the SDK or the hardware answers)

| # | Unknown | Why it matters | Resolved by |
|---|---|---|---|
| 1 | SDK form (Android library, BLE protocol, cloud API, phone app only) | Decides adapter versus phone bridge | SDK docs |
| 2 | Package names, class and method names, connection and pairing steps | Needed to write any code | SDK docs, sample app |
| 3 | Can arbitrary text be sent, and icons | Our alerts are 2 text lines | SDK docs, hardware |
| 4 | Display size, lines, characters per line, color | Config assumes 2 x 12, mono green: **assumption only** | SDK docs, hardware |
| 5 | Update rate and minimum interval between updates | Blink emulation sends a call every 0.5 s | Hardware |
| 6 | Native blink, brightness control | Decides `supports_blink` | SDK docs, hardware |
| 7 | Does `show` replace the current message, or stack or flicker? | Section 3 assumes replace | Hardware |
| 8 | Does the display clear itself (screen-off timer, auto-timeout)? | Could erase an alert early | Hardware |
| 9 | Phone mandatory, supported OS, can a laptop connect directly | Architecture A versus B | SDK docs |
| 10 | Delay from call to photons | Latency target is untested | Slow-motion video |
| 11 | Reconnect behavior after a dropped link | The adapter must not hang detection | Hardware |
| 12 | Glasses microphone access and format | Today we use an external mic | SDK docs |
| 13 | Are pre-built code, pretrained models and our own speaker allowed | Event rules | Organizers |

## 7. What is verified, assumed and unknown

- **Verified (by reading this repository):** the protocol, the call path, the replace-by-show behavior, the timeout and queue rules, the stub, and that the adapters run on a laptop with the simulator.
- **Assumed:** 2 lines of 12 characters, mono green, no native blink. These are config placeholders.
- **Unknown:** everything in section 6.
- **Not done:** no M6 adapter, no SDK call, no physical test.
