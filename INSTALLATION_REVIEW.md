# Installation review — 2026-10-17

จุดเริ่มต้นมีเพียง `app_1_install` และ `app_2_run` ต่อ OS ตาม [README.md](README.md)
สคริปต์ช่วยย้ายเข้า `scripts/install/`; อัปเดตคำสั่งในคู่มือและข้อความแจ้ง dependency ที่หายแล้ว

สิ่งที่ตรวจผ่าน:

- Python regression suite: **250 tests**, skipped 1 GPU opt-in test
- Installer tests 8 ชุด: guard งานที่ยังรัน, CPU/CUDA selection, constraints และ failure propagation,
  checksum/path safety, Bash syntax/plan, Windows install จำลองจากโฟลเดอร์ใหม่ที่มีช่องว่าง,
  รันซ้ำ, Check, Run และป้องกันการดำเนินขั้นถัดไปเมื่อ Python install ล้มเหลว
- Windows installer `-Check` บน environment จริง: packages/imports, pip check, FFmpeg/ffprobe,
  Node, COLMAP, Brush, GPU inventory, local asset checksums และ cached mask weights ผ่าน
- Cold dependency resolution แบบ `--dry-run --ignore-installed --only-binary=:all:`
  บน Windows Python 3.12: **235 packages** resolve ผ่านกับ NumPy 1.26.4,
  OpenCV headless 4.10.0.84, PyCOLMAP 4.2.1, Nerfstudio 1.1.5 และ Torch/torchvision CPU 2.7.1/0.22.1
- venv ใหม่แยก: ติดตั้ง CPU image libraries ตามเวอร์ชันใหม่จริง สร้าง panorama Views 4 ภาพ
  ผ่าน CPU worker 2 threads และเปิด HTTP Dashboard / Sources / Jobs / Models / assets สำเร็จ
  SHA ของ source ไม่เปลี่ยน ไม่ใช้ GPU หรือรัน COLMAP/Train ในการทดสอบนี้
- PowerShell parse และ Bash syntax/`--plan` ผ่าน สคริปต์ root เหลือคู่ install/run ตาม OS

ข้อจำกัดของหลักฐาน:

- Windows full installer ทดสอบ orchestration ด้วย package manager/tool fixtures;
  ไม่ได้ reinstall libraries หรืออัปเกรด system tools ของ environment ที่กำลังสร้าง Model
- Cold resolution ตรวจ dependency graph และ wheel availability ไม่ใช่การติดตั้งครบทั้ง 235 packages
  ลงเครื่อง Windows ว่างอีกเครื่อง การติดตั้งจริงใน venv ใหม่รอบนี้ครอบคลุม CPU image core
- ไม่ได้ติดตั้ง apt/build COLMAP บน Ubuntu จริง และไม่มี NVIDIA RTX สำหรับทดสอบ CUDA
- Verify ตรวจ driver/adapter readiness และ app startup; ไม่ยืนยัน GPU training หรือ VRAM
  ต้องดู backend และผล pipeline จริงบนเครื่องปลายทาง

หลักฐานใน workspace (ไม่เก็บ artifacts ใน Git):
`web_data/capture_review/install_regression_20261017.log`,
`install_script_tests_20261017.log`, `install_verify_existing_20261017.log`,
`install_resolver_20261017/windows.json` และ `install_cpu_clean_20261017.json`

เซิร์ฟเวอร์เดิมพอร์ต 8000 และ job `d36645f4d5` ไม่ถูกหยุดหรือ restart
