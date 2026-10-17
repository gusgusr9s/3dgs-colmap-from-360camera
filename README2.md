# 360 → 3D Gaussian Splatting prototype

CPU processing ใช้โหมด `full` เป็นค่าเริ่มต้น: CPU Views ทำหลายมุมพร้อมกันใน worker,
COLMAP ยกเลิกเพดาน 8 threads และ Masks ใช้ budget ของ Torch/OpenCV ใน subprocess.
ปรับด้วย `--cpu-mode full|balanced --cpu-threads N` หรือ PowerShell
`.\app_2_run.ps1 -CpuMode full`. หน้า Job มี **Logs / เวลาแต่ละขั้นตอน** แยกต่อรอบ
ทั้ง Views / COLMAP / Model ดูรายละเอียดและคำสั่งวัดผลใน [CPU_PERFORMANCE.md](CPU_PERFORMANCE.md).

Dashboard มีปุ่ม **Verify · ตรวจความพร้อมเครื่อง** แสดง Python environment, GPU,
backend และเหตุผลสำหรับ Upload, Preview, Views, COLMAP, Masks, Brush และ Export.
ตรวจแบบอ่านข้อมูลเท่านั้น ไม่ train / inference / ดาวน์โหลด weights จึงตรวจได้ระหว่างทำงาน
ผล adapter เป็นความพร้อมเบื้องต้น; การ์ดและ backend ที่ใช้จริงต้องดูสถานะ / log ของงานนั้นด้วย
เรียก script แยกได้โดยใช้ Python environment เดียวกับแอป:

```powershell
.\.venv-win\Scripts\python.exe -X utf8 scripts/verify_environment.py
```

Ubuntu ใช้ `python scripts/verify_environment.py`; เพิ่ม `--json` เพื่อรับรายงาน JSON.
แผนโปรแกรมติดตั้งและ patch อยู่ใน [DESKTOP_PLAN.md](DESKTOP_PLAN.md) (ยังไม่ได้สร้าง Electron).

Upload แสดงชื่อไฟล์และแถบสถานะก่อนเริ่มเตรียมส่ง เห็นเปอร์เซ็นต์ / bytes ระหว่างส่ง
และเวลาที่รอตรวจ / บันทึกหลังส่งครบ กดยกเลิกได้ระหว่างส่งและลองใหม่จากไฟล์ที่เลือกไว้
เมื่อส่งครบแล้วต้องรอผลบันทึกก่อน ส่วนพรีวิวสร้าง Views โหลดเฉพาะภาพมุมที่เลือก
พร้อม Loading / error / retry และยกเลิกคำขอเก่าเมื่อเปลี่ยนค่า จำกัด decoder CPU หนึ่งตัว
และ timeout แยกจากคิว GPU; CPU / ดิสก์ยังเป็นทรัพยากรที่ใช้ร่วมกับงาน Model

สถานะงานแสดงเปอร์เซ็นต์ขั้นตอนจากจำนวน Views, features, matching, masks หรือ training steps
เมื่อมีจำนวนงานรวม ส่วน Pose / evaluation / export ที่ไม่มีตัวนับจะแสดงกำลังทำงาน
และเวลาที่ log / สถานะอัปเดตล่าสุด เปอร์เซ็นต์ภาพรวมของ Model เป็นค่าประมาณตามช่วงขั้นตอน
ไม่ใช่เปอร์เซ็นต์เวลาที่ใช้ไป

เปิด [สรุปความสามารถทั้งหมดแบบ HTML](PROJECT_OVERVIEW.html) ได้โดยตรง หรือเปิด
`/project-overview` ในเว็บแอป มี 45 ความสามารถพร้อมเงื่อนไข ข้อจำกัด และลิงก์เข้าใช้งาน.
ดาวน์โหลด HTML จากปุ่มในหน้านี้ได้ ตรวจย้อนหลังและผลปรับ UI อยู่ใน [UI_REVIEW.md](UI_REVIEW.md).

ปุ่มใช้ไอคอน SVG คู่ข้อความ: **เขียว = เริ่มงาน/บันทึก**, **ฟ้า = เปิดหน้า**,
**เหลือง = ดาวน์โหลด**, **แดง = ลบ**. รายการแสดงงานหลักและทางลัดที่ใช้บ่อย
ส่วน config, เปลี่ยนชื่อ, ส่งออกเพิ่มเติม และลบ อยู่ในเมนู **⋯ เพิ่มเติม** แยกหมวด.
Sources/Jobs/Models ค้นหาและเรียงชื่อ เวลา ขนาดได้ พร้อมเก็บตัวกรองใน URL.
Gallery ค้นหาทั้งชุดก่อนแบ่งหน้า; ประวัติรุ่น/ผลงานจาก Source/ผลทดลองมีค้นหาและลำดับของตัวเอง.
หน้าคู่มือค้นหาหัวข้อได้ และตาราง Benchmarks กรอง/เรียงตามคอลัมน์ได้.

Job ใหม่และชื่อที่แก้ไขจะเติม `(2)`, `(3)` เมื่อซ้ำ โดยเก็บงานเดิมไว้.
การอัปโหลด Source/PLY แสดงเปอร์เซ็นต์ จำนวนข้อมูลที่ส่ง และขั้นตอนตรวจ/บันทึก
ก่อนยืนยันสำเร็จ. ยกเลิกได้ระหว่างส่งไฟล์ และลองใหม่จากฟอร์มเดิมได้เมื่อเกิดข้อผิดพลาด
ไฟล์ถูกเพิ่มในรายการเมื่อรับครบแล้วเท่านั้น; ชื่อไฟล์ซ้ำจะเติมลำดับโดยไม่เขียนทับ
action สร้าง Views/Model, เปลี่ยนชื่อ, ลบ และบีบอัดมีสถานะรอผลและป้องกันส่งซ้ำ
หน้าเว็บจะพักการรีโหลดอัตโนมัติระหว่างอัปโหลดหรือส่ง action

Viewer มี **รุ่นโหลดเร็ว**, **mini-map** ที่คลิกย้ายจุดถ่ายได้ และปุ่มดาวน์โหลด
**PLY เต็ม / SPZ บีบอัด** หน้า Models เก็บ record รุ่นบีบอัดที่ลิงก์กลับไปยัง
Source, Views และ Model ต้นทาง ดูวิธีใช้และผลทดลองใน [FAST_MODELS.md](FAST_MODELS.md)

UI ใช้ธีม editor เดียวกันทั้ง workspace, model, camera preview และหน้าตรวจภาพ:
หน้า Sources/Jobs/Models ค้นหาและสลับรายการ/การ์ดได้ เครื่องมือรองอยู่ในเมนู `⋯`
หน้า Model มี Inspector ด้านขวาและ Capture timeline ด้านล่าง กด **Focus** เพื่อขยาย
พื้นที่ชมโมเดล หรือ **Inspector** เพื่อเปิด/ปิดข้อมูลและการตั้งค่า บนจอเล็ก Inspector
เปิดเป็นแผงซ้อน พร้อมปุ่มดาวน์โหลด PLY/SPZ ในหมวด Export & Compression

