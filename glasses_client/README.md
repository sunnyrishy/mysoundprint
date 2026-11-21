# MySoundprint glasses client

A tiny plain Android app for the M6 glasses. It connects to the MySoundprint laptop's existing WebSocket (`/ws`), shows white text on black for `alert` messages, and clears on `clear`. It ignores every other message and never receives audio. No SDK, no blink, no animation.

**Status, stated plainly:** the app **compiles** and its 8 message-rule unit tests **pass** (built and tested in a cloud sandbox with Android SDK 34, Gradle 8.7, JDK 21). It has **never been installed on a pair of glasses**. Nothing is known about how it looks on the lens, or whether the glasses accept `adb reverse`, until a person tries it.

Why this design: the organizers say a glasses app is an ordinary Android app installed with `adb`, with no special SDK. The glasses are reached from the laptop over the USB cable with `adb reverse`, so no Wi-Fi setup is needed (standard adb, UNVERIFIED on these glasses).

## Fastest way: install the prebuilt APK

`prebuilt/mysoundprint-glasses-debug.apk` (debug build, SHA-256 `e6ca633fba51448eb9c14ca67617acb20d91a9fedc1b62af644e2f990a9396ab`). Check it with `certutil -hashfile prebuilt\mysoundprint-glasses-debug.apk SHA256` on Windows. Then, with the glasses in Developer Mode and plugged into the laptop:

```bash
adb devices -l                        # the glasses must be listed as "device"
adb install -r prebuilt/mysoundprint-glasses-debug.apk
adb reverse tcp:8765 tcp:8765         # glasses localhost:8765 -> laptop localhost:8765
adb shell am start -n com.mysoundprint.glasses/.MainActivity
```

If `adb install` fails (for example "INSTALL_FAILED_..."), paste the exact message. If `adb reverse` fails, the glasses may not support it: tell us, and the app can use a Wi-Fi address instead (`--es url ws://<laptop-ip>:8765/ws`, and start the laptop with `--lan`).

## Or build it yourself

Open this `glasses_client` folder in Android Studio (File, Open), let Gradle sync, then Build, Build APK(s). Or from a terminal here:

```bash
./gradlew :app:assembleDebug          # Windows: gradlew.bat :app:assembleDebug
./gradlew :app:testDebugUnitTest      # the 8 message-rule tests (run on the computer, not the glasses)
```

The APK is `app/build/outputs/apk/debug/app-debug.apk`. Android Studio creates `local.properties` with your SDK path.

## First physical test, step 1: no laptop pipeline at all

```bash
adb shell am start -n com.mysoundprint.glasses/.MainActivity --es line1 HELLO
adb shell am start -n com.mysoundprint.glasses/.MainActivity --es line1 DOORBELL
adb shell am start -n com.mysoundprint.glasses/.MainActivity --ez clear true
```

Look at the lens after each command and write down exactly what you see. That shows display, replacement and clearing on the real glasses, driven only by adb.

## Step 2: through the MySoundprint bridge

On the laptop, in the repository root:

```bash
python tools/hw_proof.py --first-test --display bridge --wait-s 10
```

It starts the WebSocket on `ws://127.0.0.1:8765/ws`, waits for the glasses client to connect, then sends HELLO, DOORBELL, clear. Record `observed_on_lens` and `verdict` by hand in `reports/hw_proof.json`. Later cases (`--extended`, `--manual`) only after this works. Then real alerts: stop `hw_proof`, and run `python tools/run_live.py --display bridge` (live microphone, default config). Its alerts go through the same WebSocket, and the browser page at `http://127.0.0.1:8765/` shows the same messages. `tools/demo.py` does not take `--display`, so it is not the path for the glasses.

## What the app does and does not do

- `alert`: replaces whatever is shown. `clear` with an event id: removes the message only if it is the one on screen. `clear` with no id: clears everything.
- Messages are handled in the order received on one connection. When the link drops it clears the screen and reconnects every second, and it clears again on reconnect, so an old alert never stays on the lens.
- It keeps the screen on, in landscape, and shows a small grey "MySoundprint connected" for 3 seconds after connecting, or "no link" while disconnected. Black is see-through, so the status text may be visible on the lens: note that.
- Nothing is claimed about message length: long text may not fit. Record how it renders.
- Local test extras: `--es url ws://...` changes the address, `--es id e1` sets an event id with `--es line1`.
