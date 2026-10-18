# RTX installation / test / dev handoff

เตรียมสำหรับทดสอบเครื่อง RTX วันที่ **18 ตุลาคม 2026**
ฐานสคริปต์ติดตั้ง: commit `7d90d7f` — ใช้ `git rev-parse HEAD` บันทึกรุ่นที่ทดสอบจริง

**สถานะ:** เตรียม Windows CUDA archive, Ubuntu CUDA build, Torch cu128 และ WGPU แล้ว
ยังไม่ได้ทดสอบติดตั้ง/Train บน RTX จริง หรือ build บน Ubuntu จริง
หลักฐานบนเครื่องพัฒนาอยู่ใน [INSTALLATION_REVIEW.md](../reviews/INSTALLATION_REVIEW.md)

## 1. ก่อนติดตั้ง

- [ ] ใช้ Windows 10/11 x64 หรือ Ubuntu 24.04 x86_64
- [ ] Clone/pull โค้ดล่าสุด แล้วเปิด Terminal ในโฟลเดอร์โปรเจกต์
- [ ] มี NVIDIA driver ที่ใช้งานได้: `nvidia-smi` แสดงชื่อ RTX และ driver
- [ ] มีอินเทอร์เน็ตสำหรับ packages, tools และ weights; ตรวจพื้นที่ว่างสำหรับวิดีโอ/Views/Models ด้วย
- [ ] Windows มี Microsoft App Installer (`winget`); Ubuntu มีสิทธิ์ `sudo`
- [ ] เครื่องปลายทางไม่มี job ที่กำลังทำงานก่อนติดตั้ง/ซ่อม dependencies

ตัวติดตั้งไม่ลง display driver และไม่ reboot ให้ หาก `nvidia-smi` ใช้ไม่ได้
ให้แก้ driver และ reboot ก่อน ดูรายละเอียด Ubuntu/WSL ใน [UBUNTU.md](UBUNTU.md)
ค่า CUDA Version ใน `nvidia-smi` เป็นความสามารถของ driver ไม่ได้ยืนยันว่ามี `nvcc` หรือ Torch CUDA แล้ว

## 2. ติดตั้งและเปิดแอป — สองสคริปต์

รอบทดสอบ RTX แนะนำระบุ CUDA ชัดเจน เพื่อให้การติดตั้งแจ้ง error หากตรวจไม่พบ
แทนการเลือก CPU ในโหมด auto

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File .\app_1_install.ps1 -Device cuda
powershell -ExecutionPolicy Bypass -File .\app_2_run.ps1
```

Ubuntu:

```bash
bash app_1_install.sh --cuda
bash app_2_run.sh
```

Windows ดาวน์โหลด COLMAP 4.2.1 CUDA พร้อมตรวจ SHA-256;
Ubuntu build COLMAP 4.2.1 พร้อม CUDA และใช้ `nvcc` ที่ตรวจพบ
สำหรับ RTX 50 series บน Ubuntu สคริปต์ตรวจ `sm_120` และเตรียม toolkit 12.8 หากจำเป็น
Torch/torchvision/torchaudio ใช้ชุด 2.7.1/0.22.1/2.7.1 cu128 ส่วน Brush 0.3.0 ใช้ WGPU
Windows ไม่ต้องติดตั้ง CUDA Toolkit เพื่อ build COLMAP เอง เพราะใช้ archive ที่ build แล้ว

- [ ] Installer จบ `[7/7] Ready` ไม่มี `[FAIL]` หรือคำสั่ง exit nonzero
- [ ] มี `tools/install-logs/verify.json` และ log ติดตั้งของรอบนี้
- [ ] เปิด URL ที่ run script แสดง (ปกติ `http://127.0.0.1:8000`)
- [ ] Dashboard → **Verify** แสดง RTX และ backend ตามตารางด้านล่าง

ตัวเลือก `-Device cuda` / `--cuda` เป็นตัวเลือกติดตั้งและ Verify
การรันสองคำสั่งด้านบนยังใช้ runtime **auto** และอาจ fallback ได้ จึงต้องตรวจ log ของงานจริงด้วย

## 3. Backend ที่คาดหวัง

