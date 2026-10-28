# Sources and reference laps

This catalogue adds cited coaching principles, current official Red Bull Ring corner names, real-world timing context and one genuine published human Assetto Corsa lap. It does not turn a real Formula 1 record, an unrelated GT3 car or an unknown setup into a driving target.

The coaching notes in `knowledge.json` are short original paraphrases. They link to AiM's own analysis manual and Scott Mansell's Driver61 lessons. Each note distinguishes a mechanism, a measurement to inspect, an experiment and its limitations. They are hypotheses to test against the driver's data. No paid article, chart, map image or full lesson is reproduced.

`catalog.json` uses the circuit operator's current numbering and sponsored corner names. The official current layout has ten numbered turns. Our detected corner segments are a different representation: we have no verified normalized AC positions for those official turns, so the code does not rename seven detected segments as seven guessed official turns. The official current centre-line length also must not replace the length reported by an older AC track.

## The bundled human lap

`acgym_rbr_bmw_human.csv` is derived from a real human research recording in AssettoCorsaGym, by Adrian Remonda and collaborators, published for NeurIPS 2024. The dataset is explicitly [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Its data license is separate from the code repository's MIT license.

- Dataset: https://huggingface.co/datasets/dasgringuen/assettoCorsaGym
- Paper and authors: https://assetto-corsa-gym.github.io/
- Pinned dataset revision: `ad1df12c49cce10f159ba31967744750bb9edd8b`.
- Recording: `20240313_OP`, BMW Z4 GT3, Red Bull Ring GP, fourth numbered lap, 2024-03-13. Reported lap time: **1:38.773**.
- Attribution: **Adrian Remonda et al., A Simulation Benchmark for Autonomous Racing with Large-Scale Human Data, NeurIPS 2024.**

This is a research example, **not an expert-lap or world-record claim**. Fuel consumption and tyre wear were disabled. The source reports medium slicks, 20 °C air, 27 °C road and full grip. Penalties were disabled; no more than two tyres were observed off track. Setup, game/mod versions, BoP and weather are not fully verified. It is permanently marked context-only in the bundled metadata and cannot become a Mercedes-AMG GT3 target because both cars share a class.

The sidecar JSON records the original download URL, source and derived-file SHA-256 hashes, time, licence, attribution and transformations. We selected the recorded lap, removed only the post-finish position-wrap sample caused by a lagging lap counter, and converted pedal percentages and time units. The CSV retains actual sample values. Steering remains in an extra degree-valued column; it is not converted to normalized steering without a verified lock angle. No trajectory was invented, and no pickle file was loaded.

The offline conversion used the existing [gotzl/ldparser](https://github.com/gotzl/ldparser) reader under GPL-3.0, kept with its licence in the research workspace. The application does not ship or execute that parser; it reads the derived, attributed CSV using its standard-library importer.

## Importing a driver's own reference

`ReferenceLibrary(data_dir).import_csv(csv_path, metadata)` accepts one UTF-8 CSV lap and stores its validated trace under `data_dir/references/`. It never uploads data. The canonical columns are:

```text
normalized_pos,lap_time_ms,speed_kmh,gas,brake,steer,lat_g
```

`normalized_pos` and pedal inputs are ratios, time is milliseconds and speed is km/h. `steer` and `lat_g` are optional. Steering must be a normalized input if supplied. MoTeC-style preambles and explicit unit rows are accepted for time, distance, speed and pedals. A distance column requires track length in metres. Ambiguous speed or pedal units, formula strings, NaN, position wraps, reverse movement, partial laps, large gaps and mismatched reported lap times are rejected. This is not a general binary `.ld`, `.ibt` or pickle loader.

Metadata requires kind (`simulator` or `real_world`), game, exact car/track/layout/compound identifiers, ISO recording date, source URL, licence and attribution. For an own export, use `source_url: "local://user-export"`, `license: "LicenseRef-UserOwned"` and explicitly confirm `rights_confirmed: true`; no public upload is required.

An explicit empty `layout: ""` is AC's legitimate default layout. It matches only another explicit empty value; a missing or null layout remains unknown.

Imported data can be inspected even when it cannot be a target. Automatic eligibility requires exact original-AC identity, verified source identity/conditions/clean-lap checks, matching game/car/track versions, setup and BoP, and known comparable weather, temperature, grip, fuel, ballast, restrictor and consumption settings. Missing information produces an explicit reason. These screening windows are application policy, not a guarantee of physically identical conditions. Real-world or other-simulator laps always remain context-only.

## Public module interface

- `ReferenceLibrary(data_dir).catalog(context=None)` — sources, principles, tracks, timing context and reference summaries with eligibility reasons.
- `.import_csv(path, metadata)` — validated full reference object.
- `.get(reference_id)` — full stored or bundled trace.
- `.compatibility(reference_id, context)` — compatibility and reasons, without altering live analysis.
- `load_knowledge()` — dictionary containing `sources` and `principles`.
- `track_info(track_id, layout)` — verified names and mapping limitations, or `None`.
- `compatibility(metadata, context)` — explicit metadata-screening result.
- `principles_for_corner(corner)` — cited inspection hypotheses selected from measured section loss, entry/exit speed and brake/throttle position deltas. Each result includes its evidence, text, limitations and resolved source URLs. Release shape, coasting, tyre balance and steering faults are never inferred from these scalar deltas alone.
