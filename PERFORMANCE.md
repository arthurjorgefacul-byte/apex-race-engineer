# APEX 0.4 additions

The window closes before background cleanup. Speech output reuses one worker; microphone input exists only on a push-to-talk request and ends on release. Routine radio events use bounded queues, expiry, generation checks and safe gaps. The overlay continues to use its paced background HTTP worker and two-car sampling batches; new menu callbacks only enqueue a bounded request. No further AC graphics changes were made in 0.4. See VALIDATION.md for measured replay and microphone costs and the Steam launch limitation.

# AC performance check — 27 October 2026

Tested on this computer with original AC 1.16.4, CSP 0.3.0-preview581, the Mercedes-AMG GT3 at Red Bull Ring GP, and the native APEX desktop app. AI drove the test so these laps must not become driver coaching references.

## Changes applied

- APEX reads at most two opponents per sampling step, caches names and track identity, and changes only overlay labels whose values changed. Network requests stay on its worker thread.
- Pro Engineer process-presence checks are shared and cached for 0.5 seconds. An uncached Windows process scan measured approximately 16.6 ms on this computer.
- The game overlay requests a compact response, without copying full lap graphs. The desktop polls twice per second, allows one poll at a time, reduces polling when minimized, and stops redrawing hidden charts.
- AC's `EXPORT_ENTIRE_SESSION` changed from `1` to `0`. The overnight session had accumulated almost six million samples in some built-in channels. APEX continues saving its own telemetry and lap analysis.
- AC's `FPS_CAP_MS` changed from `0` to `10`, targeting 100 FPS. GPU utilization had measured 92% in the stationary single-car scene; this cap limits rendering demand. Other graphics quality settings were left at their existing values.
- Python developer apps were enabled temporarily to run the AI test; their original disabled setting is restored after testing.

## Measurements

The 60-second moving test after warm-up produced an average AC Python update interval of **10.000 ms**, a worst sampled five-second p95 of **10.403 ms**, and a longest observed interval of **26.795 ms**. No sampled interval exceeded 50 ms. APEX's own update work averaged **0.0313 ms**, with a **0.4041 ms** peak.

AC's Python callback intervals are a frame-pacing proxy, not a GPU presentation capture. The largest value during initial session/UI loading was higher and is excluded from the warmed driving interval. This was a single-car test; it does not establish performance in multiplayer, with large fields, or with every mod.

An additional fake-AC 64-car benchmark reduced the maximum game API reads in one callback from **516 to 20**, and total reads over ten seconds from **9,804 to 2,544**. A lap-timer-only change caused one UI text update instead of thirteen. These are workload measurements, not an FPS comparison.

Saved-session browsing and exports use independent read-only WAL snapshots. A full **240 MB / 288,761-frame** export completed in 30.567 seconds while 135 concurrent live requests received 135 distinct telemetry packets. The longest live response measured 39.908 ms; the export did not freeze live recording.

## Restore your previous game settings

Run `Restore performance settings.cmd` while AC is closed, then start AC again. It restores only the frame cap and whole-session export values from the dated backups, retaining other settings and APEX recordings.

Backups are next to their original files:

- Documents/Assetto Corsa/cfg/video.ini.apex-before-fps-cap-20261027.bak
- Assetto Corsa/system/cfg/assetto_corsa.ini.apex-before-full-session-export-20261027.bak

The original recording database was also backed up as `data/sessions-before-0.3.sqlite3`. It is kept on this computer and excluded from downloadable release/source archives.
