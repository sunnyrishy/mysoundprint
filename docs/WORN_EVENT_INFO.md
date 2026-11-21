# Worn hackathon: what the official site says

Source: https://worn-hacks.com/ (a single page app) and its pages `/m6-sdk`, `/participant-hub`, `/judges`, `/how-it-works`. Read on 2026-11-21 by fetching the site and reading its published page bundle, because the pages render in JavaScript. This file records **public site facts only**. The SDK kit itself (`Microlumin_Ship.zip`) is not in this repository and must not be: the organizers say to keep it on your own laptop, with no public repos, shared drive links or chats. Check the live site for changes, and treat anything marked UNKNOWN as not established.

## Event

- **Worn: Smart Glasses Hackathon**, Nov 20 to 21, 2026, City Tech (New York City College of Technology, CUNY), Academic Building, 285 Jay Street, Brooklyn, NY 11201. Free, in person only. Presented by Waftech and localhost:nyc, with Cornell Tech AI Society and Cornell Tech AI and Blockchain Club. MicroLumin provides the glasses and SDK support.
- **Theme (locked): Second nature.** "Build wearable AI that understands you, moves with you, and feels effortless." Three tracks: **Personal Insight** (presented with Wardah, privacy and consent built in), **Seamless Handoff** (moves fluidly between phone and glasses: glance on the glasses, depth on the phone), **Natural Interaction** (voice, touch and glanceable UI so natural you forget it is there).
- Teams of 4 to 5 are mandatory, assigned at team formation. Team lock: Friday 8:45 PM EST.

## Schedule (as posted)

| When | What |
|---|---|
| Fri Nov 20, 6:30 PM | Doors, check-in, dinner |
| Fri 7:15 PM | Welcome and kickoff |
| Fri 7:35 PM | Challenge brief and a walkthrough of the MicroLumin SDK |
| Fri 8:00 PM | Team formation |
| Fri 8:45 PM | Teams locked, hardware handed out, devices set up |
| Fri 9:00 to 10:30 PM | Hacking, "90 minutes to test out the hardware, with SDK help on hand". Out of the building by 10:30 PM |
| Sat Nov 21, 8:00 AM | Breakfast |
| Sat 9:00 AM | Team check-ins (progress update) |
| Sat 10:00 AM | Hands-on SDK and hardware workshop led by the Worn team |
| Sat 11:00 AM | Workshop (TBD) |
| Sat 12:30 PM | Lunch |
| **Sat 2:00 PM** | **Code freeze, submissions due** (Airtable form linked from `/participant-hub`) |
| Sat 2:15 PM | Demo expo, judges go table to table (3 or more judges per team, average score) |
| Sat 3:45 PM | Finalists announced |
| Sat 4:00 PM | Finalist stage demos, 3 minutes plus 2 minutes of Q&A |
| Sat 4:45 PM | Talk |
| Sat 5:30 PM | Awards (top 3 teams win prizes) |
| Sat 6:00 PM | Hardware return and wrap |

The judges page says "Judging 2:00 to 6:00 PM ET" and the hub says open house from 2:15 PM. Demo assignments and judging details were still "link pending" on the hub.

## Rules that matter to us (FAQ)

- **Glasses stay onsite.** "MicroLumin glasses are available for onsite use and testing in the testing room. Staff and security cameras will be present... The room will be closed whenever no staff are present." "All glasses must remain onsite and be returned to staff after use." This is stricter than the earlier assumption that teams take the glasses away.
- **Existing projects are allowed if the team discloses prior work and clearly identifies what it builds during Worn.** MySoundprint and its Sentinel engine were built before the event, so the submission must say so and say what was built at the event.
- **Code license.** Teams keep ownership. Open source is not required, but teams must give MicroLumin access to submitted code, and grant MicroLumin a non-exclusive, royalty-free license to review, run, reproduce, modify and use it for testing, demonstrations, research and product development, including commercial use. Third-party code keeps its own licenses.
- In person only. Teams must return Saturday to submit and be judged in person, or be disqualified.
- Late submissions and edits are not accepted after the deadline. The team representative may update the submission before it.
- Changes of team after the lock need organizer approval.
- Support: the Worn Discord, channel `#ask-for-help`, "@dean_ml261120 (Dean, MicroLumin)". An engineer from MicroLumin is online for SDK support.

## The M6 SDK setup guide (`/m6-sdk`, "Presented by Mickey")

