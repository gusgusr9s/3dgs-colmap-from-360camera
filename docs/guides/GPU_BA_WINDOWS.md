# Windows: เลือกชุด libraries ตาม GPU และเปิด CUDA/cuDSS BA

อัปเดต 2026-10-18 สำหรับ COLMAP 4.2.1 และ RTX 5070

## ติดตั้งและเปิดแอป

```powershell
powershell -ExecutionPolicy Bypass -File .\app_1_install.ps1
powershell -ExecutionPolicy Bypass -File .\app_2_run.ps1
```

ค่าเริ่มต้น `-Device auto` ตรวจ driver/GPU แล้วเลือกชุดต่อไปนี้:

| เครื่อง | Torch | COLMAP features / matching | Global BA (staged mapping) | Mask | Views / Train |
|---|---|---|---|---|---|
| NVIDIA ที่รองรับชุด CUDA เช่น RTX 5070 | cu128 | CUDA | CUDA dense (cuSOLVER) ตามผล benchmark | Torch CUDA | WGPU |
| Windows AMD เช่น RX 6700 XT / Intel | CPU | CPU | CPU iterative Schur ตามผล benchmark | DirectML | WGPU |
| บังคับ `-Device cpu` | CPU | CPU | CPU iterative Schur | CPU | Views ใช้ CPU ได้; Brush ยังต้องใช้ hardware GPU |

Local BA ของ COLMAP (6 ภาพ) และการลงทะเบียนภาพใช้ CPU ทุกเครื่องตามการออกแบบของ COLMAP

บน Windows GPU NVIDIA รุ่นเก่าที่ compute capability ต่ำกว่า 7.5 จะไม่เลือก bundle CUDA นี้
โดยอัตโนมัติ เมื่ออ่าน compute capability ไม่ได้ จะใช้ผล smoke จริงตัดสิน
`install-profile.json` ใน `tools/install-logs/` บันทึกชุดที่เลือก

`requirements-gpu.txt` เป็น WGPU ที่ใช้ร่วมกัน ส่วน ONNX/DirectML แยกไป
`requirements-directml.txt` และติดตั้งเฉพาะ profile DirectML การติดตั้งไม่ได้ถอน packages
ที่เคยลงไว้ด้วย profile อื่นจาก venv เดิม

## ชุด CUDA/cuDSS ที่ใช้

