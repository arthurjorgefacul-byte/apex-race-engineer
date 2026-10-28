# APEX 0.5

Home now controls recording: Start session, drive, then End session & debrief. Disconnects and new game sessions also end the recording after appropriate checks. Opening APEX never silently starts a recording. Existing saved sessions and reversible lap removal remain available in Lap analysis.

The debrief includes complete/clean laps, best clean lap, measured consistency and fuel use, strengths, three priorities and a next-session plan. It retains the last usable clean comparison if the session finishes in the pits, names the supporting lap/reference IDs, and removes findings whose supporting laps are deleted.

Race radio adds your best, session/race fastest, opponents' best laps, gaps ahead/behind/to the leader, compound choices and feedback. Quick Pit compound changes are acknowledged by the game; they prepare a future stop. Unspecified compounds prompt a choice and report matching-car compound pace when at least three valid observed laps support it. Unknown timing stays unavailable.

Requested replies speak without waiting for automatic coaching's straight-line gate. They wait for push-to-talk to release and respect speech mute. Settings can learn a wheel button or bind a keyboard key. No microphone opens while learning.

Corner feedback puts an action first, with brake-onset/release, deceleration, throttle/exit and recorded gear evidence behind it. Distinct clean comparisons establish repetition. Track/category background informs the inspection and experiment, rather than supplying an invented optimum.

Saved-lap analysis explains why a clean reference is unavailable, including measured fuel/temperature differences and the comparison band. It continues to show the recorded inputs. For example, the saved Porsche Lap 2 starts with 5.70 L more fuel than Lap 5, outside the 5 L band; the older clean pair remains comparable.

The background corpus covers 42 circuit families and eight categories, with 37 primary sources and nine attributed CC BY human AC research laps. These are not verified elite targets. Exact conditions, car physics and renamed mods still require care. See [foundations provenance](../reference_data/foundations/README.md).

Radio communication principles were researched using official [Aston Martin engineer interviews](https://www.astonmartinf1.com/en-GB/news/feature/trust-focus-and-managing-the-mind-of-an-f1-driver), [McLaren explanations](https://www.mclaren.com/racing/latest-news/mclarenracing/article/question-time/), and Formula 1's published driver/engineer exchanges. The generated wording is original, short and led by the needed action; fuller evidence is available after the lap.

## Acceptance checks on 28 October 2026

- Python core/workflow checks; overlay checks; native input/navigation and bounded shutdown checks; native Release build.
- Actual 32-bit and 64-bit AC Python 3.3 runtimes completed overlay GET, bridge POST and radio POST.
- Windows recognition correctly identified all nine new test phrases from generated audio. An actual HTTP requested reply while paused/stationary produced a non-silent 9.45-second WAV through the shipping speech worker. These tests used no microphone or speaker output.
- A read-only replay of 35,090 recorded Porsche GT3/Red Bull Ring samples produced several corner calls and five track-limit warnings. Debrief reconstruction retained the real clean-lap priorities and measured improvement. This was a recorded-data replay, not a new human driving test.
- Actual AC 1.16.4/CSP 0.3.0-preview581: bridge loaded, compounds and definitive player validity arrived, and a Hard request selected H in the Quick Pit menu with a matching acknowledgement. Mounted tyres remained Medium. No change=-1, S=0, M=1 and H=2 were verified against the visible menu, and the preset was restored to No change after testing. Other cars must match the verified complete spinner range or control is unavailable.
- A measured stationary overlay sample had mean update work below 0.1 ms and no update intervals over 33 ms. This is a local observation, not a guarantee about whole-game frame pacing or other apps/GPU work.

Windows reported no connected controller during the final run. Device identity, disconnect/release handling and button learning are covered by native checks; a physical G29 button hold remains unverified. Full-grid online timing and every mod's pit menu were not live-tested in this acceptance run. No proprietary Track Titan library or private professional telemetry is included.

Personal recordings/settings are preserved in the installed data directory and excluded from the portable archive.
