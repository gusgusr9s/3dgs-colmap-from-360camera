# Adaptive panorama keyframes (experimental)

This feature is separate from panorama cache reuse. Cache reuse avoids decoding
again; adaptive selection optionally reduces the images entering COLMAP.
Default: `COLMAP_KEYFRAMES=off`.

Enable for a **new reconstruction**, from the PowerShell terminal launching the app:

```powershell
$env:COLMAP_KEYFRAMES='adaptive'
.\app_2_run.ps1
```

An already running app needs restarting to read this environment variable.
Resume preserves the previous database selection even if this variable changes.
Set it to `off` for a new full-image reference run.

Preview without changing the Job or running COLMAP:

```powershell
.\.venv-win\Scripts\python.exe -m app.pipeline.adaptive_keyframes web_data/jobs/6916823944/spherical_colmap/panoramas --output tools/install-logs/keyframe-preview.json
```

Selection compares 512-wide grayscale thumbnails, excludes the polar/rig area,
and requires both low per-sector appearance differences and low optical flow.
Forward/backward tracking and spatial coverage checks retain uncertain frames.
Endpoints and the frame before a detected change are retained; at most two input
samples are skipped between retained frames. Short sequences (<12) stay intact.
This bounds sample gaps, not seconds or physical distance, and cannot guarantee
geometric overlap or preservation of every turn.

Only selected images enter feature extraction (native `--image_list_path`), so
matching and mapping operate on that same subset. Source panoramas and Views
remain intact. Brush uses only crops with reconstructed poses; skipped frames
are not interpolated. Training/evaluation image sets can therefore change and
quality scores must not be compared without a common evaluation set.

The database's `keyframe_selection.json` records original and selected names.
Recovery validates this manifest against Views and the actual database, copies
it with the database, and never recomputes selection. Existing quality gates
remain; registration coverage uses the selected count. Result metadata reports
both original and selected counts. Failed reconstruction is surfaced normally;
there is no automatic full-image rerun.

Measured selection preview on Job 6916823944 (2026-10-18): 1,230 → 1,230
panoramas, 14.047 seconds. This conservative configuration does **not** speed up
that clip. End-to-end pose quality on a reduced real sequence remains unverified;
do not treat the selection smoke tests as a reconstruction quality benchmark.