หน้า Models แยก **Models (PLY)** และ **Models บีบอัด (SPZ)** เป็นแท็บ กดปุ่ม
`← / →` หรือใช้คีย์ลูกศรขณะโฟกัสแท็บเพื่อสลับ ช่องค้นหาใช้กับแท็บที่เปิดอยู่
ลิงก์ `/models#compressed-models` เปิดแท็บบีบอัดโดยตรง ภาพของแท็บนี้เริ่มโหลดเมื่อเปิด
หน้าตรวจภาพแสดงภาพได้ก่อน แล้วคำนวณ PSNR เฉพาะมุมที่เลือกพร้อม cache ที่ตรวจการเปลี่ยนไฟล์

Gallery ในรายละเอียดงานและภาพต้นทางของ Model แบ่งหน้าละ **48 ภาพ** พร้อมปุ่ม
ก่อนหน้า/ถัดไปและหน้าแรก/สุดท้าย ภาพของ Model ยังอ้างอิงชุดที่เก็บตอนสร้างรุ่นนั้น
หน้าตรวจ Render เลือก **ภาพคู่ / ลากเทียบ / รุ่นก่อนและ Mask** เป็นแท็บ และเปลี่ยนภาพ
ด้วยปุ่มลูกศรหรือคีย์ `← / →` เมื่อไม่ได้ใช้ช่องกรอกหรือแท็บ ภาพประกอบเริ่มโหลดเมื่อเปิดแท็บ
ข้อมูลการสร้างและคำอธิบายคะแนนอยู่ในแผงที่กางได้ ส่วน Camera Preview กด **Camera Inspector**
เพื่อซ่อน/เปิดแผงควบคุม บนจอเล็กเริ่มซ่อนเพื่อให้เห็นพื้นที่ 3D ก่อน

## ไฟล์ที่เก็บใน Git

Repository เก็บโค้ด, tests, scripts, viewer assets และวิดีโอตัวอย่างขนาดเล็กใน `data/raw/`:

- `sample_360_preview.mp4`: 8 วินาทีแรกของ OmniPhotos Temple1, ลดเป็น 1920×960 ที่ 15 fps
- `test_360_corridor.mp4`: คลิป panorama เคลื่อนกล้องประมาณ 3 วินาที (ประมาณ 2 MB)
- `test_360_tram.mp4`: คลิป panorama ประมาณ 3 วินาที (ประมาณ 1 MB)

เปิดไฟล์ตัวอย่างได้จาก **Sources** หลังเริ่มเว็บ รายละเอียดที่มาและ license อยู่ใน
[`data/raw/SOURCES.md`](data/raw/SOURCES.md) คลิป preview ถูกย่อและบีบอัดเพื่อทดลองการนำเข้า/แยกเฟรม;
สำหรับประเมินคุณภาพ reconstruction ให้ใช้วิดีโอต้นฉบับความละเอียดเต็ม

ไฟล์วิดีโอต้นฉบับขนาดใหญ่, ภาพที่แตกแล้ว, models, `web_data/`, `output/`, `target/`,
virtual environments และ `tools/` ถูกตัดออกด้วย `.gitignore` โปรแกรมที่จำเป็นติดตั้งได้ด้วย setup scripts
ดาวน์โหลด OmniPhotos Alley/Cathedral/Ship ได้ด้วย `scripts/download_omniphotos.py`
ส่วนคลิปอื่นดูแหล่งดาวน์โหลดใน `data/raw/SOURCES.md`; `test_catalog.json` มีทั้งคลิปที่รวมใน Git
และคลิปที่ต้องดาวน์โหลดเพิ่ม เลือก `--case corridor` หรือ `--case tram` เมื่อต้องการใช้เฉพาะคลิปที่รวมไว้

## Ubuntu / CUDA RTX / DJI

ขณะสร้าง Views/Model หน้าสถานะแสดงเวลาที่เหลือโดยประมาณและเวลาคาดว่าจะเสร็จ (UTC+7).
Views ใช้จำนวนภาพที่สร้างจริง; Model ใช้อัตรา training steps ล่าสุดพร้อมเวลาเผื่อประเมินผล/ส่งออก
และประวัติงานที่ตั้งค่าใกล้กัน. ช่วงเริ่มต้นที่ข้อมูลยังไม่พอจะแสดงว่ากำลังเก็บข้อมูล
และหยุดแสดง ETA เมื่อไม่มีความคืบหน้าใหม่หรือเมื่อทำงานเสร็จ.

คำสั่งติดตั้งและรันบน Ubuntu 24.04 พร้อมแนวทางลองไฟล์กล้อง DJI อยู่ใน
[UBUNTU.md](UBUNTU.md):

```bash
bash app_1_install.sh --cuda
bash app_2_run.sh --port 8000 --colmap-device cuda --mask-device cuda
```

ต้องมี NVIDIA driver ที่ `nvidia-smi` ใช้งานได้ก่อนติดตั้ง CUDA build.
COLMAP และ Mask คนเลือก `auto / cpu / cuda` ได้ ส่วน Brush train บน RTX ผ่าน WGPU/Vulkan.

## Insta360 input

ไฟล์ที่พร้อมสร้าง 3D ต้องเป็นวิดีโอ 360 ที่ stitch แล้วแบบ **equirectangular 2:1** เช่น
`5760x2880` MP4/MOV หรือภาพ JPG/PNG 2:1 เว็บจะตรวจสัดส่วนก่อนประมวลผลและใช้
COLMAP หา camera pose หนึ่งตำแหน่งต่อเฟรม 360

ไฟล์ดิบ `.insv` / `.insp` ยังเป็น dual-fisheye และใช้ตรง ๆ ไม่ได้ ให้เปิดไฟล์คู่ทั้งหมดใน
Insta360 Studio แล้วเลือก **Export 360 Video** ที่ความละเอียดและ bitrate สูงสุด ห้ามเลือก
Reframed Video จากนั้นอัปโหลด MP4 ที่ได้เข้าเว็บ

Preset คุณภาพ:

- `Preview`: มุมรอบแนวนอน, 10,000 steps, สูงสุด 1 ล้าน splats
- `High` (ค่าเริ่มต้น): แนวนอนพร้อมมุมเงย/ก้ม, 20,000 steps, สูงสุด 2 ล้าน splats
- `Ultra`: แนวนอนพร้อมมุมเงย/ก้ม, 30,000 steps, สูงสุด 4 ล้าน splats

เพื่อให้เกิดความลึกจริง ให้เดินกล้องช้า ๆ รอบพื้นที่แทนการหมุนอยู่จุดเดียว รักษาแสงและฉากให้นิ่ง
และเดินผ่านบริเวณเดิมโดยให้แต่ละช่วงเห็นรายละเอียดซ้ำกันมากพอสำหรับการจับคู่ภาพ


## Windows setup (no WSL)

