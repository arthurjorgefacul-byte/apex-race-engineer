# Building APEX

The release ZIP is the simplest way to playtest. This source tree contains the corresponding APEX sources, assets, tests and the pinned optional AC Pro Engineer source archive.

## Local engineer

Install Python 3.10+ and the .NET 10 SDK on Windows x64. Runtime-only packages are sufficient for the published binaries; compiling requires the SDK.

From the source root:

```powershell
dotnet build desktop/ApexDesktop.csproj -c Release
Copy-Item desktop/bin/Release/net10.0-windows/Apex.* .
.\Apex.exe
```

Keep the four native output files alongside `app.py` and the supporting folders. A fresh configuration starts with the local engineer. User data is written to `data/` and is ignored by Git.

The companion can also run separately:

```powershell
python app.py --no-browser --engine local
```

The in-game overlay/bridge instructions are in `playtest/START-HERE.md`. The full game, CM and CSP are separate installations.

## Checks

```powershell
python -m unittest discover -s tests -q
python -m unittest discover -s ac_app/tests -q
dotnet run --project tests/native_input/InputProbe.csproj -c Release
dotnet run --project tests/native_shutdown/ShutdownProbe.csproj -c Release
```

The automated checks do not replace physical wheel, microphone, game/mod and online-grid acceptance testing.

## Optional upstream engine

Published binary packages include the unmodified optional AC Pro Engineer executable. The corresponding-source archive omits executable build outputs. Its pinned original source archive, licence, notices, download URLs and checksums are in `third_party/pro_engineer/`. Build the upstream project according to its own instructions, or obtain its pinned release and verify it against `PROVENANCE.json`, to use that backend from a source checkout. See `CREDITS.md` for attribution.
