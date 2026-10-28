# Race radio and measured feedback validation

The focused Python suite passes **46 tests**: 25 race/timing tests and 21 feedback/history tests. Run it with:

```
python -m unittest tests.test_race tests.test_feedback -q
```

The race tests cover current personal/session bests, named-driver bests, ghost exclusion, race neighbours and leader, gap sign/rank disagreement, practice/race transitions, replay and stale-data rejection, reordered CSP snapshots, coherent player validity, session identity enrichment, observed compound pace, pit/compound-change exclusions, duplicate player-lap observations, compound choice prompts, guarded pit commands, acknowledgements and expiry.

Compound pace groups the **exact car ID and compound**. It is the median of up to twelve observed valid completed laps, with driver names and sample counts. At least three matching-car samples are needed before the engineer names the quickest observed option. The answer still asks the driver to confirm a compound. Fuel, traffic, tyre age and driver differences are not controlled, so this is an observed pace comparison rather than a causal tyre-performance prediction. A session best is never assigned to the compound currently mounted.

Pit commands select a future Quick Pit service. A command requires a fresh matching car/track/layout/session, a verified compound mapping, an editable spinner and an in-range integer value. A request remains queued until CSP reports the same command ID and requested spinner value. Rejection, expiry or missing confirmation must not be described as an applied tyre selection. Unit tests exercise this protocol with fixtures; they do not establish a live CSP mapping.

Feedback tests cover distinct-lap repetition, reference/section identity, action priority, brake onset/release, observed deceleration, throttle/exit interactions, missing/observed gear comparisons, repeatable strengths, source principles, input immutability and stored-history independence. Track/category context selects a relevant inspection and adds it to the experiment; measured telemetry still determines the instruction. Neither category membership nor published research recordings establish an optimal marker, gear or lap time.

## Installed CSP SDK audit

The bridge calls were checked against the installed CSP app SDK (`extension/internal/lua-sdk/ac_apps/lib.lua`). Its documentation confirms:

- `ac.SessionType.Race` is **3**, whereas original AC shared-memory race is **2**. The bridge preserves the distinction.
- `ac.getGapBetweenCars(0, other)` is negative when the player is ahead. During practice/qualifying it compares best laps, so those values are excluded from race-gap answers.
- `getTyresName`, `getTyresLongName`, `getPitstopSpinners`, `setPitstopSpinnerValue`, `currentQuickPitPreset`, car validity/timing fields and session storage are available APIs.
- `currentQuickPitPreset` is zero based. A Lua numeric zero is truthy, so the preset-zero guard accepts it.
- Remote/replay cars can lack physics-only channels. Definitive player validity is published only when physics is available and applied only to a coherent player sample.

The public SDK source is [acc-lua-sdk](https://github.com/ac-custom-shaders-patch/acc-lua-sdk).

## Live compound acceptance — 28 October 2026

Tested in **original AC 1.16.4, CSP 0.3.0-preview581**, using the **Kunos Porsche 911 GT3 R 2016** on **Red Bull Ring GP**. The visible Quick Pit tyre labels were compared with the live bridge values:

| Quick Pit label | Numeric value |
| --- | ---: |
| No change | -1 |
| S | 0 |
| M | 1 |
| H | 2 |

An APEX request for **Hard** changed the future Quick Pit tyre selection to **H**. CSP acknowledged the command with `ok=true` and `actual_value=2`; APEX reported the plan as applied. The mounted compound remained **Medium**, confirming this prepares pit service rather than replacing tyres on track. The Quick Pit selection was then restored to **No change (-1)**.

The bridge now uses zero-based compound values only when each car's reported editable spinner has **minimum -1 and maximum compound-count minus one**. A different range leaves compound control unavailable. This runtime check is a compatibility guard, not evidence that every mod or compound order has been individually validated. The above car/CSP combination is the live acceptance case.

A read-only check of the current CSP log confirmed `ApexBridge` loaded successfully; no matching Lua error or stack traceback was found in that log. Voice and physical wheel-button acceptance are separate checks.
