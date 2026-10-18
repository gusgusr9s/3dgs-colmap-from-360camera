# RTX 5070 performance review — 2026-10-18

Completed job: `cbf586354f`, model run `20261018_104718_93f494a2`.
Evidence: job `3d_status.json`, `3d.log`, model `process_summary.json`, Views status,
installed Brush/COLMAP CLI help, and the small CUDA experiments below.
The review and benchmarks leave the completed model and its input files intact.
No matched RX 6700 XT run exists in this workspace, so a cross-card speedup is not quantified here.

## Measured pipeline

Input: 123 seconds, 1920×960 panorama, 2 fps, 246 camera positions.
Views: 448×448, 16 directions per position, 3,936 images.
Model recipe: Ultra, 30,000 steps, 4,000,000 splat cap, growth stop 22,500,
SH degree 3 (installed Brush default), export every 5,000 steps.

| Stage | Seconds | Evidence / backend |
|---|---:|---|
| Generate Views | 9.016 | WGPU/Vulkan, RTX 5070, batch 16 |
| Preparation | 5.775 | CPU + disk |
| SIFT extraction | 9.126 | COLMAP CUDA; log confirms GPU extractor |
| Matching | 27.678 | COLMAP CUDA, sequential overlap 10 |
| Poses | 70.547 | Mapper, 20 CPU threads; `ba_use_gpu` left at default 0 |
| Dataset / review | 20.331 | Includes 13.5 s parallel JPEG preparation, 8 workers |
| Masks | 64.683 | CUDA, RTX 5070, no fallback, fixed batch 4 |
| Brush training phase | 1,183.125 | WGPU/Vulkan, RTX 5070 |
| Final evaluation / publish | 7.536 | Separate from evaluation/export inside Brush |

Model elapsed: **1,388.812 s = 23 min 8.8 s**; including Views: about 23 min 18 s.
Training accounts for **85.2%** of model time. All other model stages together take
205.7 s: even eliminating all of them would save only 14.8% of this run.

Pose quality: 246/246 panoramas registered, mean reprojection error 0.532 px,
66,265 sparse points. The 215 training positions provide 3,440 training views.
Held-out evaluation: 31 positions, 496 expected views, 494 valid and 2 fully masked.
Application masked PSNR: **22.164 dB**. Brush's raw PSNR is **18.114 dB** with
SSIM 0.7104; the raw and masked metrics must not be compared as the same score.

## Where more time can be saved

### 1. Training budget and splat growth — highest potential, quality tradeoff

The log grows from 66k seed points to about 2.03M splats at step 12,801,
then reaches 4M at step 20,601. Step 20,001 is logged at 11:00:26 and training
ends at 11:10:18 (Asia/Bangkok): almost ten minutes remain around the full cap.
Late intervals of 200 steps generally take 10–13 seconds.

Test in this order on separate runs with the same images, masks, poses and held-out split:

1. 30k steps / **3M cap**, retaining the baseline growth-stop schedule: isolates the cap.
2. 30k steps / **2M cap**: a more aggressive memory/work reduction.
3. **25k steps** at the chosen cap: evaluate before trying 20k.
4. Test SH degree **2** against the degree-3 baseline if view-dependent appearance allows it.

The retained 25k checkpoint precedes completion by about 4 min 49 s, but it has no
comparable saved evaluation renders, so its quality is not established.
Do not treat these intervals as guaranteed savings from a new recipe: changing total
steps also changes learning-rate schedules and the app currently sets growth stop to 75%.
Measure masked PSNR, p10 and visual defects at the same held-out positions.
Choose a quality tolerance before running a sweep; no tolerance is assumed here.

The existing High preset uses 20k / 2M, but changes multiple variables together.
Expose training steps and splat cap independently from the Views quality preset for
controlled experiments. Reuse immutable dataset snapshots rather than regenerating poses/masks.

### 2. Training backend — CUDA requires another training path

The installed Brush 0.3.0 binary runs through Burn/WGPU and Vulkan. Its CLI has
no CUDA backend switch. Installing another Torch/CUDA package does not accelerate
this Brush subprocess. PyTorch precision settings affect the person masks only.

An optional native CUDA trainer such as Nerfstudio/Splatfacto with gsplat is an
architecture experiment, not a configuration toggle or a proven speedup. It needs
verification of compiled CUDA extensions and SM 120 compatibility, plus parity of
camera transforms, masks, seeds, held-out split and export behavior. `import gsplat`
alone is insufficient. Keep this experiment isolated from the working environment.

### 3. Checkpoint I/O — smaller potential while retaining recovery

The app exports every 5,000 steps. A 4M degree-3 PLY is **944,001,552 bytes**.
Only the newest two checkpoints remain after pruning; pruning does not avoid earlier writes.
The final `splat.ply` is linked to the final checkpoint when hard links are available.
The log has longer intervals around exports, but no separate export timer: exact I/O
overhead cannot be isolated from this log. Add export/evaluation timing before changing
the interval. An optional 10k interval is a possible test with a longer recovery window.

### 4. CUDA masks — already fast; pipeline work matters more than precision

