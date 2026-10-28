# APEX 0.5.1 — playtester guide

Thanks for trying APEX, a local race engineer for **original Assetto Corsa on Windows PC**. Please report confusing advice and missing data as well as bugs. Allow roughly 20 minutes after setup. The ZIP's top-level **START-HERE.txt** is the short guide; this detailed guide lives in **APEX Race Engineer/playtest/**.

## Before you start

You need Windows x64, original AC, Content Manager/CSP, **.NET 10 Windows Desktop Runtime (x64)** and **Python 3.10+**. These runtimes are separate from AC's bundled Python.

1. Extract **APEX-Playtest-v0.5.1.zip** completely to a writable folder. Open its **APEX Race Engineer** folder and keep the files together. Run `Apex.exe` there.
2. Check your installed apps for **.NET 10 Windows Desktop Runtime (x64)** and **Python 3.10+**. If APEX will not open or reports a missing runtime, use [Microsoft .NET 10](https://dotnet.microsoft.com/en-us/download/dotnet/10.0) — **Windows Desktop Runtime, x64** — or [Python for Windows](https://www.python.org/downloads/windows/). The .NET SDK is unnecessary.
3. Voice input needs a microphone/default input device and the **English (United States) Windows speech recognizer**. Add [speech language features through Language & region](https://support.microsoft.com/en-us/windows/hardware/input-devices/manage-the-language-and-keyboard-input-layout-settings-in-windows) if needed. Quick buttons and typed questions work without voice input.

No account, paid API or cloud service is required for APEX's local engineer. Keep the included source and licence files with the package.

## Set up once

1. Close the AC driving session. Run **Install Overlay.cmd**. It installs the overlay and CSP bridge, preserving other apps and backing up changed settings. Report its message if it cannot find AC.
2. In Content Manager, enable **RaceEngineer** under **Settings → Assetto Corsa → Apps**. Enable **APEX session bridge** in CSP's **App Shelf** if it is not already enabled. Start a new driving session after installing.
3. Open **Apex.exe**. Use **APEX live engineer**, the package's starting engine. The optional Pro Engineer is unnecessary for this checklist.
4. Press **Radio check** to confirm the speech output. In **Settings**, keep **Spoken engineer feedback** enabled.
5. Choose an unused talk key, or **Bind wheel button…**: release the buttons, press the desired button and **Save preferences**. Learning should show the device/button and keep the microphone off. Release the button after reconnecting a wheel.

## A useful first test

Choose a familiar car/track and keep the compound/setup fixed. Drive yourself; AI laps are excluded from driving coaching.

- [ ] **Home:** connect to AC and confirm that APEX stays on Home until you press **Start session**.
- [ ] **Start:** press **Start session**. The live engineer should open and show your car, track, fuel and inputs. In AC, open **Race Engineer** from the app sidebar.
- [ ] **Build evidence:** complete the initial out/partial lap, then **two full clean laps**. Fuel/temperature differences can still prevent comparison; the lap sheet should explain why.
- [ ] **Radio:** are calls timely, understandable and useful across several measured sections? Note naturally occurring track-limit calls; deliberate excursions are unnecessary.
- [ ] **Ask:** try **fuel status**, **my fastest lap** and **feedback** with quick buttons or text. For voice, hold your button, wait for **MIC LISTENING**, speak in English, then release. First startup takes a moment. A recognised command should close input and answer aloud and in text. Test a reply in the pits or paused too.
- [ ] **Lap analysis:** choose the session and a comparable clean pair. Inspect several corners; hover for values, drag to zoom and use **Full lap**. Is the next action clear?
- [ ] **Remove/restore:** on your own test lap, use **Remove lap**, then **Undo removal** or **Show removed laps → Restore**. The lap should leave/rejoin comparisons; original frames are retained.
- [ ] **Finish:** **End session & debrief** should return to Home with useful figures, strengths and priorities. Reopen APEX and check the saved session/debrief.
- [ ] **Close:** close the window normally. Note any delay, frozen window or microphone that appears to remain active.

## Optional race and tyre checks

In a race, try **fastest lap of the race**, **other drivers best laps**, **gap ahead**, **gap behind** and **gap to the leader**. Check **Race timing** against the game. Gaps are estimates requiring fresh CSP race data.

While stationary, ask **change compound**. APEX should list this car's options and ask which one. Choose one and check Quick Pit: success needs game confirmation. This prepares a **future pit stop**. Restore your original selection afterward. Unsupported menus should return unavailable/unconfirmed. Compound pace needs enough valid observed laps and may be unavailable in a short test.

## What to report

Fill in **FEEDBACK-TEMPLATE.md** in this folder. One specific example is useful. If startup fails, include the exact message and the installed .NET/Python versions if known.

Review optional screenshots, `data/companion.log` and session exports for names, paths and telemetry before sharing. Only relevant evidence is needed; the entire `data` folder is unnecessary. APEX's microphone input is processed locally and is not saved/uploaded.

## Current limits

Original AC only; ACC/AC EVO are unsupported. C1/C2 labels are detected sections, not official turns. Unknown channels/mod identities stay unavailable. Bundled human research laps contain **zero verified elite benchmarks**; comparisons use your clean laps. APEX does not apply garage setups.

Wheel input exposes up to 32 buttons per device through Windows' legacy joystick interface. Fresh-PC setup, physical wheels, voice recognition, full online grids and every mod's pit menu have not all been verified; these are important playtest targets.
