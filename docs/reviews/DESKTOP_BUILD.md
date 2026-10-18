# ผลตรวจ Desktop · 18 ตุลาคม 2026

Windows 11 x64 / AMD Radeon RX 6700 XT, Python 3.12.10, Electron 44.9.0,
electron-builder 26.15.3, electron-updater 6.8.9, PyInstaller 6.22.0

## สิ่งที่ตรวจจริง

- Build Python onedir runtime แยก environment และ Windows per-user NSIS installer
- ติดตั้ง 0.1.0 จริง แล้วทดสอบ patch ไป 0.1.1 พร้อมเทียบ SHA256 ข้อมูลใน QA workspace
- เปิด Electron ทั้ง source mode และโปรแกรมที่ติดตั้งจริง ตรวจ sandbox,
  contextIsolation, nodeIntegration ปิด และคำขอที่ไม่มี session ถูกปฏิเสธ
- Verify ผ่านด้วย frozen runtime ตรวจ bundled FFmpeg/COLMAP/Brush; รูปภาพจำลอง
  upload ผ่าน UI → สร้าง 4 Views ด้วย frozen CPU worker → upload PLY → Spark canvas
- หน้า Dashboard, Sources, Jobs, Models, Tutorial และ Project overview ตอบสำเร็จ
  ไม่มี renderer JavaScript error ในเส้นทางที่ทดสอบ ตรวจภาพ Dashboard จาก Electron จริง
- ทดสอบ Mask inference จริงด้วย CPU และ DirectML พร้อม weights ใน bundle
  แก้ collection ของ torchvision native ops (`_C_stable.pyd` และ DLLs) ที่ standard hook ยังไม่เก็บ
  และทดสอบ PLY → SPZ → GLB 128 splats ด้วย bundled Node โดย PATH มีเฉพาะ System32
- สร้างวิดีโอจำลอง 256×128 / 5 FPS / 1 วินาทีด้วย bundled FFmpeg แล้วอ่าน metadata
  และเฟรมพรีวิวด้วย frozen OpenCV worker ได้ครบ
- มี live job lock แล้ว prepare-quit ตอบ 409; ปิด UI ไม่ทำลาย backend
  ตัวติดตั้ง patch ปฏิเสธเมื่อ backend ยังทำงาน ไม่ใช้ force kill
- Python regression 298 tests, skipped 1 opt-in GPU test; JavaScript UI 19 ชุด
  และ Desktop policy 2 tests ผ่าน
- Frozen supervisor และ Brush ที่บรรจุในตัวติดตั้งผ่าน GPU Train / Stop / Continue จริง
  บน RX 6700 XT/Vulkan: 16 train / 3 held-out views, 1,000 steps, หยุด checkpoint 250
  PSNR baseline 27.481 → resume 27.634 dB, ต่าง +0.153 dB; source model SHA256 เดิม
  ยังใช้ weights/iteration ต่อโดยเริ่ม optimizer/RNG ใหม่ ไม่ใช่ exact resume
- เว็บเดิมพอร์ต 8000 ยังตอบ HTTP 200 โดยไม่ restart หรือเปลี่ยน environment เดิม
- เปิดโปรแกรมที่ติดตั้งตามปกติถึงหน้าต่าง Dashboard / Verify ready พร้อม workspace
  `Documents/360 Studio Workspace` แยกจาก repository

## หลักฐานในเครื่อง

- `web_data/capture_review/desktop_20261018/python.log`, `python-result.json`, `javascript.json`
- `tools/desktop-qa/electron-development/result.json`
- `tools/desktop-qa/electron-installed/result.json`, `dashboard.png`
- `tools/desktop-qa/install-upgrade.json` — ผลตัวติดตั้ง/จำนวนไฟล์ที่เก็บไว้เหมือนเดิม
  และ `install-upgrade-0.1.0-0.1.1.json` — ผลรอบอัปเกรดจากรุ่นแรก
- `tools/desktop-qa/frozen-engines-cpu.json`, `frozen-engines-directml.json` และ log ในโฟลเดอร์ผล
- `tools/desktop-qa/final-launch.json` — health/Verify และหน้าต่างโปรแกรมที่เปิดไว้
- `web_data/capture_review/desktop_frozen_gpu_20261018/result.json` และ log ของ probe
- `web_data/desktop-backend-build.log`, `web_data/desktop-shell-final.log`
- ตัวติดตั้ง `desktop/dist/360 Studio Setup 0.1.1.exe` 692,364,242 bytes (660.29 MiB)
  พร้อมไฟล์ `.sha256`; SHA256 `98bb1921880ac8d1364d03576620d916de097c2c15cafc841265cb28945a5ccf`

Artifacts และข้อมูลทดสอบเหล่านี้อยู่ในเครื่องและถูก ignore ไม่รวม Git หรือข้อมูลผู้ใช้ใน installer
ดูคำสั่ง build/test และตำแหน่งข้อมูลใน [desktop/README.md](../../desktop/README.md)

## ขอบเขตที่ยังต้องทดสอบ

ยังไม่มี clean Windows VM: การทดสอบ packaged app ลด PATH เหลือ System32
และใช้ bundled runtime ได้บนเครื่องนี้ แต่ไม่ได้ยืนยัน prerequisites/driver ทุกเครื่อง
ชุดนี้บรรจุ CPU COLMAP และ CPU Torch + DirectML; ยังไม่ได้ทดสอบ RTX/CUDA build หรือ Ubuntu
Brush ต้องใช้ GPU ที่รองรับ ไม่ได้เพิ่ม CPU training fallback

Installer ยัง unsigned และไม่มี production release feed; online updater เป็นโค้ดเตรียมไว้
ที่ยังไม่ได้ทดสอบ download/install ผ่าน feed จริง การ rollback, schema migration,
เครื่อง disk full, ชื่อ workspace ภาษาไทย และการปิดเครื่องระหว่าง update ยังเป็นงานต่อ
GPU probe ใช้ชุดภาพเล็ก ไม่ได้ทดสอบ reconstruct วิดีโอ 8K เต็มคลิปซ้ำทั้งหมดใน Desktop
