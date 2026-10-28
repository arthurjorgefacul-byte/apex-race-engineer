# APEX 0.4 validation — 27 October 2026

The native Windows build completes with zero warnings and zero errors. Tests use isolated data folders and silent output unless specifically identified below. Personal recordings are preserved and excluded from the portable/source archives.

- **161 core tests and 32 overlay tests** cover recording/history, real finish-line packet ordering, imports/provenance, multiple live corner reports, event expiry, safe-gap scheduling, urgent track-limit calls, mute/quiet controls, requested answers, source changes, owned microphone lifetime and speech-worker shutdown.
- **Five native shutdown scenarios** pass, including a hung health endpoint, PID mismatch, preserving unrelated processes and explicitly keeping recording. Hung cleanup measured approximately **1.68 s** after the UI exits.
- **Visible shutdown** was observed through the native window inventory: the APEX window disappeared within **181 ms** of the close action. This observation includes input/automation time; it is not a compositor benchmark.
- **Actual recorded AC replay:** 14,671 original Mercedes-AMG GT3 / Red Bull Ring packets were read without changing the original database and replayed into an isolated recorder and live engineer. The run emitted 11 track-limit events, 4 pit events, 36 status/periodic events and 12 corner events. Several corners were addressed in the same lap; measured live exits covered detected sections 2, 3 and 6 across the recording. Only comparable, eligible sections produced driving advice.
- Added live-engineer update cost in that replay: **0.0694 ms median, 1.3821 ms p95, 2.5289 ms maximum**. This measures the new radio calculations, not the entire app, GPU frame time or an in-game stutter guarantee.
- **Windows speech recognition:** a generated WAV saying “fuel status” passed through the actual Windows en-US recognizer and returned the correct fuel command. This checks the recognizer/protocol, not a particular driver's microphone accuracy.
- **Actual local input-device release:** the default microphone opened on a requested start and stopped **15.10 ms** after release. No input audio was saved. Cancellation also terminates only the owned input worker if the audio driver/recognizer is busy; startup remains visible as “MIC STARTING.”
- **Speech output:** the persistent Windows worker completed two phrases to a WAV output and exited in 29 ms on close. Unit checks cover process reuse, forced interruption, expiry, clear/mute and startup/close races. The output worker is separate from push-to-talk input.
- **Embedded AC Python:** both official CPython 3.3.5 x64 and x86 processes, using AC's actual `Python33.zip` and the supplied socket extensions, loaded the updated app and passed real loopback overlay GET, timing POST and engineer-question POST. `ssl`, `select` and `ctypes` were not needed. The main-thread test double rejects all AC API calls from the network worker.
- **Native visual checks:** live quick buttons, saved session/lap selection, A/B graphs, corner details, reference/source displays and Windows scaling were inspected in the actual application. Screenshots and review notes are supplied separately under `outputs/UI-review-v0.4` in the development workspace.

## Current test limit

A fresh in-game visual check of the expanded quick menu was attempted through Content Manager, but AC stopped with **“Steam API has failed to initialize.”** Steam was already running. The error was dismissed; Steam was not restarted and no login or security settings were changed. This release's in-game menu therefore has actual embedded-runtime and protocol verification plus simulated AC UI tests, but **not a completed fresh rendered-game visual check**. The recorded-data replay is explicitly not presented as a new driving session.

Published human AC data and real F1 timing are labelled with provenance and compatibility limits. No bundled external lap is an automatically activated driving target, and no private real-world engineer telemetry or optimal-lap training is claimed. Voice commands currently use the installed English (United States) recognizer.

---

Previous 0.3 validation follows for historical context; its older UI/close descriptions are superseded above.

# APEX 0.3 validation — 27 October 2026

## Native Windows application

- Release WinForms build passed with **0 warnings and 0 errors**. The interface uses native Windows controls and drawing; no browser or WebView is embedded.
- Desktop launchers open Apex.exe, preserve the saved backend choice, and manage a hidden local companion. Script syntax checks passed for startup, overlay installation, shortcut creation and performance restoration.
- Closing the app and its owned companion was checked. A separately started QA companion was reused without becoming owned by the desktop; it was stopped separately after testing.
- At the computer's display scaling, saved-session selection, input/reference plots, detected-corner comparisons, reversible removal, Show removed laps and restoration were exercised using isolated synthetic recordings. Both laps were present again after restoration.
- The native Export full session flow produced valid JSON with two synthetic laps and all 231 recorded frames. Your real laps were not used for removal tests.

## Automated checks

**128 checks passed:** 103 core tests and 25 overlay tests. The core suite includes persistent history, soft removal/restoration, reference and fuel recalculation, native protocol compatibility, source/mode boundaries, HTTP validation, process ownership, streaming exports and finish-line timing.

Eight storage tests establish read-only WAL snapshots, continued live writes during export, exact streaming JSON equivalence, bounded allocation, early unknown-session validation and cleanup. A 13 MB synthetic export stayed below 4 MB of measured Python allocation.

Seven finish-line regression tests include sanitized packets captured from the actual AC session. They cover the timer/position wrapping before the counter, the opposite order, first partial laps, bounded synchronization, recovery after invalid boundaries, and AI exclusion.

## Actual AC verification

- Original AC 1.16.4 with CSP 0.3.0-preview581, Mercedes-AMG GT3, Red Bull Ring GP.
- The game successfully loaded RaceEngineer, displayed its live overlay and supplied timing, inputs, fuel and tyre data.
- The real test exposed missing embedded-Python modules. Official Python 3.3.5 x64 and x86 socket extensions were bundled with their license and provenance. Both architectures then passed isolated tests using AC's own standard-library ZIP: import, app startup, HTTP GET and JSON POST. Networking needs no SSL, select or ctypes extension.
- The test also exposed a roughly 40–60 ms handoff between AC's timer/position wrap and lap counter. The recorder now joins that bounded handoff. The saved database subsequently contained 19 AI laps retained as excluded, and a later live session with 12 laps, seven observed clean.
- A 60-second moving test after warm-up averaged a 10.000 ms Python callback interval, with a worst sampled five-second p95 of 10.403 ms and a longest observed interval of 26.795 ms. No interval exceeded 50 ms. Overlay work averaged 0.0313 ms and peaked at 0.4041 ms. Callback intervals are a pacing proxy, not a GPU presentation capture.
- A full export streamed **240,042,395 bytes / 288,761 frames** in 30.567 seconds while 135 concurrent live requests received 135 distinct telemetry packets. The longest live response measured 39.908 ms.

See PERFORMANCE.md for the applied game settings, measurement limits and restoration steps. Temporary developer apps were returned to their original disabled setting after the AI test. The original recordings and a pre-update database backup remain on this computer; neither is included in release/source archives.

## Upstream reuse and limits

The unmodified official **AC Pro Engineer v0.5.1** binary is included. Binary/source archives were verified against the release checksums; provenance, corresponding source and original notices are preserved. Its real Formula demo and both JSON streams were previously run through the adapter. Compatibility tests retain those actual packets, including the release's zero-valued summary sequence behavior.

The released Rust engine is unchanged; its Rust suite was not rebuilt. New live tests above used the APEX local backend, which preserves validated native AC energy/control channels and records the extra distance comparisons. Pro Engineer's live TUI still needs Start/Enter in its own window. Its independent stint history is unaffected by removing a lap from APEX analysis.

Offline Windows speech synthesis was checked previously. Audible playback through your preferred device, large-field multiplayer performance and every car mod have not been established. Automatic garage setup changes, optimized ERS deployment, Simulator Controller, Crew Chief and Track Titan integration are not implemented.