Install Python 3.12 for Windows, then open PowerShell in this project folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\app_1_install.ps1
powershell -ExecutionPolicy Bypass -File .\app_2_run.ps1
```

The installer prepares Python 3.12, FFmpeg/ffprobe, Node.js, Python libraries, COLMAP 4.2.1 (CUDA when an NVIDIA driver is available, otherwise CPU), Brush, assets and weights, then runs Verify and an isolated HTTP smoke test. COLMAP 4.1 or newer is required for its native `EQUIRECTANGULAR` camera model. Brush uses WGPU and can train with supported AMD, NVIDIA, or Intel graphics without HIP or CUDA. The app does not call `ns-train`; Torch CUDA detection is used only for person masks.

The job result displays the device and graphics backend actually selected by Brush, for example `GPU: AMD Radeon RX 6700 XT · Vulkan`. The same adapter is recorded in the job's `3d.log`. COLMAP feature extraction/matching selects CUDA when its executable supports CUDA and an NVIDIA driver is detected; otherwise it uses CPU. Person masks independently select Torch CUDA when available. `torch.cuda.is_available() = False` does not prevent Brush GPU training. Override these choices with `--colmap-device` and `--mask-device` when running `web_app.py`.

## ตรวจภาพและตำแหน่งก่อน train

การสร้าง perspective views เป็นการแตกภาพ 360 เป็นหลายทิศ ยังไม่ได้หา XYZ ของกล้อง
ไปหน้า **Jobs → Camera Preview** แล้วกด **หา Camera Pose เพื่อ preview** เพื่อรัน COLMAP
โดยยังไม่ train Gaussian Splatting จากนั้นตรวจได้ทั้งเส้นทางกล้อง จุดฉากแบบ sparse
และกรอบภาพจริงใน 3D กดจุดกล้องหรือเลื่อนตำแหน่ง เลือกมุมภาพ แล้วกด **มองจากกล้อง**
เพื่อเทียบภาพต้นฉบับ ณ ตำแหน่งนั้น เส้นแดงแสดงช่วงที่ตำแหน่งกระโดดมากกว่าช่วงอื่น
เฟรมที่หา pose ไม่ได้จะแสดงแยกเพื่อกลับไปตรวจ input

ทุกมุมที่แตกจากเฟรม 360 เดียวกันต้องมีจุดศูนย์กลางกล้องเดียวกัน การหมุนอยู่ที่เดิม
จึงไม่ได้เพิ่มตำแหน่งหรือความลึก สเกลของ COLMAP ยังเป็นหน่วยสัมพัทธ์ ถ้าต้องการเมตรจริง
ต้องมี GPS หรือระยะอ้างอิงสำหรับกำหนดสเกล

กด **Train Model ด้วย pose ชุดนี้** หลังตรวจแล้ว ระบบใช้ dataset และ camera matrices
ชุดเดียวกับ preview โดยไม่รัน COLMAP ซ้ำ เมื่อสร้าง views ใหม่ ระบบล้าง pose cache เก่าด้วย
ภาพที่วางใน preview เป็นระนาบสำหรับตรวจ input; ผิวและภาพจากมุมใหม่เกิดหลัง train

Pipeline ปัจจุบัน:

```text
Insta360 → Export 360 MP4 2:1 → Extract frames → Perspective views
         → COLMAP poses + sparse points → Camera Preview
         → Dynamic/camera-rig masks → Brush GPU training
         → Quality evaluation + splat cleanup → Gaussian PLY → Three.js
```

ส่งออก **GLB สำหรับ Three.js** ได้จาก **Models → Models บีบอัด (SPZ) → ⋯ → ส่งออก GLB / Three.js**.
กดเตรียม GLB แล้วดาวน์โหลด `model.glb` กับตัวอย่าง `index.html` ไปเปิดต่อได้
โดยใช้ `GLTFGaussianSplatLoaderExtension` และ `WebGPURenderer`.
ไฟล์ยังเป็น Gaussian Splat มีขนาดใหญ่กว่า SPZ; OBJ/GLB แบบ Mesh ต้องสร้างผิวเพิ่มอีกขั้น.
วิธีเปิดและตัวอย่างโค้ดอยู่ใน [GLB_EXPORT.md](GLB_EXPORT.md).

ถ้าซูมออกแล้วมีหมอก/จุดฟุ้งนอกห้อง เปิด **Model → ตัดขอบห้อง** เพื่อปรับกรอบ
ซ้าย–ขวา หน้า–หลัง พื้น–เพดาน และหมุนกรอบตามผนัง มีพรีวิวเทียบต้นฉบับ ย้อนกลับ
และตัวเลือกเอาก้อนที่ยื่นข้ามขอบออก บันทึกเป็นรุ่นใหม่แล้วส่งออก SPZ/GLB ต่อได้
โดยไม่ต้องสร้าง Views หรือเทรนใหม่ ดูวิธีใช้ใน [MODEL_CROP.md](MODEL_CROP.md).

For stitched 2:1 panorama video, the app estimates one spherical camera pose per source frame with COLMAP. Every perspective crop from that frame then receives the same camera center plus its known yaw. This constraint prevents the stretched or exploding geometry caused by estimating each crop as an unrelated camera. Before Brush training, the app masks people and the nearby camera operator and rejects weak camera paths. PLY export validates numerical values while preserving learned geometry. Long, thin Gaussians and distant centers are not automatically removed: that heuristic degraded real held-out renders.

Open the URL printed by `app_2_run.ps1` (normally `http://127.0.0.1:8000`). The installer includes Brush and Verify; no separate tool installation is needed. See [README.md](README.md) for the two-command quick start.

## Web workflow and CRUD

### เทียบ GPU / Insta360 Cloud

เปิด **เทียบ GPU / Cloud** ในเมนู หรือ `/benchmarks` เพื่อดูตารางค่าประมาณจาก
สรุป AI คู่กับผลจริง RX 6700 XT ของ DJI High 1024, Ultra 1536 และคลิปทดสอบสั้น.
ตารางอ่าน status และ Brush log ของ run ที่ระบุ รวม Views + Model โดยไม่บวก Train ซ้ำ.
เครื่องคำนวณใช้รอบ High เป็น baseline แล้วตรวจค่าคำนวณกับ Ultra อีกหนึ่งรอบ.
เวลา Cloud 60–120 นาที/อ้างอิง 90 นาทีมาจากสรุปที่แนบ ยังไม่มีผล Cloud ฉากเดียวกัน.
ระบบ auto หลักทำงานแล้ว แต่ยังไม่ยืนยันคุณภาพหรือความเทียบเท่าของผลิตภัณฑ์ Insta360.

ส่งออกรายงานพร้อมหลักฐานและสูตรเป็น Markdown/JSON โดยไม่รัน training เพิ่ม:

```powershell
.\.venv-win\Scripts\python.exe scripts/compare_gpu_cloud.py
```

ดูผลใน [GPU_COMPARISON.md](GPU_COMPARISON.md). บน Ubuntu ใช้ Python ของ environment
เดียวกับแอปเรียก script นี้; ต้องมี artifacts ของ runs ที่อ้างอิงใน workspace.

### นำวิดีโอเข้าแล้วสร้างโมเดลอัตโนมัติ

1. อัปโหลดไฟล์ Export 360 MP4 2:1 ใน **Sources** แล้วกด **สร้าง 3D / Preview Views**
2. ลากเวลาเริ่ม–จบ หรือใช้ปุ่มเริ่ม/จบจากเวลาที่วิดีโอกำลังเล่น ไม่ตัดหรือเขียนทับไฟล์ต้นฉบับ
3. เปลี่ยน **เก็บทุกกี่เฟรม** แล้วดูแถบภาพที่เก็บจริง เลื่อนตำแหน่งและทิศเพื่อดู perspective crop
   จำนวนภาพรวมรวมมุมเงย/ก้มด้วย ถ้าระบบลด interval เพื่อรักษาภาพซ้อน จะบอกค่าที่ใช้จริง
