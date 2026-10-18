# Inspect camera poses before training

The Source creation form offers three processing targets plus a temporary map button:

- Full Model: Views → Camera Pose → Mask → Train → export.
- **Calculate test map (temporary)**: defaults to five seconds from the
  selected start; open a separate inspector without creating a persistent Job.
  Choose ten seconds when a wider frame interval would leave fewer than six
  positions in five seconds (e.g. every 30 frames at 30 FPS).
  Temporary crops are capped at 640px (preserving aspect ratio); native spherical
  COLMAP panoramas use the usual size. No Mask, Train or Model is created.
- Views and Camera Pose: process the selected range, then stop at the camera map.
- Views only: no COLMAP or training.

The temporary map button respects a shorter selected end or source duration.
It does not change the form's selected processing target or full clip range.
Both the button and legacy `pipeline_mode=preview5` requests now create a cache
entry under `web_data/cache/pose_previews`, not a Job. Existing old Preview Jobs
are retained and can still be filtered out of the library.
Concurrent matching requests share a cache entry keyed by source identity and
settings. The worker runs in a private workspace and acquires the **same process
queue lock** as normal Views/Train, including across server ports. It waits for
active training before loading any GPU work. Expiration is one hour; a daemon
cleanup timer and subsequent requests remove expired generated files, skip locked
previews and retain only small expired-status records. At most 16 recent unlocked
cache entries are retained on cleanup. Preview maps cannot submit Train directly;
create the real Job from the Source form after inspection.

The temporary request and its worker use the same validated clipping rule;
the Source form keeps its original selected range for the real Job.
It keeps the requested angles and frame interval. It is a
short clip test, not a reduced quality training run. A short or stationary
sequence can fail pose initialization; extend the range when that happens.
For a short spherical sequence (<60 panoramas), a mapper failure explicitly
reporting `No good initial image pair found` triggers one retry with
`Mapper.init_max_forward_motion=1`. This addresses rejection of forward-moving
360 captures by the default pinhole-oriented z-translation filter. It applies
only to fresh spherical mapping, keeps GPU settings and all inlier,
triangulation, point-filtering and final quality checks, and records the retry
in status/quality metadata. Other failures and resumed runs are not retried.

Evidence (2026-10-18, Job 128cd8fd31, 0–5s, 19 panoramas): default and a
triangulation-only change both failed; the forward-motion-only retry registered
19/19 images, produced 9,254 points with mean reprojection 0.51885px, and passed
the existing dataset quality checks. Against the matching cameras in the full
reconstruction, aligned center errors were median 0.0544%, max 0.1805% of the
clip's path span; rotation error median 0.0728°, max 0.1651°. This validates this
clip, not all short sequences. Artifacts: `tools/install-logs/short-clip-init-20261018_172958/`.
The rejection condition is in the pinned
[COLMAP 4.2.1 implementation](https://github.com/colmap/colmap/blob/4.2.1/src/colmap/sfm/incremental_mapper_impl.cc#L635).

Source files and existing Jobs remain available. Starting the full source later
creates its own Job and does not extrapolate poses beyond the tested clip.

The map shows consecutive **camera position** distances: mean, minimum, median and
maximum in COLMAP's relative units and as a percentage of the diagonal of the
camera path bounding box. Perspective crops at a shared panorama center do not
count as separate camera positions.

When reference Job `cbf586354f` is available, comparison uses baseline divided by
the average of the two cameras' median observed 3D point distances. Depth uses up
to 256 deterministic samples per native panorama. This removes arbitrary model
scale without normalizing a five-second clip by its own path length. Cross-scene
comparison remains a heuristic; different scene structure/visibility can change it.
Raw distances from different reconstructions must not be compared as meters.
Geometry is recorded in new dataset quality metadata; historical models are read
once per model file stamp and cached in memory. No remapping is needed.

Editable lower/upper thresholds default to 1 and 2 times the reference mean.
Camera markers and lines are red below half the lower bound, yellow below the
lower bound, green within the bounds, orange above the upper bound. Selected
cameras remain blue; excluded training cameras remain gray. Pose jumps have
purple lines. Without native depth or reference data, the inspector explicitly
falls back to path-diagonal percentages (0.15–1%) and withholds recommendations.
Colors describe sampling density, not reconstruction quality or proven ghosting.

Recommendations target 1.25 times the reference mean. Increasing the source frame
interval is capped at 4x per experiment and requires no missing cameras or pose
jumps, plus shared native 3D tracks: 10th-percentile common count >=100 and shared
fraction >=0.15 for the tested stride. These are conservative inspection heuristics,
not universal overlap guarantees. Curves, visibility changes, and pose quality must
still be checked in a new preview. The Source form shows the completed test's mean
and recommendation and offers an explicit Apply button; stale recommendations
cannot overwrite settings changed since the test.

Sampling defaults to the existing 0.3-second automatic gap limit. The new
`sampling_guard=manual` option uses the actual requested `sample_every`, consistently
in previews, CPU workers, GPU workers and metadata. Apply recommendation selects
manual explicitly. Thus every 30 source frames at 30 FPS means one position per
second, not 30 FPS, and is no longer silently capped back to every nine frames.

After inspection, the map's Train action reuses the verified pose/dataset for
that exact Views version. Timing includes the pose preview and Views when
training is started later, excluding paused time.
