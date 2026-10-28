# Code and engine provenance

APEX starts with its own local telemetry, recording and live engineer. The release package also includes **AC Pro Engineer**, by Rgosh and contributors, as an optional native backend selected in Settings. Its unmodified Windows executable computes the upstream engineering advice and lap debriefs; when selected, APEX receives the existing loopback JSON streams and presents them alongside its own recordings and live calls.

Upstream: <https://github.com/Rgosh/ac-pro-engineer>

Pinned release: **v0.5.1**. Exact download URLs, published checksums, actual SHA-256 hashes and source revision are recorded in `third_party/pro_engineer/PROVENANCE.json`. The corresponding upstream source archive and original license/notices travel in the package.

The APEX adapter, native Windows interface, voice scheduling, recording, saved-session library and additional distance-based lap comparisons are local additions. Those additions require their own validation; upstream tests do not establish that the adapter or every AC mod works correctly.

This combined package is provided with source under **GNU AGPL version 3**. Upstream copyright notices and additional terms are preserved in `third_party/pro_engineer/NOTICE`. The APEX name and visuals are separate from upstream's name, logo and icons.

## CPython support for the game overlay

The AC Python app includes the official **CPython 3.3.5** `_socket.pyd` extension for x64 and x86 under `ac_app/RaceEngineer/stdlib/`. These two files supply socket support missing from AC's embedded interpreter; the bundle does not install a separate Python interpreter.

The files were administratively extracted from the Python Software Foundation's official, signed Windows MSI archives without installing them. Download URLs, archive/file SHA-256 hashes and signature results are preserved in `ac_app/RaceEngineer/stdlib/PROVENANCE.json`. CPython's license and historical notices are preserved separately in `ac_app/RaceEngineer/stdlib/PYTHON-LICENSE.txt`.

CPython source: <https://www.python.org/ftp/python/3.3.5/Python-3.3.5.tar.xz>.

Simulator Controller and Crew Chief were investigated as possible integrations but their engines are not included in this release. Track Titan is not integrated. No claim of endorsement by those projects, Kunos, Content Manager or Custom Shaders Patch is made.


## Data and teaching sources added in 0.4

Human original-AC telemetry: Adrian Remonda et al., AssettoCorsaGym, NeurIPS 2024, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), [dataset](https://huggingface.co/datasets/dasgringuen/assettoCorsaGym). The derived one-lap CSV and its transformations, pinned revision, attribution and checksums are documented in reference_data/README.md and its JSON sidecar.

Original short coaching paraphrases cite [AiM RaceStudio 3](https://www.aim-sportline.com/docs/racestudio3/manual/html/analysis.html) and [Scott Mansell / Driver61](https://driver61.com/uni/corner-phases/). Official [Formula 1 timing](https://www.formula1.com/en/racing/2026/austria) and [Red Bull Ring circuit information](https://www.redbullring.com/en/events-tickets/formula-1/formula-1-circuit/) are labelled context, with retrieval dates. No maps, full lessons or private telemetry were copied. This is not an endorsement or integration with these publishers.
