# Handoff: GPU optimization ของขั้น Pose / Masks / Verify (2026-10-18)

เอกสารนี้สรุปงานที่ทำ หลักฐานที่วัดได้ และจุดที่ยังไม่ได้พิสูจน์ สำหรับผู้ตรวจรอบถัดไป
ยังไม่มีการ commit; working tree มีงานค้างจาก session ก่อน (Codex) ปนอยู่ด้วย ดูรายการไฟล์ในหัวข้อ 4

เครื่องที่ทดสอบ: Windows 11, RTX 5070 12 GB (driver 617.42), 20 threads, 32 GB RAM
Job อ้างอิง: `web_data/jobs/6916823944` (วิดีโอ 360 8K HEVC 7680×3840, 1,230 panoramas, 19,680 views 1536²)

## 1. ปัญหาที่พบ (พร้อมหลักฐาน)

| # | ปัญหา | หลักฐาน |
|---|---|---|
| P1 | Global BA ใน `mapper` ใช้ GPU น้อยมาก: CPU 1 core, GPU 5–10%, ~40 W จาก 250 W | วัดด้วย nvidia-smi + per-thread CPU ระหว่าง global BA ของ run `20261018_134912` |
| P2 | `mapper` เลือก solver ตายตัว (CUDA >200 ภาพ = sparse cuDSS; CPU 50–1000 ภาพ = CHOLMOD) และไม่มี CLI option เปลี่ยน threshold (`mapper -h` ไม่มี `BundleAdjustmentCeres.*`) | ทดสอบ `bundle_adjuster` บน snapshot จริง 935 ภาพ (6.88M residuals) ทุก solver ได้ final cost 0.477172 px เท่ากัน: CUDA dense 39.7 s, CPU iterative 46.8 s, CUDA sparse 79.1 s, CHOLMOD ~94 s (5 iter) |
| P3 | Global BA กินเวลา ~21 จาก 30.6 นาทีของขั้น Pose (785→1230) เพราะ refinement loop สูงสุด 5 รอบ × solver ช้า | timeline จาก `3d.log` |
| P4 | Caspar backend ไม่รองรับ EQUIRECTANGULAR: skip ทุกภาพ "No residuals to optimize" แต่ **exit 0** | `bundle_adjuster --BundleAdjustment.backend CASPAR` |
| P5 | Bundle CUDA (lyehe/build_gpu_colmap 4.2.1) build arch `75;80;86;89;90` ไม่มี sm_120 (RTX 5070) ใช้ PTX JIT | `tools/colmap-cuda-ba/build_info.json`; dense solve ขึ้นถึง 99% GPU จึงทำงานได้ |
| P6 | cuDSS threading layer (`CUDSS_THREADING_LIB`) ไม่มีผล — Ceres ไม่เรียก `cudssSetThreadingLayer` | 76.3 s vs 72.5 s |
| P7 | Masking ติด CPU: GPU ~10%, ~23 ภาพ/วินาที (loop Python เธรดเดียว: decode, resize, morphology 21×21, PNG) | วัดระหว่าง job |
| P8 | Verify execution checks ของ Codex: `--SiftExtraction.max_image_size` ไม่มีใน COLMAP 4.2.1 → features CPU/GPU fail ทั้งคู่ | `colmap feature_extractor -h` มีแค่ `--FeatureExtraction.max_image_size` |
| P9 | stdout ของ COLMAP ถูก buffer เป็นก้อน → log/UI ดูค้างระหว่าง global BA 5–10 นาที | log ขาดกลางบรรทัด |
| P10 | Video decode ด้วย GPU ไม่คุ้มสำหรับ 8K HEVC: CPU 34.2 fps, NVDEC 37.2 fps; FFmpeg D3D12VA "not supported"; **OpenCV `CAP_PROP_HW_ACCELERATION` (D3D11) ล้มทุกเฟรมแต่ไม่ error — ได้ 0 เฟรม** | ffmpeg 9.0.1 + cv2 4.10 กับ `uploads/1017.mp4` |
| P11 | ขั้นเตรียมข้อมูล COLMAP decode วิดีโอทั้งไฟล์ซ้ำกับขั้น Views (186 s); เตรียม dataset decode+encode JPEG ใหม่ทุกภาพ (174 s) | `extract_spherical_keyframes`, `dataset_preparation.py` |
| P12 | การลงทะเบียนภาพ incremental ~1.1 s/ภาพ เป็น CPU ทีละภาพ (ข้อจำกัดของ incremental SfM ไม่ใช่ของ GPU) | ส่วนที่เหลือมากที่สุดของขั้น Pose |

