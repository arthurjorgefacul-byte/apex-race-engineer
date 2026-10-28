# Coaching foundations used by feedback

This corpus supplies background and inspection priorities directly to coaching and debrief code. It is not a collection of preset braking marks, gears, tyre pressures or optimal lap times.

There are **42 circuit-family profiles, eight car categories and 336 compositional background combinations**. A combination means that sourced track context can be combined with category constraints; it does **not** mean a car-specific professional setup or elite telemetry model exists for that combination. Some smaller profiles currently provide circuit identity and generic section-analysis priorities, with that gap explicitly recorded. A missing exact track/layout or unknown category stays unknown.

The categories cover GT3, Formula/single-seater, legacy GT2/GTE, modern SRO GT2, GT4, prototypes, road cars and historic single-seaters. Exact known car IDs are mapped; a road-going Porsche GT3 RS is not classified as a GT3 race car. Formula and prototype are broad families, so they must not be treated as equal-performance classes for timing comparisons. Car systems, tyre windows, physics versions and BoP remain unverified until actual data establishes them.

Circuit bindings use exact IDs and explicit layouts, including AC's legitimate empty default layout. Renamed mod folders are not identified by fuzzy text. In the inspected installation, `F1-Hungaroring`, `F1-Melbourne` and `F1-Yas Marina` conflict with their own UI descriptions; they are deliberately excluded from automatic binding. A caller can explicitly verify a circuit family, but this still does not calibrate the mod's geometry or map detected segments to official turn numbers. GP, Sprint, historical, tourist and combined layouts never become comparable merely because they share terrain priorities. The older Kunos Zandvoort is separate from modern banked Zandvoort.

## What supports the feedback