A 16-image sample from this completed job was measured with the existing LRASPP
model, 520×520 transformed inputs, three warm timed repetitions, and host/device copies.
These timings exclude JPEG decode, transforms, morphology, PNG writes and worker startup.

| Variant | Median for 16 images | IoU vs current CUDA batch 4 |
|---|---:|---:|
| FP32 NCHW batch 4, current path | 52.95 ms | 1.000000 |
| FP32 NCHW batch 8 | 47.36 ms | 1.000000 |
| FP32 NCHW batch 16 | 52.76 ms | 0.999838 |
| FP32 channels_last batch 4 | 71.10 ms | 0.999428 |
| FP32 channels_last batch 8 | 69.10 ms | 0.999417 |
| FP32 channels_last batch 16 | 64.62 ms | 0.999410 |
| FP16 autocast + channels_last batch 8 | 53.09 ms | 0.997741 |

Batch 8 reduced sampled inference time by about **10.6%**; batch 16, channels_last
and the tested FP16 combination did not improve on it. This is a small exploratory
sample, not a full-job speedup or full-dataset quality test. There is no evidence here
to enable mixed precision or channels_last as defaults. CPU transforms with 20 versus
4 threads took 47.2 versus 50.4 ms per 16 images, so fewer threads were not a clear win.

If optimizing masks, first split timings into decode/transform, inference, postprocess
and PNG writes; then test producer/consumer preprocessing and bounded write queues.
Keep CPU/DirectML recovery and atomic mask publishing. Even halving the complete 64.7 s
mask phase would save only about 32 s of the 23-minute model run.

### 5. Poses / Views — lower priority and build-dependent options

CUDA SIFT and matching are already enabled. Mapper BA defaults to CPU in the installed
CLI. Ceres GPU BA requires a build with suitable CUDA solver support; a CUDA SIFT
banner does not establish that support. Confirm actual solver use and benchmark a copy
before exposing `Mapper.ba_use_gpu=1`. Caspar currently supports PINHOLE/SIMPLE_RADIAL,
so it is unsuitable for this job's native EQUIRECTANGULAR observations.

`--max-resolution 2048` is a ceiling; the actual inputs are already 448×448.
Changing that ceiling to 1024 will not downscale these inputs or provide a speedup.
Reducing image count also does not automatically shorten fixed-step random-view training.

No historical training GPU utilization, power, clocks or VRAM timeline was captured.
The later `nvidia-smi` snapshot is not evidence of utilization during training. Add a
timestamped per-run GPU/CPU/disk trace to distinguish compute, CPU scheduling and I/O.

## Verification scripts reviewed and updated

- Dashboard `app/runtime/runtime_verification.py`: inventory only; correctly does not
  claim actual training. A CUDA Torch build plus NVIDIA driver is weaker evidence than inference.
- Installer `scripts/install/manage.py --cuda-smoke`: tiny allocation/kernel, not the
  segmentation graph or Brush. The full Windows installer adds this only for explicit
  `-Device cuda`; default `auto` currently does not request it.
- `scripts/check_runtime.py`: actual WGPU compute/readback and LRASPP inference when
  explicitly requested. Its module description now states that it is a compute smoke
  and may download weights, instead of calling it a read-only inventory. Running it
  exposed a Windows encoding failure: the parent Python used UTF-8 but the spawned
  `ns-process-data --help` emitted CP1252, causing a decoder thread failure and a false
  dependency failure. Fixed by propagating Python UTF-8 settings and decoding native
  subprocess output as UTF-8 with replacement for non-UTF-8 diagnostics.
- `scripts/check_gpu_pipeline.py`: previously forced DirectML. Added
  `--mask-device auto|cuda|directml`, actual backend reporting, rejection of CPU fallback
  or backend changes during timings, and timing-scope labels.

Actual small pipeline check on this RTX passed: WGPU/OpenCV pixel parity and CUDA/CPU
mask IoU **0.999609**. Warm mask inference measured 8.46 ms CUDA versus 181.74 ms CPU
per four-image batch. This confirms existing CUDA acceleration; it is not another 95%
speedup available to the already-CUDA production job.

The short 64-image Views benchmark took 0.567 s CPU versus 1.478 s WGPU, including
worker/device startup. It demonstrates a short-workload startup cost and does not
establish that CPU would beat the completed 3,936-image WGPU run.

Artifacts (local, ignored by Git): `tools/install-logs/rtx-cuda-pipeline.json`,
`tools/install-logs/rtx-cuda-pipeline-verified.json`, `tools/install-logs/rtx-mask-tuning.json`,
`tools/install-logs/rtx_mask_tuning_probe.py`.
After the encoding fix, `check_runtime.py --view-device wgpu --colmap-device cuda --mask-device cuda`
passed all checks. The updated pipeline benchmark also passed with an explicit CUDA request;
both commands exited 0 and the job page continued to return HTTP 200.

Primary upstream references:

- [Brush 0.3.0 backend dependencies](https://github.com/ArthurBrussee/brush/blob/v0.3.0/Cargo.toml)
- [COLMAP 4.2.1 BA guidance and Caspar camera restrictions](https://github.com/colmap/colmap/blob/4.2.1/doc/faq.rst)
- [Nerfstudio Splatfacto](https://docs.nerf.studio/nerfology/methods/splat.html)