4. ใช้ค่าแนะนำได้เลย หรือเปิด **ปรับขนาดภาพและมุมเอง** ขนาดภาพพรีวิวถูกลดเพื่อโหลดเร็ว
   ภาพเทรนใช้ความละเอียดที่ตั้งไว้
5. เปิด **สร้าง Gaussian Splat ต่ออัตโนมัติ** (ค่าเริ่มต้นสำหรับวิดีโอ) แล้วกดเริ่มประมวลผล
   ระบบสร้าง Views → หา COLMAP poses และจุดฉาก → mask คน → กรองจุดตั้งต้นด้วย tracks และ masks
   → เทรน Brush GPU → ตรวจ Render → PLY
   หน้า job แสดงความคืบหน้าและลิงก์เปิดโมเดลเมื่อเสร็จ ปิด checkbox เพื่อสร้าง Views อย่างเดียวได้

เลือกช่วงที่กล้องเคลื่อนผ่านสถานที่จริง มีรายละเอียดเห็นซ้ำกันและภาพไม่เบลอ อย่างน้อย 6 เฟรม
เป็นเพียงเงื่อนไขเริ่มต้น ไม่ได้ยืนยันว่ามี parallax เพียงพอ ภาพในแถบพรีวิวเป็นเฟรมตามเวลา
ตำแหน่ง XYZ และการเชื่อมจุดเกิดตอน COLMAP การหมุนอยู่ที่เดิม ฉากเคลื่อนไหวเยอะ พื้นผิวเรียบ
กระจก หรือไฟล์ที่ reframe แล้วอาจสร้าง 3D ได้ไม่สมบูรณ์; ระบบไม่สามารถรับประกันทุกวิดีโอ

พรีวิวกับงานจริงใช้ sampling plan เดียวกัน: เวลาเริ่มปัดขึ้นถึงเฟรมแรก เวลาออกเป็นขอบเขตไม่รวม
เฟรมนั้น เก็บทุก N เฟรมโดยเริ่มนับจากเฟรมเริ่มคลิป และคงเลขเฟรมจากไฟล์เดิมไปถึง COLMAP
ภาพพรีวิวไม่เขียน dataset หรือประมวลผล COLMAP จึงใช้ตรวจมุม/FOV/ช่วงภาพก่อนเริ่มงานได้
ไฟล์ `capture_plan` ถูกเก็บใน `transforms.json` และประวัติ Views ของแต่ละ job

ถ้า Browser ถอดรหัสวิดีโอต้นฉบับไม่ได้ เช่น DJI 8K HEVC 10-bit หน้า Source และหน้าตัดช่วงคลิป
จะเตรียม Preview แบบ H.264 8-bit กว้างไม่เกิน 1920 px พร้อมแสดงความคืบหน้าและปุ่มลองใหม่
หรือกด **เล่น Preview** ใต้วิดีโอได้เอง ระบบแปลงด้วย FFmpeg บน CPU ครั้งละหนึ่งไฟล์และเก็บไว้ใช้ซ้ำ
การเลือกช่วงเวลาและการสร้าง Views/Model ยังอ้างอิงไฟล์ต้นฉบับความละเอียดเต็ม

ทดสอบ backend: `python -m unittest discover -s tests -p "test_*.py"`
ทดสอบการควบคุมพรีวิว: `node tests/capture_preview_ui.mjs` (จำลอง DOM ไม่มีการ rasterize หน้าเว็บ)
ทดสอบ GPU จริงผ่าน HTTP: เปิดเซิร์ฟเวอร์ที่พอร์ต 8003 แล้วรัน `python scripts/check_capture_pipeline.py`
สคริปต์สร้าง job ใหม่สองคลิปและเก็บภาพพรีวิว/ผลจริงใน `web_data/capture_review/`

ผลทดสอบ GPU จริง 2026-10-08 บน AMD RX 6700 XT / Vulkan:

- Temple ช่วง 1.1–3.1 วินาที: 13 ตำแหน่ง / 208 ภาพ, หา pose ครบ, 20,000 steps
- X4 Export ช่วง 0–4 วินาที: 20 ตำแหน่ง / 320 ภาพ, หา pose ครบ, 20,000 steps
- ทดสอบกรอง seed อัตโนมัติใน Temple: 5,592 → 5,383 จุด โดยไม่ลดภาพหรือตำแหน่ง
  PSNR เฉลี่ยนอก mask 29.17 dB เทียบกับรอบก่อน 29.25 dB; ความต่างเล็กและยังมีเงาคน
  จึงไม่ใช่หลักฐานว่าการกรอง seed อย่างเดียวแก้ความฟุ้งได้
- พรีวิวอ่านได้ทั้ง Temple, X4, Corridor และ Tram; สองรายการหลังทดสอบเฉพาะพรีวิวครั้งนี้

ผลและภาพอยู่ใน `web_data/capture_review/pipeline_results.json` และ `seed_filter_results.json`
เปิดหน้า `/quality/a147ed6bb2/20261008_015213_86c22346` เพื่อเทียบ Temple รอบกรองกับรอบก่อน
และ `/quality/bb41eed643/20261008_013616_352f99d7` เพื่อดู X4
ภาพตรวจเป็น Brush GPU renders ของมุมที่กันไว้ทดสอบ ก่อนกรอง PLY ที่เผยแพร่
ยังไม่ได้ capture หน้าจอ Three.js โดยตรงเพราะ Browser automation ไม่พร้อมในรอบนี้

The web app separates the pipeline into four pages:

- **Dashboard** (`/`) shows totals and recent activity.
- **Sources** (`/sources`) uploads, previews, renames, and deletes images or videos. A source can be converted into a new processing job from a modal.
- **Jobs** (`/jobs`) tracks perspective-view generation, edits labels and settings, reprocesses views, opens Camera Preview (`/poses/<job>`), starts COLMAP or Brush, and deletes jobs.
- **Models** (`/models`) opens Gaussian Splat PLY files with Spark on Three.js, downloads, renames, uploads, and deletes models. The viewer includes captured camera positions and checkpoint fine tuning when an immutable input dataset is available.

Long view-generation and model-generation tasks run in the background. Their cards and detail pages poll the job API for progress. Destructive actions open a confirmation modal; deleting a source or job can optionally remove its downstream data.

### เวลาและประวัติ Model

หน้า Jobs และรายละเอียด job แสดงเวลาเริ่ม–สิ้นสุดของการสร้าง Views/Model และระยะเวลาที่ใช้
ขณะทำงานตัวนับเวลาจะอัปเดตอัตโนมัติ เวลาแสดงตามประเทศไทย (UTC+7)
หน้า Job ใต้ progress และ Camera Preview มี **Console · การทำงานล่าสุด** อ่าน log ในหน้าได้ทันที
ติดตามขั้นตอนปัจจุบันอัตโนมัติ หรือเลือก Views / Camera Pose / Model พร้อม COLMAP, Masks, Train และ Export
พัก/ติดตามต่อ เลื่อนไปข้อความล่าสุด และลองเชื่อมต่อใหม่ได้ โดยไม่หยุดงานประมวลผล
เมื่อเลื่อนขึ้นหรือเลือกข้อความ ระบบพักการเปลี่ยนข้อความเพื่อให้อ่าน/คัดลอกได้
อ่านเฉพาะท้ายไฟล์ไม่เกิน 48 KB / 300 บรรทัด ไม่โหลด log เต็มซ้ำ และใช้กับงานรุ่นก่อนที่ยังรันอยู่ได้
งานเก่าที่ไม่มีข้อมูลเวลาเริ่มจะแสดงเวลาบันทึกไฟล์ โดยไม่ประมาณระยะเวลาย้อนหลัง