Windows CUDA installer ใช้ community build ของ upstream COLMAP **4.2.1** จาก
[lyehe/build_gpu_colmap v4.2.1](https://github.com/lyehe/build_gpu_colmap/releases/tag/v4.2.1)
มี Ceres CUDA, cuDSS 0.7.1.4 และ runtime CUDA 12.8.1 ในแพ็กเกจ ไม่ต้องติดตั้ง compiler/Toolkit เพิ่ม

- Archive: `COLMAP-4.2.1-windows-2022-CUDA-cuDSS-Caspar.zip` (~1.96 GB)
- SHA-256: `a36bc01763b3ecf50a43ebf541a3727e209998479a1821fd7900b33b75e2b4aa`
- Upstream commit: `bd1fcf654d2dd8fefa1466999c190a246f83f4b9`
- ติดตั้งที่ `tools/colmap-cuda-ba/` แยกจาก official bundle เดิมใน `tools/colmap/`
- ตรวจ hash และ `build_info.json` ก่อนทดสอบ/เปิดใช้งาน
- ใช้ backend **CERES** กับกล้อง EQUIRECTANGULAR; ไม่เปิด Caspar ซึ่งไม่รองรับกล้องชุดนี้

Installer สร้าง spherical fixture แยก 4 กล้อง / 30 จุด แล้วทดสอบทั้ง dense CUDA และ sparse cuDSS
โดยบังคับขนาดปัญหาให้เข้าถึง GPU solver จึงไม่ข้าม GPU เพราะ fixture มีภาพน้อย
ตรวจ output model และปฏิเสธคำเตือน CPU fallback แม้ command จบด้วย exit code 0
เมื่อผ่านทั้งคู่จึงสร้าง `gpu_ba_verified.json` พร้อม hash ของ executable และ solver DLLs
แอปตรวจ receipt นี้ก่อนเลือก bundle และเพิ่ม `--Mapper.ba_use_gpu 1`

## ตรวจซ้ำ

ตรวจ inventory / receipt โดยไม่เริ่ม GPU solve:

```powershell
powershell -ExecutionPolicy Bypass -File .\app_1_install.ps1 -Check
```

ทดสอบ GPU BA จริงด้วย fixture แยก (ไม่แก้ Job):

```powershell
.\.venv-win\Scripts\python.exe -X utf8 scripts/check_colmap_ba.py --colmap tools/colmap-cuda-ba/bin/colmap.exe
```

ผลอยู่ที่ `tools/install-logs/colmap-ba-smoke.json` และ log dense/sparse ของแต่ละ probe
หน้า Verify แสดง BA แยกจาก features/matching; สถานะ Job และ `3d.log` ระบุ backend ที่ใช้
ใช้ `COLMAP_BA_DEVICE=cpu` เพื่อเทียบผล หรือ `cuda` เพื่อให้รันไม่ได้เมื่อไม่มี receipt ที่ผ่าน

## ผลตรวจบน RTX 5070 และขอบเขต

- Installer แบบ `auto` เลือก `nvidia-cuda`; dependency check, Torch CUDA kernel และ HTTP startup ผ่าน
- Dense CUDA / sparse cuDSS ของ spherical fixture ผ่านทั้งคู่
- ทดสอบ BA ของ sparse model จริง **785 กล้อง / 304,036 จุด** ใน output แยก ด้วย iteration limit 3 เท่ากัน
  และ function tolerance 1e-6; ไม่เขียนทับ source Job
- Official CPU 28.438 s, bundle ใหม่ CPU 27.015 s, bundle ใหม่ GPU 28.985 s
- GPU trial sampled utilization สูงสุด 45%, VRAM สูงสุด 3,919 MiB
- ทั้งสามผลคงจำนวนกล้อง/จุดเท่ากัน, finite และ mean reprojection error 0.5780588402 px
- ตัวอย่างนี้ convergence หลัง 1 iteration จึงไม่ได้ยืนยันอัตราเร่งในช่วง mapper ที่ยังหา pose เพิ่ม
  การใช้ GPU ไม่รับรองว่าแต่ละ BA เร็วขึ้น: ยังมี CPU residual evaluation, graph setup และการอ่าน/เขียน model

หลักฐาน: `web_data/capture_review/ba_cuda_20261018_131818/report.json`,
`tools/colmap-cuda-ba/gpu_ba_verified.json`, `tools/install-logs/verify.json`
เส้นทางเลือก libraries สำหรับ RX 6700 XT ตรวจด้วย regression ที่จำลอง hardware;
รอบนี้ไม่ได้ทดสอบการ์ด AMD จริง

Regression รอบนี้: Python 312 tests ผ่าน (ข้าม 9), JavaScript task history และ job logs ผ่าน
เปิด backend ใหม่แล้วตรวจ HTTP/Verify ผ่าน โดย Job เดิมยังอยู่สถานะ `stopped`

## Staged mapping และการเลือก Global BA solver

ปัญหาที่พบใน Job `6916823944` (1,230 panoramas): `mapper` ของ COLMAP เลือก solver จากจำนวนภาพแบบตายตัว
(CUDA เกิน 200 ภาพใช้ sparse cuDSS, CPU 50–1000 ภาพใช้ CHOLMOD) และทำ retriangulation + BA ซ้ำสูงสุด 5 รอบ
ทุกครั้งที่ทำ Global BA ช่วง Global BA จึงใช้ CPU ราว 1 core และ GPU ~5–10% เป็นเวลา ~21 จาก 30.6 นาที
ส่วน `mapper` ไม่มี option เปลี่ยน threshold เหล่านี้

ภาพ 360 องศามองเห็นกันกว้าง ระบบสมการของกล้องจึงเกือบ dense ผลวัดบน snapshot จริง 935 panoramas
(6.88 ล้าน residuals ทุกวิธีได้ final cost 0.477172 px เท่ากัน):

| Solver | เวลาจนลู่เข้า |
|---|---|
| CUDA dense (cuSOLVER) | 39.7 s |
| CPU iterative Schur | 46.8 s |
| CUDA sparse (cuDSS) — ค่าที่ mapper ใช้ | 79.1 s |
| CPU sparse (CHOLMOD) — ค่าที่ mapper ใช้บน CPU | ~94 s (5 iterations) |

`app/pipeline/staged_mapping.py` จึงรัน `mapper` เป็นช่วง (เพิ่มครั้งละ ~20% ตาม balanced profile ผ่าน
`--Mapper.image_list_path`) โดยปิด Global BA ระหว่างทาง แล้วให้ `bundle_adjuster` แก้ Global BA ด้วย solver
ที่วัดแล้วเร็วที่สุดก่อนช่วงถัดไป ปิดท้ายด้วย `point_filtering` (4 px, 1.5°) และ BA อีกรอบ
200 ภาพแรกยังใช้ mapper ปกติ (mapper เลือก dense CUDA เองในช่วงนี้)

ผลทดสอบบนสำเนาของ Job เดียวกัน ต่อจาก model 785 ภาพเดียวกัน (ไม่แตะ Job):

| | mapper เดิม | staged mapping |
|---|---|---|
| เวลา 785 → 1230 ภาพ | 30.6 นาที | **19.0 นาที** (ขณะ Brush train ใช้ GPU อยู่) |
| ภาพที่ลงทะเบียน | 1230 | 1230 |
| จุด 3D | 459,024 | 459,284 |
| Mean reprojection error | 0.5583 px | 0.5586 px |
| ตำแหน่งกล้องหลัง align | — | ต่าง median 0.043% ของรัศมีฉาก |

ค่าเริ่มต้น `COLMAP_STAGED_MAPPING=auto` เปิดใช้เมื่อ profile เป็น balanced และมีมากกว่า 240 panoramas
(`on`/`off` บังคับได้) ส่วน `COLMAP_BA_SOLVER=auto` ใช้ลำดับจาก benchmark ของ executable ตัวนั้น
(`cuda_dense`, `cpu_iterative`, `cuda_sparse`, `cpu_sparse` บังคับได้) CUDA dense จะถูกข้ามเมื่อเกิน 3,000 ภาพ
หรือ VRAM ว่างไม่พอ (~(6N)²×16 bytes + 768 MiB) Job status บันทึก `colmap_global_ba_solver` และ `3d.log`
มีบรรทัด `Staged mapping:` ทุกช่วง checkpoint ล่าสุดอยู่ใน `snapshots/staged-*` ใช้ทำต่อได้

## Benchmark ตอนติดตั้งและใน Verify

- `app_1_install.ps1` / `app_1_install.sh` ขั้น [6/7] รัน `manage.py verify --smoke --execute`: ทดสอบ Views,
  Mask, COLMAP features/matching และ BA จริงทั้ง CPU และ GPU แล้วจับเวลา Global BA ทุก solver บน fixture
  360 องศา (360 ภาพ / 1.3 ล้าน observations, ~45 s) บันทึกที่ `tools/install-logs/ba-benchmark.json`
  ผูกกับ SHA-256 ของ `colmap.exe`
- หน้า Dashboard ปุ่ม **ทดสอบรันจริง CPU/GPU** รันชุดเดียวกัน (~1–4 นาที, ต้องไม่มี Job กำลังรัน) แสดงตาราง
  "ขอใช้ / ใช้จริง / เวลา" และบันทึกผล benchmark ใหม่ ปุ่ม Verify เดิมยังตรวจแบบอ่านอย่างเดียว
- RTX 5070 (ระหว่าง Brush train): CUDA dense 6.2 s < CPU iterative 7.0 s < CUDA sparse 9.4 s < CHOLMOD 10.5 s
- RX 6700 XT / เครื่อง CPU: benchmark มีแค่ CPU solvers จึงเลือก CPU iterative Schur (เร็วกว่า CHOLMOD
  ~2 เท่าบนข้อมูลจริง) ยังไม่ได้วัดบนการ์ด AMD จริง ให้รันปุ่มทดสอบรันจริงบนเครื่องนั้นหนึ่งครั้ง

## Mask คน

`create_dynamic_person_masks` ถอดรหัสภาพชุดถัดไประหว่างที่ GPU/DirectML ทำ inference และทำ morphology +
PNG encode ใน thread pool (batch 8 บน GPU) ผลต่อภาพเหมือนเดิมทุก pixel (ทดสอบ 240 ภาพจริง ต่างกัน 0 pixel)
วัดได้ CUDA 22.7 → 48.8 ภาพ/วินาที และ CPU 12.6 → 19.5 ภาพ/วินาที (ขณะเครื่องมีงานอื่น)

## ข้อจำกัดที่ยังเหลือ

- Bundle CUDA ชุดนี้ build ให้ arch `75;80;86;89;90`; RTX 5070 (sm_120) ใช้ PTX JIT ของ sm_90 ส่วน
  cuBLAS/cuSOLVER/cuDSS มี kernel ของ Blackwell เอง การ rebuild ต้องใช้ MSVC + CUDA Toolkit ซึ่งเครื่องนี้ไม่มี
- การลงทะเบียนภาพและ Local BA ของ COLMAP เป็น CPU thread เดียว (~1.1 s/ภาพ) เป็นงานหลักที่เหลือในขั้น Pose
- ท้ายแต่ละช่วง mapper ยังทำ retriangulation + BA 1 step สองครั้ง (~40–50 s) ปิดด้วย option ไม่ได้
- Caspar ไม่รองรับ EQUIRECTANGULAR: ข้ามทุกภาพแต่จบด้วย exit 0 แอปจึงไม่ใช้ Caspar

อ้างอิง [COLMAP: GPU bundle adjustment](https://colmap.github.io/faq.html#speedup-bundle-adjustment)