`profiles.json` links every authored note to its sources. Real circuit facts and category descriptions come from the circuit operators, manufacturers, teams and suppliers themselves. Examples include [Brembo's braking-demand analysis](https://www.brembo.com/en/motorsport/formula1/infernal-circuits), [Mercedes' Monza engineering explanation](https://www.mercedesamgf1.com/news/insight-hitting-the-brakes-in-monza), [Pirelli's Spa analysis](https://press.pirelli.com/a-brand-new-track-surface-at-spa-francorchamps/), [Toyota's GT4 description](https://toyotagazooracing.com/gt4/about/) and [Toyota's hybrid engineering](https://toyotagazooracing.com/wec/engineering/). Their real-world numbers and tyre limits are not transferred to AC.

The original inspection rules combine those constraints with the cited AiM/Driver61 principles already in `knowledge.json`: compare by track position, relate release and throttle to steering demand, evaluate connected corners and the following straight, and test one change against a repeatable baseline. A circuit description selects a question to inspect; it cannot prove an error, tyre damage or setup fault. The measured feedback engine decides whether the driver's repeated clean data supports a finding.

The radio style is grounded in [McLaren's explanation of when engineers transmit](https://www.mclaren.com/racing/latest-news/mclarenracing/article/question-time/) and [Aston Martin's interviews with its race engineers](https://www.astonmartinf1.com/en-GB/news/feature/trust-focus-and-managing-the-mind-of-an-f1-driver). Two short actual examples come from Formula 1's own published [Austin 2015 radio](https://www.formula1.com/en/latest/article/say-what-the-best-of-race-radio-in-austin.6bO9lnmaa5XgQzuQPFidEq.6bO9lnmaa5XgQzuQPFidEq) and [Silverstone 2022 pit calls](https://www.formula1.com/en/latest/article/binotto-says-there-was-no-way-for-leclerc-to-win-the-british-gp-after-the.42Gc8Chie7I2pcDyCjMSf3). The app's wording is original: one action, one measured reason, quiet timing, and a longer explanation after the lap. The examples establish communication patterns; they do not import F1 pit or tyre rules into AC.

## Actual licensed human telemetry

`human_evidence.json` indexes **nine complete human laps from one independent research collection**, AssettoCorsaGym. Each is the fastest complete observed-clean lap **within one selected recording**, not the fastest driver in the dataset or a world record.

| Circuit | BMW Z4 GT3 | Dallara F317 | Mazda Miata NA |
| --- | --- | --- | --- |
| Red Bull Ring GP | 1:38.773 | 1:28.507 | 2:17.310 |
| Barcelona older GP | 1:59.810 | 1:41.420 | 2:41.967 |
| Monza GP | 1:58.740 | 1:51.090 | 2:57.570 |

Attribution: **Adrian Remonda, Nicklas Hansen, Ayoub Raji, Nicola Musiu, Marko Bertogna, Eduardo E. Veas and Xiaolong Wang, A Simulation Benchmark for Autonomous Racing with Large-Scale Human Data, NeurIPS 2024.** The data is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), independently of the code repository's MIT licence. The authors' [dataset card](https://github.com/dasGringuen/assetto_corsa_gym/blob/9e79864e4c5cf9971dc9cc260d20113b54198d9d/data/data_set.md) and [paper website](https://assetto-corsa-gym.github.io/) establish this provenance. They report mixed skill levels including a professional esports participant and four experts overall; the selected anonymous `20240313_OP` recording cannot be assigned that professional label.

All original `.ld` downloads are pinned to dataset revision `ad1df12c49cce10f159ba31967744750bb9edd8b`. Human-versus-AI labels are pinned to manifest commit `9e79864e4c5cf9971dc9cc260d20113b54198d9d`. Each index entry contains the source URL, source SHA-256, derived CSV SHA-256, tyre label, source lap number, conditions and transformation notes. Fuel consumption and tyre wear were disabled; penalties were disabled; weather, setup, BoP and physics versions are not fully verified. Therefore every bundled lap remains permanently **context only**, with **zero verified elite references**. A BMW example is never an automatic Mercedes target, nor an F317 example a target for a modern Formula hybrid.

The offline conversion reused the unmodified [gotzl/ldparser](https://github.com/gotzl/ldparser) reader under GPL-3.0 in the research workspace. The companion ships no copy of that parser and needs no NumPy or binary telemetry loader. `tools/extract_recordings.py` documents the reproducible conversion: select a complete lap, require no pit sample, no lap-invalidated sample and at most two tyres observed off track, trim only lagging-counter wrap samples, convert seconds/pedal units, and retain actual samples. These observations are not an official validity certification. CSV steering degrees remain an extra column rather than an invented normalized input. No pickle is loaded.

Additional independent source candidates are recorded separately in `source_candidates.json`. Their public cards declare MIT and original AC telemetry, but driver skill, human-versus-AI identity, timing reconstruction and exact layout are not validated here. One card contradicts its own title. They are not counted as bundled human or elite coverage.

## Integration contract

- `FoundationLibrary(data_dir=None).context(frame)` returns category, exact track match, phase-labelled principles, coverage, exact-car human evidence summaries, radio rules, sources and limits. It contains no full trace and performs no network or game access.
- `classify_car(car_id)` is a cached exact-ID lookup returning a coarse category or `None`. Prefer exact car/compound for pace grouping.
- `.category_for_car(car_id)` returns category display metadata with physics verification explicitly false.
- `.coverage(frame=None)` returns either current combination coverage or the full background coverage matrix.
- `.catalog()` exposes the complete cited corpus for inspection.
- `.evidence(id)` returns a full attributed trace only on explicit request and verifies its SHA-256 before parsing. Cached results are protected from caller mutation.

Feedback should attach only a phase-matching note to an actual measured finding. `confidence: engineering_inspection_priority` describes a reason to inspect data, never proof of causation. `best_driver_evidence` is retained as the caller-facing field name but its entries explicitly say research participant, selection scope, context-only and no elite verification. Missing telemetry and incomplete source metadata remain visible rather than being replaced by plausible numbers.