## 2. สิ่งที่แก้แล้วและผลที่วัดได้

### 2.1 Staged mapping + external global BA (แก้ P1–P3)
- `app/pipeline/staged_mapping.py` (ใหม่): mapper ทีละช่วง +20% (`--Mapper.image_list_path`), ปิด intermediate global BA
  (`ba_global_*_ratio/freq` สูงมาก), final refinement 1 step (`ba_global_max_refinements 1`,
  `ba_global_max_num_iterations 1`), แล้ว `bundle_adjuster` แบบลู่เข้า (50 iter, ftol 1e-6 สำหรับ balanced)
  ด้วย solver ที่เร็วที่สุด; ปิดท้าย `point_filtering` (4 px, 1.5°) + BA อีกรอบ; 200 ภาพแรกใช้ mapper ปกติ
- `app/runtime/ba_solvers.py` (ใหม่): catalog solver + flags, `select()` (env `COLMAP_BA_SOLVER`, ranking จาก
  benchmark ที่ผูก SHA-256 ของ executable, ตัด `cuda_dense` เมื่อ >3000 ภาพหรือ VRAM ว่างไม่พอ
  ประมาณ `(6N+16)²×16 B + 768 MiB`), fixture 360 สังเคราะห์ + `benchmark()`
- `app/web/server.py` `run_spherical_colmap`: `map_poses()` แทน `run(mapper_command)` ทั้ง 3 จุด (รวม retry
  exhaustive); เปิดเมื่อ `COLMAP_STAGED_MAPPING=auto` + profile balanced + >240 panoramas
- `app/jobs/colmap_progress.py`: นับ progress จาก `pose_segment_registered_positions` และแสดง global BA ระหว่าง
  `bundle_adjuster`/`point_filtering`
- ผลบนสำเนา job (เริ่มจาก model 785 ภาพเดียวกัน, ระหว่าง Brush train ใช้ GPU อยู่):

| | mapper เดิม | staged |
|---|---|---|
| เวลา 785→1230 | 30.6 นาที | 19.0 นาที |
| ภาพ / จุด | 1230 / 459,024 | 1230 / 459,284 |
| mean reprojection | 0.5583 px | 0.5586 px |
| track length | 10.205 | 10.203 |
| camera centres หลัง Sim3 align | — | median 0.043%, max ~0.08% ของรัศมีฉาก |

### 2.2 Benchmark ตอนติดตั้ง / Verify
- `app/runtime/execution_verification.py`: เพิ่ม `ba_benchmark_checks()` (จับเวลาทุก solver บน fixture 360 ภาพ /
  1.3M observations ~45 s, บันทึก `tools/install-logs/ba-benchmark.json`), แก้ P8
- `verify_cli --execute`, `verification_service.start(execute=)` (timeout 900 s), `POST /api/runtime-verification?execute=1`,
  ปุ่ม "ทดสอบรันจริง CPU/GPU" ใน `assets/app/runtime_verify.mjs` + markup ใน server.py
- `scripts/install/manage.py verify --execute`; `windows.ps1` และ `ubuntu.sh` ขั้น [6/7] ส่ง `--execute`
- `runtime_verification.py`: แถว COLMAP แสดง solver ที่ staged mapping จะใช้
- ผล fixture บน RTX 5070 (ระหว่าง Brush train): CUDA dense 6.2 s < CPU iterative 7.0 s < CUDA sparse 9.4 s < CHOLMOD 10.5 s
  ทุกตัว cost 1.1207 → 0.3453 px; ลำดับตรงกับข้อมูลจริง

### 2.3 Masking (แก้ P7 บางส่วน)
- `create_dynamic_person_masks`: prefetch decode ชุดถัดไประหว่าง inference, `write_mask` ใน ThreadPoolExecutor,
  batch 8 บน GPU / 4 บน CPU
- ภาพจริง 240 ภาพ: ต่างจากเดิม 0 pixel; CUDA 22.7 → 48.8 ภาพ/วินาที, CPU 12.6 → 19.5 (วัดระหว่างมีงานอื่น)

