# 360 Studio Desktop · Windows

Electron เปิด UI เดิมและ backend ส่วนตัวบน `127.0.0.1` พอร์ตว่าง ไม่ใช้พอร์ต 8000
และไม่ปิดเว็บที่เปิดจาก `app_2_run.*` ตัวติดตั้งบรรจุ Python runtime, FFmpeg,
COLMAP, Brush และ Node สำหรับงานส่งออกแล้ว ผู้ใช้ปลายทางไม่ต้องติดตั้ง Python/Node เอง
ยังต้องมี graphics driver ที่รองรับ และ Brush Train ต้องใช้ GPU

## ติดตั้งและใช้งาน

ใช้ `dist/360 Studio Setup 0.1.1.exe` แล้วเปิด **360 Studio** จาก Start menu
โปรแกรมตรวจเครื่องด้วย Verify ก่อนแสดง Dashboard ตรวจได้อีกครั้งจาก Help → ตรวจเครื่อง
รุ่นนี้เป็น Windows x64 รุ่นทดสอบที่ยังไม่ได้ลงลายเซ็น

Workspace เริ่มต้นคือ `Documents/360 Studio Workspace` เปลี่ยนได้จากเมนู Workspace
ข้อมูลอยู่ใน `web_data/` และ `data/raw/` ภายใน Workspace แยกจากไฟล์ติดตั้ง
การติดตั้ง patch ทับเก็บ Sources, Jobs, Models และ checkpoints เดิมไว้
อย่าเลือก workspace ของเว็บที่กำลังประมวลผลอยู่พร้อมกัน: การล็อก Desktop ไม่ได้ย้าย
หรือควบคุม backend ของเว็บเดิม และคิว GPU แยกตาม workspace

เมื่อปิดหน้าต่างระหว่างทำงาน เลือก **ทำงานต่อเบื้องหลัง** เพื่อเก็บไว้ที่ system tray
เปิดกลับโดยดับเบิลคลิกไอคอน เมื่อไม่มีงานจึงออกได้อย่างปลอดภัย โปรแกรมไม่ kill worker
ถ้า UI ล้มและ backend ยังทำงานอยู่ การเปิดใหม่ใช้ backend ของ Desktop instance เดิม
Log ของ launcher/backend เปิดได้จาก Help → เปิด Log; log แต่ละงานยังอยู่ในหน้า Job

## พัฒนาควบคู่กับเว็บ

ตั้งค่า environment เดิมด้วย `app_1_install.ps1` ก่อน จาก repository root:

```powershell
npm --prefix desktop ci
npm --prefix desktop start
```

`npm start` ใช้ Python ของ `.venv-win` และ source ใน `app/` โดยใช้ Desktop workspace
ส่วน `app_2_run.ps1` และ `app_2_run.sh` ยังใช้เว็บโหมดเดิม การแก้ source ต้องเปิด Desktop
ใหม่เพื่อโหลด Python/JavaScript ใหม่; โปรแกรมที่ติดตั้งแล้วใช้ runtime ที่ build ไว้
จึงไม่เปลี่ยนตาม source จนกว่าจะ build/install รุ่นใหม่

## Build

ใช้ Windows x64, Node/npm และ environment ที่รันแอปเดิมได้ มี engines และ mask weights แล้ว:

```powershell
npm --prefix desktop ci
npm --prefix desktop run build
```

Build backend ใช้ PyInstaller ใน `tools/desktop-build-env` แยกจาก environment ของเว็บ
อ่าน dependencies จาก environment ที่ติดตั้งไว้ ไม่ pip เปลี่ยน environment ของแอปเดิม
`desktop/build/backend` และ `desktop/dist` ไม่เข้า Git
build script บรรจุ engine/weights จากเครื่องที่ build พร้อม manifest SHA256
ไม่มีวิดีโอ, jobs หรือ models ของผู้ใช้ติดไปด้วย

หากแก้เฉพาะ Electron และ backend ที่ build ไว้ยังตรงกับ source:

```powershell
npm --prefix desktop run build:shell
```

ชุดที่ทดสอบบน AMD บรรจุ COLMAP **CPU** และ Torch CPU + DirectML
การมี RTX อย่างเดียวไม่ทำให้ binary นี้กลายเป็น COLMAP CUDA ต้องเตรียม CUDA-enabled
COLMAP/Torch ใน build flavor ที่ต้องการแล้ว Verify/ทดสอบบน RTX จริง
Ubuntu installer ยังไม่ได้ build/test; ใช้ `app_1_install.sh` / `app_2_run.sh` ต่อไป

## ทดสอบ

```powershell
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
npm --prefix desktop test
node desktop/scripts/smoke.cjs
node desktop/scripts/smoke.cjs --exe "$env:LOCALAPPDATA\Programs\360 Studio\360 Studio.exe" --installed --exercise
.\.venv-win\Scripts\python.exe -X utf8 desktop/scripts/frozen-smoke.py
.\.venv-win\Scripts\python.exe -X utf8 desktop/scripts/frozen-smoke.py --mask-device directml
```

Installed smoke ใช้ profile/workspace ใน `tools/desktop-qa` ลด PATH เหลือ System32
แล้วตรวจ Verify, หน้าเว็บ, upload จริง, CPU Views 4 ภาพ, Spark viewer และการปิดขณะมีงาน
Frozen smoke ตรวจ Mask inference, FFmpeg/วิดีโอพรีวิว และ PLY → SPZ → GLB ด้วย runtime/Node ที่ติดตั้งจริง
ตัวเลือก `directml` ต้องใช้ Windows ที่มี GPU และ driver รองรับ
`desktop/scripts/install-smoke.cjs` เป็นการ **ติดตั้งจริง** จาก artifact ตาม version ใน package.json
ทดสอบปฏิเสธติดตั้งขณะ backend ทำงาน แล้วติดตั้งทับและเทียบ SHA256 ข้อมูล QA เดิม
จึงใช้เมื่อพร้อมเปลี่ยนเวอร์ชัน Desktop ที่ติดตั้งในบัญชีนี้แล้วเท่านั้น

ผลทดสอบรอบแรก: [DESKTOP_BUILD.md](../docs/reviews/DESKTOP_BUILD.md)

## Patch / update

ตอนนี้ติดตั้งไฟล์ Setup รุ่นใหม่ทับได้เมื่อปิด Desktop และไม่มี backend ค้างทำงาน
ตัวติดตั้งตรวจ process และปฏิเสธแทนการบังคับปิด ข้อมูลอยู่ข้างนอกโฟลเดอร์ติดตั้ง

มี updater แบบผู้ใช้กดตรวจ/ดาวน์โหลด/ติดตั้งเอง เปิดใช้เมื่อกำหนด `STUDIO_UPDATE_URL`
เป็น HTTPS feed สำหรับ electron-updater โดยรุ่นปกติไม่ตั้ง feed และไม่ตรวจเครือข่ายเอง
`STUDIO_UPDATE_TEST=1` อนุญาต HTTP เฉพาะ `127.0.0.1` สำหรับ QA
ก่อนติดตั้งต้องไม่มี upload, คิว หรืองานใน backend ของ Desktop และหยุด backend อย่างปลอดภัย

ก่อนเผยแพร่จริงยังต้องตั้ง release feed, code signing, ตรวจ notices/licenses ของ engine,
ทดสอบ online update บนเครื่องสะอาด, download ขาดช่วง และ rollback/migration
รอบนี้ยืนยัน manual patch 0.1.0 → 0.1.1; ยังไม่ได้รับรอง online update หรือ rollback อัตโนมัติ