กด **สร้าง Model ใหม่** จาก Jobs หรือหน้ารายละเอียด job ได้ทุกครั้งหลังรอบก่อนเสร็จ
ระบบใช้ pose cache ที่ตรวจแล้ว และสร้าง PLY ชื่อใหม่ เช่น `job_20261007_230000_ab12cd34.ply`
เปิดแต่ละรุ่น ดูเวลา ดาวน์โหลด และอ่าน log ได้ใน **ประวัติการสร้าง Model** ของ job
หน้า Models รวมไฟล์ทุกรุ่นและเปลี่ยนชื่อหรือลบแยกรุ่นได้

สถานะและ log ของแต่ละรอบอยู่ใน `web_data/jobs/<job>/model_runs/<run>/`
ข้อมูลเวลาและกล้องเริ่มต้นของแต่ละไฟล์อยู่ใน `web_data/models/<model>.meta.json`
การสร้าง Views ใหม่เก็บ Model ที่สร้างไปแล้วไว้ เมื่อเลือกลบ job พร้อม models
จะลบ Model ทุกรุ่นที่เชื่อมกับ job นั้น

### แนะนำค่าจากไฟล์และประวัติ

เมื่อเปิด **Sources → สร้าง Views** ฟอร์มอ่านความละเอียด FPS จำนวนเฟรม และความยาวคลิป
แล้วเติม Width/Height, FOV, มุมแนวนอน, ช่วงเก็บเฟรม, ชนิดภาพ และคุณภาพให้ พร้อมเหตุผลใต้แต่ละช่อง
High เป็นค่าตั้งต้น เปลี่ยน Preview/High/Ultra แล้วระบบคำนวณคำแนะนำใหม่
การตรวจชนิดภาพ 360 ใช้สัดส่วนใกล้ 2:1 ซึ่งแก้เองได้ถ้าไฟล์นั้นเป็นวิดีโอปกติ

ค่าจาก Model/Camera Pose ที่สำเร็จของไฟล์เดียวกันมีน้ำหนักมากกว่าประวัติการแตก Views อย่างเดียว
ใช้ช่วงเก็บเฟรมที่ประมวลผลจริง เช่น คลิป X4 5 FPS แนะนำทุก 1 เฟรม แม้ครั้งก่อนเคยกรอก 10
เมื่อไฟล์เปลี่ยน ระบบไม่ใช้ snapshot จากไฟล์เก่าเป็นคำแนะนำ

ช่องที่แก้เองจะเก็บค่าไว้ ใช้ปุ่ม **ใช้ค่าแนะนำ** ของแต่ละช่อง หรือ **ใช้ค่าที่แนะนำทั้งหมด** เพื่อแทนค่า
ฟอร์มแก้ไข Job เก็บค่าที่บันทึกไว้ก่อน และแสดงคำแนะนำให้เลือกใช้
ส่วนสรุปแสดงจำนวนภาพโดยประมาณ รวมมุมเงย/ก้ม และช่วงเก็บเฟรมที่ระบบจะใช้จริง
คำแนะนำนี้คำนวณจาก metadata และผลที่ผ่านมา ไม่ใช่การรับรองว่าคลิปมี parallax หรือ geometry ที่ถูกต้อง

### เปิดผลงานและย้อนกลับไปหาต้นฉบับ

- กดชื่อ Source เพื่อเปิดหน้ารวมวิดีโอต้นฉบับ ชุด Views/งาน และ Models ทุกรุ่นที่สร้างจากไฟล์นั้น
- หน้า Job มีลิงก์ Source, Gallery, Camera Preview และ Models ทั้งหมด แยกประวัติตามรหัสชุด Views ที่ใช้จริง พร้อม modal ปรับช่วง/สร้าง Views ใหม่ได้จากหน้านี้
- `/models?job=<job>&view_run=<view-run>` แสดงเฉพาะ Models จาก Views ชุดนั้น; `/models?source=<source-key>` แสดงผลงานจากต้นฉบับเดียวกัน
- 3D Viewer มีตัวเลือกสลับ Models จาก Views ชุดเดียวกัน พร้อมกลับไป Source/งาน และเปิดภาพที่ใช้เทรนรุ่นนั้น
- `/model-inputs/<model>.ply` เปิด Gallery ภาพเทรนของรุ่นนั้น แยกจาก Gallery ของ Views ปัจจุบัน
- รุ่นใหม่เก็บ dataset ไว้ใน `model_runs/<run>/dataset/` แม้เปลี่ยนค่าหรือสร้าง Views ใหม่ ส่วนรุ่นเก่าที่ไม่เก็บ dataset จะแจ้งว่าไม่มีภาพย้อนหลัง
- การเปลี่ยนชื่อ Source จะอัปเดตลิงก์ในประวัติ Models ด้วย

### ทดสอบหลายวิดีโอ 360 จริง

หน้า **ผลทดสอบวิดีโอจริง** (`/experiments`) แสดง Source → Views → Model, ผลสำเร็จ/ล้มเหลว,
ค่าที่แนะนำจาก profile, เวลาที่ใช้ และภาพ held-out render จริงจาก Brush บน GPU
พร้อมเปิดต้นฉบับและหน้าลากเปรียบเทียบภาพ ใช้ poses ที่ COLMAP หาใหม่จาก MP4 ไม่ได้นำเข้า poses/model จาก dataset

ดาวน์โหลดฉากใหม่และรันต่อได้ใน PowerShell:

```powershell
.\.venv-win\Scripts\python.exe scripts/download_omniphotos.py --scene Alley --scene Cathedral --scene Ship
.\.venv-win\Scripts\python.exe scripts/test_real_videos.py --base http://127.0.0.1:8001 --batch real360_20261008
```

ตัว runner ทำงานทีละคลิป รอ task อื่นก่อนใช้ GPU บันทึกผลลง `web_data/experiments/<batch>.json`
รันคำสั่งเดิมเพื่อทำต่อจากงานเดิม หรือเปลี่ยน `--batch` เพื่อทดสอบรอบใหม่
เลือกเฉพาะคลิปด้วย `--case alley` หรือเพิ่มรายการใน `data/raw/test_catalog.json`
ชุดแรกใช้ช่วง 0–4 วินาทีของ MP4 Insta360 One X ฉาก Alley/Cathedral/Ship ตามด้วยการทดสอบ Cathedral/Ship แบบเต็มคลิป,
และคลิป Corridor/Tram ที่เข้ารหัสจาก panorama จริง การสร้างไฟล์สำเร็จยังต้องเปิด 3D ตรวจ geometry/มุมใหม่
PSNR เป็นค่าจากบริเวณที่ไม่ถูก mask และภาพในรายงานเป็น checkpoint ก่อนกรอง PLY outlier