### 2.4 Tests
- ใหม่ `tests/test_ba_solvers.py` (11 tests: flags, CPU ไม่เลือก GPU solver, ranking ต้องตรง executable, VRAM/frame
  limit, report parsing, plan_chunk, staged run ด้วย fake COLMAP, progress) — ผ่าน
- แก้ `tests/test_runtime_verification.py` ให้รองรับ `execute=` — ผ่าน
- รันทุกไฟล์ test แยกไฟล์: ผ่านทั้งหมด ยกเว้น `test_task_control` (2 failures + 2 errors: timeout รอ worker,
  WinError 32) — ไฟล์นี้และ `app/jobs/task_control.py` ไม่ถูกแก้ แต่ยังไม่ได้รันซ้ำตอนเครื่องว่าง

## 3. จุดเสี่ยง / ยังไม่ได้พิสูจน์ (ขอให้ตรวจ)

1. **Staged mapping จาก 0 ภาพยังไม่เคยรันกับข้อมูลจริง** วัดแค่ resume จาก 785; bootstrap path (200 ภาพแรก,
   `multiple_models` default, กรณี init ไม่สำเร็จแล้ว fallback ไปทุกภาพ) มีแค่ unit test กับ fake COLMAP
2. คุณภาพเทียบได้ใกล้เดิมบน **job เดียว**; mapper ท้ายทุก segment ทำ retriangulation + 1-step BA สองครั้ง (~40–50 s)
   ซึ่งต่างจาก refinement loop ของ COLMAP (สูงสุด 5 รอบ)
3. `point_filtering` ใช้ 4 px / 1.5° แบบ hardcode (ค่า default ของ mapper) ไม่ sync กับ `Mapper.filter_*` ถ้ามีการตั้งค่าอื่น
4. `bundle_adjuster` ไม่มี option จำนวน thread → ไม่เคารพ `CPU_MODE=balanced` ระหว่าง external BA
5. สูตร VRAM ของ dense และ `DENSE_MAX_FRAMES=3000` เป็น heuristic ยังไม่ได้ทดสอบที่ N ใหญ่ (>1,230)
6. `ba-benchmark.json` บนเครื่องนี้บันทึกตอน GPU ไม่ว่าง; Verify (execute) **เขียนไฟล์นี้ทับ** เป็น side effect
7. Benchmark ใช้ fixture สังเคราะห์; ยืนยันลำดับกับข้อมูลจริงบน RTX 5070 เท่านั้น — **ยังไม่ได้ทดสอบบน RX 6700 XT จริง**
   (คาดว่าได้ `cpu_iterative`)
8. Masks path DirectML (AMD) ไม่ได้ทดสอบหลังเพิ่ม thread pool (เครื่องนี้ไม่มี AMD); `predictor.probabilities`
   ถูกเรียกจาก main thread เท่านั้น
9. UI ปุ่มใหม่ตรวจแค่ `node --check` ยังไม่ได้เปิดใน browser; server port 8000 ยังรันโค้ดเก่า (ต้อง restart)
10. Verify execution ยังไม่ทดสอบ Brush (`not_tested`) และถูก block เมื่อมี Job รัน (ตั้งใจ)
11. `staged_mapping.run` ลบ `staged-*` เก่าใน checkpoints เมื่อเริ่ม (guard ด้วย prefix + parent) — ตรวจว่าไม่ชน recovery
12. P9 (log buffering) ยังไม่ได้แก้ — staged mapping แค่เพิ่ม status update ระหว่าง segment
13. P5 (sm_120) แก้ไม่ได้ในเครื่องนี้ (ไม่มี MSVC / CUDA Toolkit)

## 4. ไฟล์ที่แก้ใน session นี้

ใหม่: `app/pipeline/staged_mapping.py`, `app/runtime/ba_solvers.py`, `tests/test_ba_solvers.py`, เอกสารนี้
แก้: `app/web/server.py` (imports, `run_spherical_colmap`, POST verify, markup ปุ่ม), `app/jobs/colmap_progress.py`,
`app/pipeline/spherical_to_brush.py` (`create_dynamic_person_masks`), `app/runtime/execution_verification.py`,
`app/runtime/verify_cli.py`, `app/runtime/verification_service.py`, `app/runtime/runtime_verification.py`,
`assets/app/runtime_verify.mjs`, `scripts/install/manage.py`, `scripts/install/windows.ps1` (2 บรรทัด),
`scripts/install/ubuntu.sh` (1 บรรทัด), `tests/test_runtime_verification.py`, `docs/guides/GPU_BA_WINDOWS.md`, `docs/README.md`

