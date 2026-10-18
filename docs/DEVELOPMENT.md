# โครงสร้างสำหรับพัฒนา

ติดตั้งและเปิดแอปจากโฟลเดอร์ repository ด้วยสคริปต์เดิม 2 ตัวต่อ OS ตาม [README](../README.md)
คำสั่งตัวอย่างในคู่มือให้รันจากโฟลเดอร์ repository แม้ไฟล์คู่มือจะอยู่ใน `docs/`

```text
app/
  paths.py                พาธ repository, assets, templates และเอกสาร
  pipeline/               Views, datasets, camera poses, quality, training recovery
  runtime/                CPU/GPU backends, workers และ environment verification
  jobs/                   คิว, Stop, process supervision, progress, log และชื่อ resources
  models/                 Crop/selection, PLY/SPZ/GLB, package และ quality metrics
  web/                    HTTP server และตัวสร้าง UI
    templates/            HTML ของ viewer, preview และ quality review
assets/
  app/                    JavaScript/CSS ของแอป
  vendor/                 Three.js / Spark ที่ติดตั้งไว้
  tutorial/               ภาพประกอบคู่มือ
docs/
  README.md               คู่มือฟีเจอร์ฉบับเต็ม
  guides/                 คู่มือใช้งานและพัฒนาแต่ละเรื่อง
  plans/                  แผนที่ยังไม่ได้ implementation รวม Desktop
  reports/                ผลวัด GPU และ performance
  reviews/                บันทึกตรวจระบบ
scripts/                  เครื่องมือ install, ตรวจระบบ, benchmark และทดลอง
tests/                    Python regression และ JavaScript UI checks
examples/                 ตัวอย่าง Three.js และ portable viewer
data/, web_data/, tools/  ข้อมูลผู้ใช้ ผลประมวลผล และ dependencies ในเครื่อง
```

## วิธี import และรัน

โค้ดใหม่ import ผ่านชื่อ package เช่น:

```python
from app.web import server
from app.pipeline import training_recovery
from app.runtime.cpu_runtime import cpu_budget
from app.paths import ROOT
```

`web_app.py` และ `pano_to_views.py` ที่ root เป็น compatibility entry points สั้น ๆ
ทั้งคำสั่ง CLI เดิมและ `import web_app` / `import pano_to_views` ยังใช้ได้
และอ้างถึง module เดียวกับ package เพื่อไม่แยกสถานะคิวหรือ registry เป็นสองชุด

```bash
python web_app.py --help
python pano_to_views.py --help
python -m app.pipeline.spherical_to_brush --help
```

Worker เรียกด้วย `python -m app.runtime...` หรือ `app.jobs.task_control`
กำหนด working directory และ import paths ให้ชัดเจน จึงไม่พึ่งการอยู่ในโฟลเดอร์ module
อย่าเรียกไฟล์ worker ภายในด้วยพาธตรง และอย่าเพิ่ม `sys.path` เพื่อ import ชื่อ module เก่า

## การแก้หน้าเว็บ

- JavaScript/CSS อยู่ใน `assets/app/`; HTML อยู่ใน `app/web/templates/`
- URL สาธารณะยังเป็น `/assets/model_viewer.mjs`, `/assets/editor.css` ฯลฯ
  การย้ายไฟล์บนดิสก์ไม่เปลี่ยน URL ของ Viewer หรือ API
- เพิ่มชื่อ asset ใหม่ใน `app/web/static_assets.py` เพื่ออนุญาตให้ HTTP ส่งไฟล์นั้น
  ไม่เปิดทั้งโฟลเดอร์ source เป็น static directory
- CSS/JavaScript หลักของ workspace แยกเป็น `workspace.css` / `workspace.js`
  server ยังอ่านไปฝังใน HTML ตามเดิม เพื่อคงลำดับเริ่มทำงานและพฤติกรรมเดิม
  สองไฟล์นี้อ่านเมื่อ backend เริ่ม จึงเปิด backend ใหม่หลังแก้ในช่วงที่ไม่มี job ทำงาน

## ข้อมูลและการติดตั้ง

`app.paths.ROOT` เป็นโฟลเดอร์ repository ไม่ใช่โฟลเดอร์ของ Python module
รอบจัดโครงสร้างนี้ไม่ได้ย้าย Sources, Jobs, Models, checkpoints, venv หรือ engines
สคริปต์ `app_1_install.*` และ `app_2_run.*` ยังอยู่ที่ root เหมือนเดิม

Electron ยังเป็น [แผนแยก](plans/DESKTOP_PLAN.md)
สามารถเพิ่ม `desktop/` และขั้น build ภายหลัง โดยใช้ backend package นี้ร่วมกับเว็บเดิม

## ทดสอบ

Windows ใช้ Python ของ `.venv-win`; Ubuntu ใช้ Python ของ `.venv`:

```powershell
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv-win\Scripts\python.exe scripts/install/smoke_app.py
node tests/editor_ui.mjs
```

ทดสอบ JavaScript ทั้งหมดบน PowerShell:

```powershell
Get-ChildItem tests/*.mjs | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { throw "UI check failed: $_" } }
```

`test_project_layout.py` ตรวจพาธส่วนกลาง, asset allowlist, compatibility CLI และ CPU worker
จาก working directory อื่น ส่วน regression เดิมยังตรวจข้อมูล/กล้อง, recovery, คิว,
install scripts, upload, export และ HTTP โดยใช้พื้นที่ทดสอบแยก
การรันชุดทดสอบทั่วไปไม่เริ่ม GPU Train ของผู้ใช้

ผลตรวจหลังย้ายโครงสร้าง: [FOLDER_CLEANUP.md](reviews/FOLDER_CLEANUP.md)