ผลจริงรอบ `real360_20261008`: 8 ครั้งจาก 5 วิดีโอ สร้าง Model ได้จาก 4 วิดีโอ
ใช้ AMD Radeon RX 6700 XT ผ่าน Vulkan, High 20,000 steps ในทุกรอบที่สำเร็จ

| วิดีโอ | ตำแหน่งที่หา pose ได้ | ผล | Masked held-out PSNR |
| --- | --- | --- | --- |
| Alley — 4 วินาทีแรก | 20/20 | สร้าง Model สำเร็จ | 32.14 dB |
| Cathedral — เต็มคลิป | 152/152 | สร้าง Model สำเร็จ; ช่วง 4 วินาทีแรกหา initial pair ไม่ได้ | 27.69 dB |
| Corridor — panorama จริงเข้ารหัสเป็น MP4 | 16/16 | สร้าง Model สำเร็จ | 34.42 dB |
| Tram — panorama จริงเข้ารหัสเป็น MP4 | 15/15 | สร้าง Model สำเร็จหลังแก้ JPEG decoder EOF | 30.83 dB |
| Ship — 4 วินาทีแรกและเต็มคลิป | ไม่มี reconstruction | ยังหา initial pair ไม่ได้ | — |

ตรวจภาพ GPU Render จริงเทียบต้นฉบับแล้ว อาคารและฉากนิ่งต่อเนื่องในมุมที่ตรวจ
แต่คนเคลื่อนไหว ขอบบางส่วน และพื้นบริเวณ mask ยังฟุ้ง ต้องตรวจ geometry/มุมใหม่ใน 3D Viewer ต่อ
จึงยังไม่รับรองว่าทุกวิดีโอจะสร้างภาพสมจริงได้ รายงานเก็บทั้งรอบที่สำเร็จและล้มเหลวไว้

ทดสอบ navigation/การกรอง/การเปลี่ยนชื่อ/ภาพย้อนหลังด้วย HTTP ในพื้นที่แยกของ tests
รวม 53 Python checks และชุดตรวจ UI JavaScript 4 ชุด Browser ของ session ไม่พร้อม จึงไม่มีภาพหน้าจอ UI ที่ยืนยันจากเบราว์เซอร์

หาก Brush แจ้ง `Failed to load format / early eof` จากตัวอ่าน JPEG ระบบแปลงภาพในสำเนา dataset
เป็น PNG ที่เก็บค่าพิกเซลหลัง decode เท่าเดิม แล้วลอง train ใหม่หนึ่งครั้ง
ไม่เปลี่ยนไฟล์ Source และบันทึกการกู้คืนไว้ในคุณภาพของ Model
ระหว่าง COLMAP sequential matching จะแสดงจำนวนเฟรมที่จับคู่แล้ว เช่น `70/152 panoramas`

### เลือกกล้องบางส่วนไป train

Camera Preview มีแถบลาก **ทิศทางภาพ** สำหรับเลื่อนมุมที่แตกไว้โดยไม่ต้องกด thumbnail ทีละภาพ
ใน **เลือกตำแหน่งไปสร้าง Model** เลือกช่วงกล้อง, 10 จุดต่อเนื่อง, เพิ่ม/ตัดจุดที่เลือก
หรือกด **ลากวงเลือกตำแหน่ง** แล้ววาด polygon รอบกลุ่มจุดกล้องในภาพรวม
สีส้มคือจุดที่จะ train สีเทาคือจุดที่ตัดออก

ระบบเก็บทุกทิศของกล้องที่เลือก และสร้าง dataset แยกใน `model_runs/<run>/dataset/`
โดยใช้ pose เดิม ไม่แก้ชุดข้อมูลต้นฉบับ ตรวจว่าลายเซ็นชุดภาพยังตรงกับ preview
ต้องเลือกอย่างน้อย 3 ตำแหน่งที่เคลื่อนกล้องจริง แนะนำทดลอง 10 ตำแหน่งต่อเนื่องที่เห็นฉากซ้อนกัน
สำหรับ spherical COLMAP กรอง sparse seeds ที่ถูกสังเกตจากอย่างน้อยสองตำแหน่งที่เลือก
ร่วมกับ reprojection error และ valid masks ป้องกันใช้จุดไกลที่ชุดย่อยไม่มีภาพรองรับ
หยุดเพิ่ม splats ที่ 75% ของ steps เพื่อเหลือช่วงท้ายสำหรับปรับรายละเอียดให้เข้าที่

เมื่อเสร็จ เปิด **ประวัติการสร้าง Model → ตรวจภาพ Render** เพื่อเทียบภาพต้นฉบับ ภาพทดสอบจาก GPU
และ mask พร้อมแถบลากเทียบภาพ PSNR คำนวณเฉพาะบริเวณที่ไม่ถูก mask
จุดน้อยลงไม่ได้แปลว่าภาพจะดีขึ้นเสมอ ต้องคัด pose ผิด ภาพเบลอ และฉากที่มีคนเคลื่อนที่ด้วย
บริเวณที่ไม่เคยมีภาพสะอาด หรือมุมที่ออกนอกช่วงกล้องที่เลือก อาจยังฟุ้งหรือขาดรายละเอียด

ผลทดลองจริง `temple_subset10_e3509737`: 10/59 ตำแหน่ง, 160 ภาพ, sparse seeds 32,209 → 14,495,
10,000 steps บน AMD RX 6700 XT/Vulkan, เวลารวม 146.6 วินาที, published splats 358,219
ภาพมุมทดสอบร่วมหนึ่งมุมได้ masked PSNR 25.15 → 30.32 dB เทียบ model เดิม
แต่ชุดเต็มและชุดย่อยใช้จำนวน steps ต่างกัน และยังพบภาพฟุ้งบริเวณคนเดิน/ฉากไกล
ภาพ GPU และผลตรวจอยู่ใน `model_runs/20261008_003509_addea272/quality_review.json`
ไม่ได้จับภาพหน้าจอ Three.js เนื่องจาก Browser ที่เชื่อมกับ session ไม่พร้อม

The included `data/raw/sample_360_preview.mp4` is an 8-second, 1920×960, 15 fps preview derived from a moving-camera Insta360 One X video. The original `data/raw/sample_360.mp4` (3840×1920, 50 fps) is excluded from Git. `test_360_tram.mp4` is also included, with geometry and dynamic-scene quality assessed separately from decoder failures. Attribution and license details are in `data/raw/SOURCES.md`.

Prototype สำหรับเตรียมไฟล์จากกล้อง 360 เช่น Insta360/DJI ที่ export แล้วเป็น
equirectangular MP4/JPG ให้เป็น perspective views สำหรับ COLMAP/3DGS

## ทดลองกับไฟล์ตัวอย่าง

ไฟล์ `data/raw/sample_360_preview.mp4` เป็นคลิปย่อจากวิดีโอ equirectangular 360 จริงจากชุดข้อมูล
OmniPhotos (กล้อง Insta360 One X, preview 1920×960 ที่ 15 fps) มีการเคลื่อนกล้องในฉาก ใช้ทดลองแยกเฟรมได้:

```bash
python3 pano_to_views.py \
  --input data/raw/sample_360_preview.mp4 \
  --output data/views/sample_360 \
  --width 640 --height 640 --fov 90 --views 8 --sample-every 15 --quality preview
```