ไฟล์ที่ Codex สร้าง/แก้ไว้ก่อน session นี้ (ยังไม่ได้ review ทั้งหมด): `app/runtime/colmap_ba.py`,
`app/runtime/execution_verification.py` (ส่วนเดิม), `app/jobs/colmap_progress.py` (ส่วนเดิม), `scripts/check_colmap_ba.py`,
`scripts/install/colmap_gpu_ba.py`, `docs/guides/GPU_BA_WINDOWS.md` (ส่วนเดิม) และไฟล์ `M` อื่นใน `git status`

## 5. งานที่ยังรันอยู่ (multi-agent workflow `wf_0a2df603-a81`) — ยังไม่มีผล

ขณะเขียนเอกสารนี้ทั้ง 3 agent ยังทำงาน; **ไฟล์ด้านล่างอาจยังเปลี่ยนอยู่ ให้ตรวจหลัง workflow จบ**

| สาย | เป้าหมาย | ไฟล์ที่ agent ถือครอง |
|---|---|---|
| ทดลอง mapper | `global_mapper` (GPU / CPU), refinement chain, `hierarchical_mapper` บนสำเนา job; เกณฑ์ ≥1218/1230 ภาพ, reproj ≤0.62 px, centres median ≤0.5% / p95 ≤2% / max ≤5%, rotation median ≤0.5° | ไม่แก้ repo; ผลอยู่ที่ scratchpad `mapper_alternatives/` |
| Panorama reuse + dataset | Views เก็บ panorama 1920 px (INTER_AREA, q94) + manifest ให้ COLMAP ใช้ซ้ำ (fallback decode เดิม); dataset prep ข้าม re-encode เมื่อพิสูจน์ได้ว่าปลอดภัยกับ Brush (fallback re-encode q95) | `extract_spherical_keyframes` + Views writer ใน server.py, `cpu_views.py`, `view_worker.py`, `view_compute.py`, `pano_to_views.py`, `dataset_preparation.py`, `build_brush_dataset`, tests ใหม่ |
| Mask GPU post-processing | resize/threshold/morphology บน CUDA (torch), CPU OpenCV เป็น fallback, ต้องต่าง 0 pixel บน 240 ภาพจริง | `create_dynamic_person_masks`, `mask_compute.py`, `app/runtime/mask_postprocess.py` (ใหม่), `tests/test_mask_postprocess.py` |

แต่ละสายพัฒนามี reviewer อิสระ → แก้ → re-review อัตโนมัติ ผลการทดลอง mapper ยังไม่ถูกนำไปใช้ในโค้ด
(ต้องตัดสินเรื่องคุณภาพก่อน)

## 5.1 สถานะล่าสุด (17:00)

**Mask GPU post-processing — dev เสร็จ, review ผ่าน (มี minor 1 ข้อใน execution_verification: ยังไม่ทดสอบ CUDA post-process path ใน Verify)**
- ใหม่ `app/runtime/mask_postprocess.py`, `tests/test_mask_postprocess.py` (9 tests OK); แก้ `create_dynamic_person_masks`, `mask_compute.py`
- ใช้เฉพาะ `predictor.backend == 'CUDA'`; DirectML/CPU ใช้ OpenCV เดิม; error หรือ batch แรกไม่ตรงกับ OpenCV → fallback CPU + บันทึก `fallback_reasons`
- ต่างจากเดิม 0 pixel บน 1,440 ภาพจริง (รวม CPU-only และกรณีจำลอง CUDA error)
- CUDA 97–104 → 144–148 ภาพ/วินาที (เครื่องว่าง), CPU time/ภาพ −40%; CPU-only ความเร็วเท่าเดิม
- ความเสี่ยง: resize ทำให้ตรงกับ IPP ของ OpenCV ซึ่งขึ้นกับชุดคำสั่ง CPU (AVX2/AVX-512) — เครื่องอื่นอาจต่างได้ (มี check batch แรกกัน); ใช้ VRAM เพิ่ม ~312 MB/batch