| ขั้นตอน | ผลที่ควรเห็นบน RTX | ตรวจจาก |
|---|---|---|
| Upload / decode / JPEG / disk | CPU + Disk | สถานะ Upload และ log; ไม่ต้องมี CUDA ทุกส่วน |
| Panorama → Views | WGPU บน NVIDIA RTX; backend ตาม OS/driver เช่น Vulkan หรือ D3D12 | `view_compute` / `views.log` / Console |
| COLMAP สกัด features / matching | CUDA | `colmap_backend`, ข้อความ `COLMAP compute` และ `3d.log` |
| COLMAP mapper / bundle adjustment | ยังมีงาน CPU ตาม pipeline ปัจจุบัน | การเพิ่ม `num_reg_frames` / ข้อความ bundle adjustment |
| Masks | CUDA | `quality.mask_compute`, backend/device และ fallback reasons |
| Brush Train Model | NVIDIA RTX ผ่าน WGPU | `compute_device`, `compute_backend` และ Brush log |
| Export / บีบอัด / GLB | CPU ในหลายขั้นตอน | Console / process log |

Torch CUDA กับ Brush เป็นคนละเส้นทาง: `torch.cuda.is_available()` อย่างเดียว
ยังไม่ยืนยันว่า Brush ใช้ RTX และ GPU load ต่ำระหว่าง mapper ไม่ได้แปลว่างานหยุด
Verify เป็น inventory + checks; การทำงานจริงต้องผ่านข้อ 4

## 4. ทดสอบงานจริงจากชุดเล็ก

ใช้วิดีโอ 360 ที่ stitch แล้วแบบ 2:1 ช่วง **5–10 วินาทีที่กล้องเคลื่อนจริง**
เริ่มด้วย **Preview** และค่าที่แนะนำก่อน อย่าเริ่มด้วย Ultra เต็มคลิปยาว
ไฟล์ทดลองเล็กใน `data/raw/` ใช้ได้หากมีใน clone; หากไม่มีให้อัปโหลดคลิปทดสอบเอง
งานเดิมใน `web_data/` และวิดีโอใหญ่ไม่ได้ย้ายตาม Git ไปเครื่องใหม่

- [ ] Upload เห็นชื่อไฟล์, %, จำนวนข้อมูล และขั้นตอนบันทึกจนสำเร็จ
- [ ] ฟอร์มสร้าง Views โหลดภาพพรีวิวได้ เปลี่ยนช่วง/มุมได้
- [ ] สร้าง job **ใหม่** สำหรับ RTX; จด source, settings, job ID และ run ID
- [ ] Views เสร็จ ภาพเปิดได้ และ log ระบุ NVIDIA WGPU ไม่ใช่ CPU fallback
- [ ] Camera Pose หาได้จากหลายตำแหน่ง; Camera Preview มีเส้นทางสมเหตุผล
- [ ] Features/matching ระบุ CUDA; หากเป็น CPU ให้จดเหตุผลก่อนสรุปผล RTX
- [ ] Masks ระบุ CUDA และ Train ระบุ NVIDIA RTX / backend ของ Brush จริง
- [ ] Console มีความคืบหน้า, เวลาแต่ละขั้นตอน และไม่มี error ที่ถูกมองข้าม
- [ ] Model ผ่าน quality evaluation มี held-out views ที่ใช้ได้ แล้วเปิดหน้า 3D ได้
- [ ] เปิดภาพเทียบ Render และเดินดู geometry/มุมใหม่ใน Viewer — `done` อย่างเดียวไม่ยืนยันคุณภาพ
- [ ] ลอง SPZ / GLB / ZIP สำหรับ Three.js อย่างน้อยหนึ่งรอบ

เมื่อชุดเล็กผ่าน ค่อยลองคลิป DJI จริงด้วย High แล้วจึง Ultra
หากจะเทียบเวลา ใช้ไฟล์ ช่วงคลิป settings และจำนวนตำแหน่งเดียวกัน
บันทึกเวลาของ Views / features / matching / poses / masks / training / export แยกกัน

## 5. เมื่อพบปัญหา

| อาการ | ตรวจต่อ |
|---|---|
| Installer หยุดก่อน Ready | ดูขั้น `[n/7]` และ error แรกใน install log; อย่าข้าม Verify |
| `nvidia-smi` ใช้ไม่ได้ | Driver / reboot / OS ก่อน dependencies ของแอป |
| COLMAP ยัง CPU | ตรวจ Verify ว่าใช้ executable ใด, banner มี CUDA หรือไม่, env `COLMAP_BIN` / `COLMAP_DEVICE` และ fallback reason |
| Torch CUDA ไม่พร้อม | ตรวจว่าติดตั้ง Torch cu128 ใน venv ของแอป และ driver รองรับ; เก็บข้อความ error จริง |
| Masks ไป DirectML/CPU | ดู `mask_compute` และ fallback reasons; เป็น fallback ไม่ใช่ผลทดสอบ CUDA ผ่าน |
| Brush ไม่เห็น RTX | ดูชื่อ adapter/backend ใน Brush log และ driver/Vulkan/D3D12; อย่าแก้เฉพาะ Torch แล้วสรุปว่า Brush พร้อม |
| CUDA OOM / device lost | เก็บ log และ settings; ทดลอง job ใหม่ที่เล็กลงหลังงานเดิมจบ ไม่แก้ dataset ของ run เดิม |
| Pose ช้า / solver warning ซ้ำ | ดูจำนวนกล้องและ timestamp ล่าสุด; แยก bundle adjustment ออกจาก Train ไม่ประเมินจาก %GPU อย่างเดียว |