ผลลัพธ์จะแตกแต่ละเฟรมพาโนรามาเป็นภาพ perspective 8 ทิศทางใน `data/views/sample_360/` และ `transforms.json`
สำหรับตรวจสอบ/ต่อเข้ากับ reconstruction pipeline

ถ้าเป็นวิดีโอที่ export จาก Insta360 Studio หรือ DJI ให้ใช้:

```bash
python3 pano_to_views.py \
  --input input.mp4 \
  --output data/views/shot \
  --sample-every 10 --views 8
```

## ตัวอย่างวิดีโอเดินถ่ายหลายตำแหน่ง

`data/raw/sample_walkthrough.webm` เป็นคลิปเดินผ่านห้องใต้ดินจริงจาก Wikimedia Commons
(CC0; รายละเอียดและที่มาอยู่ใน `data/raw/SOURCES.md`) ใช้เทียบกับวิดีโอเดินถ่ายทั่วไป
ซึ่งไม่ใช่ภาพพาโนรามา 360 ไฟล์นี้ไม่ได้รวมใน Git ให้ดาวน์โหลดจากลิงก์ใน `SOURCES.md` ก่อนรัน:

```bash
python3 pano_to_views.py --input data/raw/sample_walkthrough.webm \
  --output data/views/sample_walkthrough --sample-every 60
```

สำหรับคลิปเดินถ่าย converter จะเก็บหนึ่งภาพต่อเฟรมที่สุ่มได้ ทำให้แต่ละภาพมาจากตำแหน่งกล้องคนละจุด
ส่วนไฟล์พาโนรามา 2:1 จะยังถูกแตกเป็นหลายทิศทางต่อเฟรมเหมือนเดิม เลือกพฤติกรรมเองได้ด้วย
`--projection walkthrough` หรือ `--projection panorama` (ค่าเริ่มต้น `auto` ตรวจจากอัตราส่วนภาพ)

ไฟล์ `.insv` ที่ยังไม่ stitch ควร export จาก Insta360 Studio เป็น equirectangular
MP4 ก่อน ส่วน DJI MP4 ที่เป็น equirectangular ใช้ได้โดยตรง

## สิ่งที่ prototype ทำ

1. อ่านภาพ 360/equirectangular หรือดึงเฟรมจาก MP4 ด้วย OpenCV
2. แปลง panorama เป็น perspective views หรือสุ่มเฟรมจากวิดีโอเดินถ่ายตามตำแหน่งกล้อง
3. ใช้ COLMAP แบบ `EQUIRECTANGULAR` หา camera pose หนึ่งตำแหน่งต่อเฟรม 360
4. นำ pose เดียวกันไปใช้กับ perspective views ทุกมุมของเฟรมนั้น
5. มาสก์คน/ผู้ถือกล้อง แล้ว train Gaussian Splatting ด้วย Brush
6. ตรวจคุณภาพและเผยแพร่ไฟล์ `.ply` สำหรับเปิดใน Three.js viewer

## ข้อจำกัดสำคัญ

ภาพ 360 เพียงภาพเดียวมีข้อมูลจาก camera center เดียว จึงยังสร้าง geometry 3D
ที่ถูกต้องไม่ได้ การ train Gaussian Splatting ต้องใช้หลายเฟรมขณะที่กล้องเคลื่อนที่
รอบฉาก และต้องหา camera poses ด้วย COLMAP/SLAM ก่อน `transforms.json` ที่สร้างจาก
ภาพเดียวจึงเป็น output สำหรับตรวจ projection เท่านั้น ไม่ใช่ reconstruction ที่สมบูรณ์

วิดีโอควร stitch และ export จาก Insta360 Studio หรือ DJI Studio เป็น equirectangular
2:1 ก่อนนำเข้า กล้องต้องเคลื่อนที่รอบสถานที่อย่างต่อเนื่อง ฉากควรอยู่นิ่ง มีรายละเอียด
และแสงคงที่ การหมุนกล้องอยู่กับที่ให้ภาพ 360 ได้ แต่ไม่มี parallax สำหรับสร้างความลึก 3D

แหล่งภาพพาโนรามาทดสอบ: Wikimedia Commons, “In times like this... (360° equirectangular)”

## ตรวจและแก้ภาพซีด / ฟุ้ง / เส้นยาว — 08/10/2026

ตรวจจากภาพที่ผู้ใช้ส่งมา, shader ของ Brush/Three.js และภาพทดสอบจริงบน RX 6700 XT:

- Viewer เริ่มต้นเปลี่ยนเป็น Spark 2.3.1 / Three.js 0.180.0 จากไฟล์ในเครื่อง ตั้งสีเป็น sRGB
  และใช้ covariance blur 0.3 โดยไม่ลด opacity เพื่อให้ตรงกับ Brush 0.3.0
  Three.js Native 0.186.1 ยังเลือกเปรียบเทียบได้ที่ `?renderer=native`
- การลากเริ่มต้นหันมอง ณ จุดถ่ายเดิม แถบตำแหน่งและปุ่มก่อนหน้า/ถัดไปกลับไปยัง camera pose
  ที่ใช้เทรนจริง โหมด Orbit ยังเลือกได้ แต่การออกห่างจากเส้นทางถ่ายอาจเห็นส่วนที่ไม่มีข้อมูล
- ยกเลิกการตัด splat ตามอัตราส่วนความยาวหรือระยะห่างแบบเหมารวม การทดสอบ PLY ของ Tram
  พบว่าตัวกรองเดิมทำให้ PSNR ลดจาก **30.831 → 28.102 dB** หลังแก้ export ได้ **30.831 dB**
  เท่ากับ checkpoint เดิม ไม่ได้ train Tram เพิ่มในการทดลองนี้
- เพิ่มปุ่ม **ปรับละเอียดต่อ** ในหน้า Model: ใช้ checkpoint และภาพ/poses ของรุ่นนั้น เทรนเพิ่ม
  ด้วย learning rate ของตำแหน่งที่ต่ำลง ได้ชื่อ Model ใหม่และเก็บรุ่นก่อนสำหรับเปรียบเทียบ
  เป็นการนำ weights กลับมาใช้ โดยเริ่ม optimizer และ schedule ใหม่

ผล fine tune วัดจากภาพทดสอบชุดเดียวกันบน GPU ก่อนขั้น export:

| ฉาก | Steps รวม | PSNR ก่อน → หลัง | SSIM ก่อน → หลัง |
| --- | ---: | ---: | ---: |
| Alley | 20,000 → 30,000 | 32.141 → 32.543 dB | 0.9399 → 0.9435 |
| Cathedral | 20,000 → 40,000 | 27.690 → 28.059 dB | 0.8653 → 0.8728 |

คะแนนคำนวณเฉพาะส่วนที่ไม่ได้ถูก mask ไม่รับรอง geometry ทั้งฉากหรือรายละเอียดจากมุมใหม่
ส่วนที่ถูกคน/ผู้ถือกล้องบังตลอดหรือไม่เคยถ่ายยังอาจฟุ้ง ภาพ Tram ใช้ input เทรน 192×192
จึงมีข้อจำกัดเมื่อขยายเต็มหน้าจอ ไม่สามารถเพิ่มรายละเอียดจริงด้วยการเปลี่ยน Renderer

รุ่นที่แก้ export แล้ว (ใช้พอร์ตที่ `app_2_run.ps1` แสดง):

