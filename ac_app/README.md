# Assetto Corsa in-game overlay

This folder contains a small, read-only Python app for the original Assetto Corsa on PC. It displays the Race Engineer companion's lap, delta, fuel, tyre, and coaching text in a 420 x 240 in-game window.

## Enable the app

1. Start the Race Engineer companion on your desktop.
2. Install/copy the complete `RaceEngineer` folder, **including `stdlib/`**, into your Assetto Corsa installation's `apps\python` folder. The resulting file is `assettocorsa\apps\python\RaceEngineer\RaceEngineer.py`. The project's overlay-copy script can perform this copy for you.
3. In Content Manager, open **Settings → Assetto Corsa → Apps** and enable **RaceEngineer** (it may be displayed as **Race Engineer**).
4. Start a driving session. Open the app list on the right side of the screen and show **Race Engineer**.
5. Select **Live** in the desktop companion for real driving data. Select **Demo** only when you want sample data.

The app retains Assetto Corsa's normal draggable title bar. Move it where it is comfortable to glance at, or hide it using the app sidebar.

## Connection states

- **LIVE TELEMETRY:** the companion reports a live connection, and its most recent response is fresh.
- **DEMO | SAMPLE TELEMETRY:** explicitly selected sample data. This never displays as a live game connection.
- **WAITING FOR LIVE TELEMETRY:** the companion responds, but no live game data is available. Old readings are cleared.
- **COMPANION OFFLINE:** the companion is closed, the request failed, the reply is invalid, or it is stale. Old readings are cleared. Start the companion to reconnect automatically.

Unknown measurements stay unavailable. The overlay does not invent tyre targets, car setup values, or energy readings. It does not change game settings or control the car.

## Technical details

The app uses Assetto Corsa's `ac`/`acsys` modules, Python standard-library code and the bundled official CPython 3.3.5 `_socket.pyd` extension. AC omits optional interpreter extensions, so `stdlib/x64/` and `stdlib/x86/` contain the matching socket build for each architecture. The app selects its interpreter's architecture without launching another process. These files were extracted from official, signed Python Software Foundation MSI archives without installing them; `stdlib/PROVENANCE.json` records their URLs, hashes and signature checks, and `stdlib/PYTHON-LICENSE.txt` preserves their license/notices.

A daemon thread polls `http://127.0.0.1:8777/api/overlay` every 0.5 seconds with a 0.8-second request timeout. The HTTP connection uses the literal IPv4 loopback address directly; it does not need DNS/IDNA, `_ssl`, `select` or `ctypes`. Proxies are not used and redirects are refused. Network activity never runs in the game's update callback. UI calls run only in `acMain` and `acUpdate`.

No extra Python installation or package is needed inside the game. The desktop companion must be running on the same PC.

### Optional race timing bridge

When the game exposes the relevant APIs, the app also gathers public timing for up to 64 occupied car slots in small batches and publishes the latest available snapshot up to twice per second. Driver/car names, completed laps, current/last lap times, track progress, and speed are sent to the same local companion at `/api/bridge`. The payload includes the player's car, track and layout identifiers when available so the companion can restrict it to the current live session.

All game reads stay on the game thread. Only the latest sample is queued for the worker; samples older than three seconds are discarded. A failed or unsupported bridge does not interrupt the text overlay. Unavailable individual signals are omitted, and disconnected slots are skipped when connection information is available. Timing is limited to what the local game publishes and can be delayed or incomplete in multiplayer. Opponents' fuel, tyres, and setups are not collected or estimated.

## Verification

From the project folder, run:

```powershell
python -m unittest discover -s ac_app/tests -v
```

The tests provide a fake `ac` module; they do not launch or modify Assetto Corsa. Additional isolated CPython 3.3.5 x64 and x86 probes verified imports, `acMain`, and real local HTTP GET/POST with the bundled matching socket extension. The probes did not load `_ssl`, `select` or `ctypes`. A real in-game check is still required to confirm final font rendering and placement in the user's AC/CSP version.