### Camera Pose สำหรับคลิปยาว (2026-10-18)

อัปเดตล่าสุด: Windows installer เลือก libraries ตามการ์ดและติดตั้ง CUDA/cuDSS bundle
ที่ผ่าน spherical GPU smoke แล้ว ดู [GPU_BA_WINDOWS.md](GPU_BA_WINDOWS.md)
บันทึกด้านล่างที่ว่าไม่มี Ceres CUDA เป็นผลของ official bundle เดิมใน `tools/colmap/`
แอปเลือก bundle ใหม่ใน `tools/colmap-cuda-ba/` เมื่อ receipt ถูกต้องและพบ NVIDIA GPU

- `COLMAP_MAPPER_PROFILE=auto` (ค่าเริ่มต้น): ตั้งแต่ 60 panorama หรือการทำต่อจาก cache ใช้ `balanced`
- `balanced`: global BA frames/points ratio `1.2` และ function tolerance `1e-6`
  ลดการเรียก global solve ซ้ำและเปิดเกณฑ์ convergence; เก็บ features, points, iteration limits และ quality gates เดิม
- ใช้ `COLMAP_MAPPER_PROFILE=full` เพื่อเทียบกับค่า BA เดิม หรือ `balanced` เพื่อบังคับใช้กับชุดเล็ก
- Native mapper รับค่าตอนเริ่ม process ต้องหยุดและเก็บงาน รอ COLMAP ปิด แล้วเปิดแอปใหม่ด้วย `app_2_run.ps1`
  ก่อนกด **ทำต่อจาก Camera Pose** จึงจะใช้โค้ดใหม่ได้ ตรวจ `COLMAP mapper profile:` และคำสั่ง mapper ใน `3d.log`
- Progress ขั้น Pose คำนวณ `num_reg_frames / จำนวน panorama × 100` และแสดงกิจกรรม Global BA
  ใช้จำนวนตำแหน่ง ไม่ใช้จำนวน perspective Views; เป็นสัดส่วนโดยประมาณและใช้แทนเปอร์เซ็นต์เวลาไม่ได้
  ช่วง BA อาจหยุดนับนาน และ mapper อาจหาได้ไม่ครบทุกภาพ; แถบไม่ขึ้น 100% ขณะยังรัน BA/ตรวจผล
- ยังไม่มี benchmark ยืนยันอัตราเร่งหรือคุณภาพของรอบ `balanced` บน RTX 5070 ต้องเทียบเวลาขั้น poses,
  จำนวนกล้อง/จุด, reprojection error และ Camera Preview หลังรอบจริงเสร็จ

**GPU ของขั้น BA:** ตรวจ binary ของเครื่องนี้ด้วย spherical fixture แยก 4 กล้อง / 30 จุด
และ `BundleAdjustmentCeres.use_gpu=1` แล้ว (2026-10-18) พบคำเตือน Ceres ถูก compile
โดยไม่มี CUDA และ cuDSS จึง fallback CPU ทั้ง dense/sparse solver แม้คำสั่งจบด้วย exit code 0
ดังนั้น banner `with CUDA` และการมี flag `ba_use_gpu` ยังไม่ยืนยันว่า BA ใช้ GPU ได้
หลักฐานเฉพาะเครื่องอยู่ที่ `tools/install-logs/ba-cuda-capability.json` และ native log ที่อ้างในไฟล์นั้น