- [Tram](http://127.0.0.1:8000/model/49611799da_20261008_080306_1cd746c5.ply)
- [Temple](http://127.0.0.1:8000/model/a147ed6bb2_20261008_080307_0e43c38b.ply)
- [Alley หลัง fine tune](http://127.0.0.1:8000/model/21aba53d99_20261008_080309_101bd289.ply)
- [Cathedral หลัง fine tune](http://127.0.0.1:8000/model/7bd9e99424_20261008_080311_19b495f0.ply)
- [Corridor](http://127.0.0.1:8000/model/56d66207d8_20261008_080322_931280ba.ply)

ภาพเปรียบเทียบ input / GPU render ก่อน / หลังและผลวัดอยู่ใน:

- `web_data/jobs/21aba53d99/model_runs/20261008_073701_fddeb836/quality_review.json`
- `web_data/jobs/7bd9e99424/model_runs/20261008_074254_14136ff2/quality_review.json`
- `web_data/quality_diagnostics/published_*/result.json`

ทดสอบ PLY ที่เผยแพร่ด้วย `scripts/check_published_model.py`: ใช้ GPU หนึ่ง step โดยตั้ง
learning rate ทุกตัวเป็น 1e-20 ปิด noise และ growth แล้วตรวจการเปลี่ยนแปลงของพารามิเตอร์
ภาพเหล่านี้เป็น Brush render ไม่ใช่ภาพหน้าจอ Browser ใน session นี้ไม่มี Browser ให้ควบคุม
จึงยังต้องยืนยันภาพ Spark จาก Browser ของผู้ใช้จริง

```powershell
.\.venv-win\Scripts\python.exe -X utf8 scripts/install_viewer_assets.py
.\.venv-win\Scripts\python.exe -X utf8 scripts/finetune_models.py --model <model.ply>
.\.venv-win\Scripts\python.exe -X utf8 scripts/repair_model_export.py --model <model.ply>
.\.venv-win\Scripts\python.exe -X utf8 scripts/check_published_model.py --model <model.ply>
```

คำสั่งทดลอง GPU ต้องรอการเทรนปัจจุบันเสร็จก่อน ส่วนคำสั่ง repair เก็บรุ่นเดิมไว้และตรวจ
ชุด input ของโมเดลเก่าก่อนสร้างสำเนา หาก input เปลี่ยนไปแล้วจะปฏิเสธการกู้จากชุดปัจจุบัน

อ้างอิงรูปแบบการ render: [Brush 0.3 project shader](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-render/src/shaders/project_visible.wgsl),
[Three.js r186 GaussianSplat](https://github.com/mrdoob/three.js/blob/r186/examples/jsm/objects/GaussianSplat.js),
[ตัวอย่างสี sRGB ของ Three.js](https://github.com/mrdoob/three.js/blob/r186/examples/webgpu_gaussian_splat.html),
[SparkRenderer](https://sparkjs.dev/docs/spark-renderer/)

## License

[MIT License](LICENSE) — Copyright (c) 2026 Thanakiat Homhual.

## GPU สำหรับ Views และ masks

```powershell
.\.venv-win\Scripts\python.exe -m pip install -r requirements-gpu.txt
.\.venv-win\Scripts\python.exe web_app.py --view-device auto --mask-device auto
```

Ubuntu: `app_1_install.sh` ติดตั้ง WGPU ให้แล้ว ใช้ `bash app_2_run.sh --view-device auto --mask-device auto`.
Views panorama ใช้ WGPU/Vulkan หรือ D3D12 บน Windows รองรับ AMD/NVIDIA/Intel โดยไม่ขึ้นกับ Torch CUDA.
Masks ใช้โมเดล LRASPP/weights เดิม: CUDA → DirectML (Windows) → CPU. Ubuntu AMD ใช้ WGPU สำหรับ Views และ CPU สำหรับ masks เมื่อไม่มี Torch CUDA.
Workers แยกจาก web server; auto ลอง backend ทางเลือกก่อน CPU เมื่อ GPU ใช้ไม่ได้ และบันทึกเหตุผลไว้ใน status/config/log.
กำหนด `--view-device cpu|wgpu` และ `--mask-device cpu|cuda[:N]|directml[:N]` ได้ การระบุ GPU โดยตรงจะแสดง error เมื่อใช้ไม่ได้.
`VIEW_DEVICE` และ `MASK_DEVICE` เป็น environment variables ที่เทียบกับตัวเลือกเหล่านี้.
COLMAP CUDA ใน auto retry บน CPU เมื่อพบข้อผิดพลาด GPU; binary ที่ไม่มี GPU support ยังคงใช้ CPU.

ตรวจ GPU ที่ใช้งานจริงด้วย `scripts/check_runtime.py` และทดสอบภาพ/เวลาแบบแยกจาก jobs เดิม:

```powershell
.\.venv-win\Scripts\python.exe scripts/check_gpu_pipeline.py --source web_data/uploads/your-360.mp4 --output web_data/capture_review/gpu-report.json --resolution 1024
```

ผล benchmark รายงาน Views รวม decode, projection, readback, JPEG, disk และ worker startup; masks รายงาน inference หลัง warmup.
ความเร็วขึ้นกับขนาดภาพ ช่วงคลิป การ์ด และ I/O ไม่ใช้เปอร์เซ็นต์ GPU load เป็นหลักฐานว่าทั้งงานเร็วขึ้น.

## Tutorial ในแอป

เปิด `/tutorial` หรือเมนู **Tutorial · คู่มือ** เพื่อเรียน Record → Upload → Profile → Views → Camera Preview/Train → Model.
มีเส้นทางจำลองห้องเดียว ทางเดิน หลายห้อง และการวนซ้ำ พร้อมตัวอย่างภาพ DJI และตำแหน่งกล้องจริงที่เก็บเป็น assets ถาวร.
ตัวอย่างเส้นทางจาก COLMAP เป็นหน่วยสัมพัทธ์และไม่ใช่ screenshot หรือ model surface.

## เก็บขอบโมเดลและส่งออกพร้อมเปิด

หน้า **Model → ตัดขอบห้อง** มีกรอบห้อง, **แปรงลบ** และ **ลากวงเลือก** สำหรับเก็บจุดฟุ้ง.
เลือกเป็นสีแดงก่อนกดยืนยันลบ มี Undo/Redo ทั้งการเลือกและการลบ แล้วบันทึกเป็นรุ่นใหม่.
ค่าเริ่มต้นเลือกชั้นด้านหน้าโดยประมาณ; ปิดได้เพื่อเลือกทะลุ แต่ควรตรวจสีแดงก่อนลบ.
อ่าน [วิธีใช้ Editor](MODEL_CROP.md).

ในหน้าส่งออก GLB กด **เตรียม ZIP พร้อมเปิด** เพื่อดาวน์โหลดโมเดลพร้อมตัวอย่าง Three.js,
ไฟล์แสดงผลในเครื่อง, คู่มือและตัวเปิด Windows/Ubuntu. แตก ZIP แล้วเปิด START_WINDOWS.cmd
หรือรัน `bash start_ubuntu.sh`. ต้องมี Python 3.10+ และ browser/GPU ที่รองรับ;
ไม่ต้องเชื่อมอินเทอร์เน็ตขณะดูโมเดล. อ่าน [คู่มือส่งออก](GLB_EXPORT.md).
