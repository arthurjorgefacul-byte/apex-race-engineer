# APEX session bridge

This CSP app supplies the APEX desktop engineer with session timing and player validity that the original AC shared-memory interface does not reliably expose. It works alongside the **RaceEngineer** Python overlay.

Install both apps with the package's **Install Overlay.ps1**. Enable **RaceEngineer** in Content Manager → Settings → Assetto Corsa → Apps, and **APEX session bridge** in the CSP App Shelf. Enter a new driving session after enabling them. Keep the APEX desktop application running. The bridge works in the background; its small status window is optional.

The bridge samples up to 64 connected cars in small batches. When CSP exposes them, APEX receives driver/car identity, position, current/last/best laps, current compound, validity and pit status. Race-gap values are estimates based on distance and speed. During practice and qualifying, CSP's gap function compares best laps, so APEX does not present it as a physical race gap. Ghost best laps are excluded from current-session records.

Opponent compound pace uses observed completed valid laps, grouped by the exact car and compound. It does not infer which compound set a reported session best. Fuel, tyre age, traffic and driver differences can affect the pace. Opponent fuel, tyre pressures/temperatures/wear and garage setups are not supplied by this bridge.

## Choosing tyres for the next stop

Ask **“change compound”** to hear the available options and any sufficient observed matching-car pace. Confirm a named compound, such as **“change to hard”**. APEX prepares the game's Quick Pit tyre selection and waits for the game to confirm the selected value. This changes the next pit service; it does not replace mounted tyres while driving.

The compound mapping was checked live in **AC 1.16.4 / CSP 0.3.0-preview581**, with the **Kunos Porsche 911 GT3 R 2016 at Red Bull Ring GP**: No change=-1, S=0, M=1, H=2. An APEX Hard request was acknowledged at value 2 while the mounted tyres remained Medium. The selection was restored to No change after testing.

Each car must report an editable spinner with minimum **-1** and maximum **number of compounds minus one** before APEX enables this mapping. Other ranges remain unavailable and require selection in AC's pit menu. This guard does not mean every mod's compound order has been independently tested. If the game rejects a request or confirmation expires, check the pit menu; APEX must not claim the selection succeeded.

## Local operation

The Lua app uses CSP's session-shared storage. The Python overlay relays that information to the desktop companion on the same PC using its background worker. The Lua app makes no network requests or file writes, and does not alter vehicle physics. Its only car-related write is the guarded, acknowledged Quick Pit compound selection requested by the driver.

New sessions change the bridge session identity. Requests also carry the exact car, circuit/layout, session identity and a short expiry, so an old request cannot be applied to a different session. Stale snapshots are discarded.

API source: [CSP Lua SDK](https://github.com/ac-custom-shaders-patch/acc-lua-sdk). Acceptance details: [race radio validation](../../docs/RACE_RADIO_VALIDATION.md).