**Kit:** `Microlumin_Ship.zip`, about 719 MB, on Google Drive (link on the site), "download on venue Wi-Fi". Start with `01_Start_Here/README.md` after unzipping. Keep it on your laptop only.

**Kit layout (from the site):**
- `01_Start_Here/`: kit README, iOS integration guide, Android SDK guide (the Android guide "covers every feature"; a "BTConnect usage doc covers connecting and messaging").
- `02_Android_SDK/mainapp-main-sdk-oversea/`: the Android SDK Gradle project. Your Android app is added as a new module. The SDK ships as modules of this project.
- `03_iOS_SDK/`: Swift plus `MLVisionM6SDK`. Fork MLDemo and add your screen. `GlassesSession.swift` wraps the whole SDK. Known SDK issues are listed in the README.
- `04_Demo_Apps/MLDemo/`: the iOS demo everyone runs first. Open `MLDemo.xcworkspace`, not `.xcodeproj`. Pods are already included, no `pod install`.

**Glasses (M6):** Android 9, 640 by 480 display. "A small screen where black is see-through: use a black background and bright text." Built-in apps (assistant, camera, recorder, translation) are driven by the phone app. Links: **Bluetooth for control and audio, Wi-Fi for media files.**

**Two kinds of app:**
1. **Phone app** (iOS or Android) that talks to the glasses over the SDK.
2. **Glasses app (optional)**: "a plain Android app, installed over USB with `adb`." "No special SDK needed. Talk to your phone or laptop over Wi-Fi."

**You need:** M6 glasses with USB cable; a Mac with Xcode 26.5 and an iPhone (iOS 15 or later) with cable (every team sets up with MLDemo on an iPhone first); for an Android phone app, Android Studio, JDK 17 or later and an Android phone with cable; for a glasses app, Android Studio, `adb` (Android platform-tools) and the glasses' USB cable. **The iOS Simulator and Android emulators do not work: the SDK needs a real phone with Bluetooth.** The first Android Studio sync takes about 5 minutes and downloads about 850 MB.

**Setup order:** charge the glasses on the USB cable (they will probably be flat; the first power-on needs a long press of the power button; if nothing shows, leave them charging a few minutes) then run MLDemo on the iPhone (choose your Apple ID team, change the bundle ID from `com.CHANGEME.mldemo` to something unique, enable Developer Mode on iOS 16 or later, trust the developer certificate, allow Bluetooth), tap "Set Up Glasses", pick the strongest signal, wait for "Connected".

**Glasses app path:** in MLDemo, with the glasses connected, turn on Developer › Developer Mode; unplug the glasses' USB cable and plug it into the computer; run `adb devices` and accept any prompt on the glasses. Then:

```bash
adb install -r app-debug.apk
adb shell am start -n com.you.app/.MainActivity
```

**Android phone app path:** open `02_Android_SDK/mainapp-main-sdk-oversea` in Android Studio, set Gradle JDK to 17 or later, sync, add a new Phone and Tablet module (it is listed in `settings.gradle.kts`), copy the dependency list and the manifest label fix and Bluetooth permissions from the kit README ("Building an Android phone app"). "Manifest merger failed" means the label fix was skipped.

**First lines of code shown on the site (class and method names exactly as published):**
- iOS: `MLVisionM6SDKManager.sdkInit(withDelegate:)`, `startScan()`, on `MLVisonBLEDiscoverDeviceNotification` call `stopScan()` then `startConnect()`, on `MLVisonBLEBindDeviceNotification` read `getGlassInfo()`.
- Android: `ConnectClientProxy(serviceCallback)`, `proxy.bindService(context)`, in `onServiceConnected` call `service.startBluetoothProcess()`, in `onScanResult(result, device)` call `proxy.connectDev(result)`, then `ConnectClientProxy.sendMsg(appId, false, json)`.

**Logs:** iOS MLDemo Event Log or `Documents/console.log`; Android and glasses: Logcat or `adb logcat`.

## What the site does not say (still UNKNOWN)

The display's text capabilities in characters or lines, the message JSON formats for `sendMsg`, whether `sendMsg` acknowledges delivery or visibility, update-rate limits, how the glasses join Wi-Fi, whether the glasses microphone is readable by an app, and what a glasses app can draw beyond ordinary Android views. These are in the kit documents, which we have not read. Do not guess them.