**Panorama reuse + dataset copy — dev เสร็จ, review กำลังรัน**
- ใหม่ `app/pipeline/view_outputs.py` (`ViewOutputs` ใช้ทุก Views backend), `tests/test_panorama_cache.py` (6 OK), `tests/test_dataset_copy.py` (6 OK); แก้ server.py (`process_file`, `extract_spherical_keyframes`, restore ใน `run_spherical_colmap`, cleanup `.view_outputs`), `cpu_views.py`, `dataset_preparation.py`, `_build_brush_dataset`
- Views เขียน panorama cache `<job>/.view_outputs/panoramas/` + `panoramas.json`; COLMAP restore ด้วย sha256 + hardlink: 198.1 s → 0.5 s; byte-identical กับ decode path (75/75 ทั้ง WGPU และ CPU worker); Views CPU +3.4%
- เตรียม dataset: hardlink views แทน re-encode เมื่อมี manifest: 140 s → 20 s (−85%); job เก่าไม่มี manifest: decode-verify 61–69 s; fallback re-encode เท่าเดิม; ประหยัดดิสก์ 10.27 GB/job; PSNR ภาพเทรนดีขึ้น 44.06 → 44.35 dB
- **สาเหตุ "early eof" ของ Brush 0.3 ที่พบ: ไฟล์ภาพ ≤16,386 bytes โหลดไม่ได้** (bisection) → ไฟล์เล็กถูก pad หลัง EOI ให้ถึง 32 KiB; Brush train 50 steps ผ่านกับ dataset ใหม่
- ความเสี่ยง: ไฟล์ dataset เป็น hardlink ของ Views จึงห้ามเขียนทับแบบ in-place; legacy decode-verify ไม่จับ JPEG ที่ scan ขาดแต่มี EOI; `prepare_brush_dataset` (perspective path) ยัง re-encode q95 ไม่ pad

**ทดลอง mapper — ยังรัน**
- `global_mapper` GPU รอบเต็ม 1230 ภาพครั้งแรกถูก agent kill (`r0_global_gpu_stdhash_killed`); ทดลองย่อย 150 ภาพ: ช้ากว่า incremental (285 s) และ centres median 0.71% (เกินเกณฑ์ 0.5%), rotation 0.35°; COLMAP เตือนว่าไม่มี focal prior
- ตอนนี้กำลังรัน `global_mapper` CPU (`r2_global_cpu`); ยังไม่ได้รัน hierarchical_mapper
- ผลทั้งหมดอยู่ที่ scratchpad `mapper_alternatives/` (metrics.json ต่อ run)

**⚠️ มีเครื่องมืออื่นแก้ repo พร้อมกัน (16:19):** `app/pipeline/adaptive_keyframes.py`, `app/pipeline/camera_spacing.py`,
`app/jobs/cumulative_timing.py`, `docs/guides/ADAPTIVE_KEYFRAMES.md`, pose viewer, และเพิ่ม `adaptive_keyframes.prepare(...)`
ใน `run_spherical_colmap` (server.py ~1640) — **ไม่ใช่งานของ Claude/workflow นี้** ทำให้ `test_cpu_regression` (2 errors) และ
`test_compute_runtime` (1 error) fail ด้วย `KeyError 'frames'` ที่ `adaptive_keyframes.py:79` (test ส่ง metadata `{}`) —
ต้องให้เจ้าของงานนั้นแก้ และตรวจว่าเข้ากับ staged mapping / panorama cache

## 5.2 Codex รับช่วงต่อหลัง Claude session limit (2026-10-18)

- แก้ regression ที่เกิดจาก Codex: `adaptive_keyframes.prepare` ต้อง return ทันทีเมื่อโหมด `off`
  ก่อนอ่าน `metadata.frames`; โหมด `adaptive` ตรวจ metadata และรายงาน ValueError ที่เข้าใจได้
  ไม่ใช่ KeyError งานปกติและ fixture ที่ไม่ได้ใช้ adaptive จึงไม่ต้องส่ง frames
- ตรวจรวม CPU regression / compute runtime / adaptive / staged solver / panorama cache / dataset copy /
  mask post-processing: 54 tests ผ่าน (`tools/install-logs/handoff-integration-tests.log`)