การเปิด GPU BA ต้องใช้ Ceres ที่ build พร้อม CUDA/cuDSS แล้ว link กับ COLMAP ใหม่
ส่วน Caspar ของเวอร์ชันนี้รองรับเฉพาะ PINHOLE/SIMPLE_RADIAL จึงใช้แทนกับ EQUIRECTANGULAR ไม่ได้
ดู [COLMAP: Speedup bundle adjustment](https://colmap.github.io/faq.html#speedup-bundle-adjustment)
การแก้ progress ให้อ่าน `num_reg_frames` จนถึงวงเล็บปิดจะมีผลเมื่อเปิด backend รอบหน้า
เพื่อไม่อ่านเลขบางส่วนขณะที่ native process กำลังเขียน log

ตรวจ readiness ซ้ำแบบไม่ติดตั้งหรือเริ่ม GPU inference:

```powershell
powershell -ExecutionPolicy Bypass -File .\app_1_install.ps1 -Check -Device cuda
```

```bash
bash app_1_install.sh --check --cuda
```

Check ไม่เขียน `verify.json` ทับ: ไฟล์นั้นเป็นผลจากการติดตั้งล่าสุด ให้เก็บ output ของ Check เพิ่มเมื่อแก้ driver/config
การระบุ CUDA จะช่วยตรวจ COLMAP/Mask; ยังต้องตรวจชื่อการ์ด WGPU ในงาน Views/Brush จริง
ระหว่างมี job ใช้ Check ได้ แต่การติดตั้ง/ซ่อม libraries ต้องรอให้งานจบก่อน

สำหรับ dev: `scripts/check_runtime.py` มี smoke test ที่ทำ GPU projection และ mask inference จริง
ให้ใช้ตอนเครื่องว่างเท่านั้น ส่วน `scripts/check_gpu_pipeline.py` รองรับ `--mask-device cuda|directml|auto`
โดย `auto` ลอง CUDA ก่อน แล้ว DirectML บน Windows และถือว่าไม่ผ่านหาก fallback ไป CPU
ผล masks วัดเฉพาะ inference จาก tensor ที่เตรียมแล้ว ไม่รวม decode/transform ภาพหรือเขียน PNG
ส่วน Views วัดการสร้างภาพจริงผ่าน CPU/WGPU; ทั้งสองส่วนตรวจ parity กับ CPU ด้วย

ตัวอย่างตรวจ CUDA บนคลิปสั้น (ไม่แก้ job เดิม):

```powershell
.\.venv-win\Scripts\python.exe -X utf8 .\scripts\check_gpu_pipeline.py --source .\data\raw\360loc_concourse_walk_preview_2k.mp4 --output .\tools\install-logs\rtx-cuda-pipeline.json --resolution 448 --seconds 2 --mask-device cuda
```

## 6. เก็บหลักฐานเพื่อพัฒนาต่อ

เก็บในเครื่องปลายทางก่อนแก้ env/driver หรือสร้างรอบใหม่:

- [ ] `git rev-parse HEAD` และ `git status --short`
- [ ] OS, รุ่น RTX, VRAM, driver และผล `nvidia-smi`
- [ ] `tools/install-logs/` รวม install log, `verify.json` และ output Check ล่าสุด
- [ ] `pip freeze` จาก venv ของแอป (Windows `.venv-win`; Ubuntu `.venv`)
- [ ] job/run ID, Check config JSON, รูป error และ backend ที่เลือกจริง
- [ ] `web_data/jobs/<job>/model_runs/<run>/3d.log`, `status.json`, `process.log`, `process_summary.json` เท่าที่มี
- [ ] Views: `views_status.json`, `views.log` และ `view_runs/<run>/` เท่าที่มี
- [ ] กรณี Mask: log ใน dataset ที่ใช้จริง เช่น `masks.log`; จด path จาก run ไม่เดาว่าเป็น dataset ล่าสุด

คำสั่งบันทึกเวอร์ชัน libraries (ไม่ติดตั้ง packages):

```powershell
.\.venv-win\Scripts\python.exe -m pip freeze > tools\install-logs\rtx-pip-freeze.txt
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
```

```bash
.venv/bin/python -m pip freeze > tools/install-logs/rtx-pip-freeze.txt
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
```

`tools/` และ `web_data/` ไม่ถูก push ตาม Git เก็บหรือคัดลอกหลักฐานเองก่อนย้ายเครื่อง

### บันทึกผล / ข้อความสำหรับ dev session ถัดไป

```text
Project: 360 Studio
Checklist: RTX_CHECKLIST.md
Commit / branch:
OS / version:
RTX / VRAM / driver:
Install: ผ่าน / ติดที่ขั้น ...
Verify: ผ่าน / failed checks ...
Source + ช่วงคลิป + profile:
Job ID / View run ID / Model run ID:
Views backend:
COLMAP features/matching backend:
Masks backend:
Brush GPU / backend:
เวลาแต่ละขั้นตอน:
Quality / geometry ที่ตรวจแล้ว:
อาการ + error แรก + log paths:
สิ่งที่แก้/ลองแล้ว:
งานที่ยังรันอยู่ (ห้าม restart/ติดตั้งทับ):
สิ่งที่ต้องทำต่อ:
```