- `test_task_control` รันแยกขณะเครื่องว่าง: 6 tests ผ่าน (`tools/install-logs/handoff-task-control.log`)
- เพิ่ม integration test ว่า adaptive selection ไม่แก้ bytes ของ panorama cache และรายชื่อ selected ตรงกัน
  ระหว่าง image_list, recovery manifest, database และ staged chunk planner
- ตรวจ artifacts ของ Claude เพิ่ม: `r2_global_cpu` และ `r3a_global_cpu_tri_ba_gpu` ได้ 1,230 ภาพ แต่
  `passes_quality_bar=false` ทั้งคู่ (p95 centre error ~2.74% >2%, max ~5.53% >5%);
  ยังไม่นำ mapper ทางเลือกมาแทน pipeline เดิม และยังไม่พบผล hierarchical_mapper
- ยังต้องตามต่อ: review panorama/dataset ให้ครบ, Verify CUDA post-process smoke, benchmark ตั้งแต่ 0 ภาพ
  และบนเครื่องว่าง; ตัวเลขเร่งงานทั้ง pipeline ยังไม่ได้พิสูจน์จากการรันครั้งเดียว
- งาน Codex ที่เพิ่มตามคำขอผู้ใช้: Preview 5 วินาที/หยุดที่ Camera Pose, relative camera-spacing thresholds,
  cumulative timing รวม Views/resume, Windows registered PATH recovery และ bounded short-spherical
  initialization retry (รายละเอียด `docs/guides/POSE_PREVIEW_WORKFLOW.md`)
- เพิ่มตัวกรองหน้า Jobs `kind=all|work|preview5` ใช้ `preview_clip_seconds=5` จาก manifest
  เพื่อซ่อน/แสดงเฉพาะ Jobs ที่สร้างด้วยโหมดทดสอบ 5 วินาที; ไม่เดาจากชื่อหรือความยาวคลิป
- เปลี่ยน Preview 5 วินาทีรุ่นใหม่เป็น temporary process: ปุ่มในฟอร์ม Source,
  cache ที่ `web_data/cache/pose_previews`, private worker workspace, ไม่เพิ่ม Job/Model,
  ไม่ Mask/Train, ล้างไฟล์หมดอายุหลังประมาณ 1 ชั่วโมง และ deduplicate ตาม Source/settings
- Temporary preview ใช้ `processing_queue` เดียวกับ Train ข้ามพอร์ต; ทดสอบ OS lock จริงว่า
  worker ไม่เริ่มระหว่าง Train ถือคิวอยู่ พร้อม tests ว่าไม่เรียก Mask/Train และไม่เขียน Jobs
- ผู้ใช้ระบุว่า 8001 กำลัง Train จึงเปิดโค้ดรุ่นใหม่ที่ **8002** โดยไม่ restart 8001
  request ทดลอง `ff17788b1b443f4cbd6eebd6b7903e55` เข้า queued รอ Train ของ Job `2884a89ca3`
  จริง ไม่พบ COLMAP process เพิ่มระหว่างนั้น; หลัง Train จบ Preview รันสำเร็จใน **24.438 s**:
  19/19 ตำแหน่ง, 304 views ชั่วคราว, 9,215 sparse points, 0 suspicious segments,
  รูปผ่าน endpoint ชั่วคราว HTTP 200 และจำนวน Jobs คงเดิม (6)
- Temporary preview tests: 11 Python tests (service/queue/worker/HTTP/desktop), 5 collection tests,
  และ UI scripts temporary/pose/capture ผ่าน; ยังไม่ได้ restart 8001 ตามคำขอผู้ใช้

## 5.3 ระยะกล้องเทียบงานอ้างอิงและการเว้นเฟรมจริง (Codex, 18:35)

- ตามคำขอผู้ใช้ เพิ่มค่าเฉลี่ย/ต่ำสุด/มัธยฐาน/สูงสุด ระยะกล้อง และเทียบ `cbf586354f`
  โดยใช้ baseline / median observed 3D depth ของกล้องสองตัว ไม่เทียบหน่วย COLMAP ดิบข้ามงาน
  หรือเอาเส้นทแยงเส้นทาง 5 วินาทีไปเทียบเต็มคลิป; ความต่างของฉากยังมีผล จึงเป็น heuristic
- `spacing_geometry.measure` อ่าน native tracks/ความลึก (sample สูงสุด 256 จุด/กล้อง), เก็บใน quality
  ของ dataset ใหม่ ส่วนงานเก่าอ่าน model ที่ระบุใน brush_data/transforms.json และ cache ตาม file stamp
- จุด/เส้นแดงเมื่อ <0.5 เท่าอ้างอิง, เหลือง <1, เขียว 1–2, ส้ม >2; จุดเลือกฟ้า,
  ตัด Train สีเทา, pose กระโดดเส้นม่วง; ปรับ threshold ได้ กรณีไม่มี native/reference มีข้อความ fallback
- คำแนะนำเพิ่ม sample_every จำกัดไม่เกิน 4 เท่าต่อรอบ, ต้องไม่มี missing/jump และ shared tracks
  p10 จำนวน >=100, สัดส่วน >=0.15; ฟอร์ม Source poll ผลทดสอบและมีปุ่มใช้ค่า โดยกันผลเก่าทับค่าที่แก้แล้ว
- เพิ่ม `sampling_guard=manual` ให้เก็บตามเลขที่กรอกจริง (เดิม 30 FPS ขอทุก 30 ถูก cap เหลือทุก 9)
  ผ่าน preview/CPU/GPU/metadata/ประมาณจำนวนภาพ/Template; default auto คง guard 0.3 วินาที
- Preview เริ่มต้น 5 วินาที และเลือก 10 วินาทีได้ เมื่อเว้นเฟรมมากจน 5 วินาทีไม่ครบขั้นต่ำ 6 ตำแหน่ง
  ยังเป็น cache ชั่วคราว ใช้คิวเดียวกับ Train ไม่เพิ่ม Jobs และไม่ลด quality gate
- งานล่าสุด `2884a89ca3`: mean ดิบ 0.0378919 เทียบอ้างอิง 0.1121620 แต่หลัง normalize depth
  mean เป็น 31.00% ของอ้างอิง; เสนอทดลองทุก 30 เฟรมจากเดิม 8 (จำนวนตำแหน่ง ~26.7% ไม่ใช่เวลา ~26.7%)
- ทดสอบจริง source 1017.mp4, ทุก 30 เฟรม, 0–10s: **13.266s**, 10/10 กล้อง, 160 views,
  2,186 sparse points, reprojection 0.43677px, 0 suspicious, image endpoint HTTP 200
  cache `b53a22b5bc221beccfe5770cd6402678`; ratio 96.97% อ้างอิง เป็นผลเฉพาะช่วงนี้
- ทดสอบจริง 0–5s ทุก 8 เฟรม: **22.641s**, 19/19 กล้อง, 304 views, 9,246 points,
  0 suspicious; ratio 39.73% อ้างอิง เสนอทุก 25 เฟรม แสดงว่าคำแนะนำเปลี่ยนได้ตามช่วงที่ทดสอบ
  cache `f476059d84327ef65ec371e400706fac`; จำนวน Jobs คง 6 ทั้งก่อน/หลัง
- Python integration 84 tests ผ่าน + UI scripts pose/temporary/capture/view-settings ผ่าน
- เว็บรุ่นใหม่ **8002**; **8001 PID 23940 ไม่ได้ restart** ตามข้อกำชับผู้ใช้

## 6. คำขอให้ผู้ตรวจรอบถัดไป

1. ตรวจ `staged_mapping.run` เรื่อง loop termination, การลบ checkpoint, resume หลังหยุดกลางทาง, retry exhaustive
2. ตรวจว่า CPU fallback ครบทุกทาง (`COLMAP_DEVICE=cpu`, `COLMAP_BA_DEVICE=cpu`, `MASK_DEVICE=cpu`, ไม่มี receipt)
3. รัน job ใหม่ตั้งแต่ 0 ภาพหนึ่งรอบหลัง restart แอป เพื่อยืนยันข้อ 3.1 และเวลารวมทั้ง job
4. รัน `tests/test_task_control.py` ซ้ำตอนเครื่องว่าง
5. ตรวจงานจาก workflow ในหัวข้อ 5 เมื่อจบ (ผม/Claude จะสรุปผลให้เมื่อจบเช่นกัน)
