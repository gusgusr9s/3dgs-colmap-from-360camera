# 360 Studio

แปลงภาพ/วิดีโอ 360 ที่ stitch แล้วเป็น Views → Camera Poses → Gaussian Splat Model
พร้อมตรวจคุณภาพ แก้ส่วนเกิน และส่งออก PLY / SPZ / GLB สำหรับ Three.js

หลัง clone ใช้ **2 สคริปต์ต่อ OS** ชื่อเรียงติดกันในโฟลเดอร์หลัก:

| OS | 1. ติดตั้ง prerequisites + Verify | 2. เปิดแอป |
|---|---|---|
| Windows 10/11 x64 | `app_1_install.ps1` | `app_2_run.ps1` |
| Ubuntu 24.04 x86_64 | `app_1_install.sh` | `app_2_run.sh` |

Windows — เปิด PowerShell ในโฟลเดอร์ที่ clone แล้วรัน:

```powershell
powershell -ExecutionPolicy Bypass -File .\app_1_install.ps1
powershell -ExecutionPolicy Bypass -File .\app_2_run.ps1
```

Ubuntu — เปิด Terminal ในโฟลเดอร์ที่ clone แล้วรัน:

```bash
bash app_1_install.sh
bash app_2_run.sh
```

เปิด URL ที่ Terminal แสดง โดยปกติคือ `http://127.0.0.1:8000` ไม่ต้อง activate venv
ติดตั้งครั้งแรกต้องใช้อินเทอร์เน็ตและสิทธิ์ติดตั้ง system packages; Windows ใช้ Microsoft App Installer (`winget`)
Ubuntu จะขอ `sudo` สำหรับ apt/build prerequisites ส่วนแอปให้รันด้วยผู้ใช้ปกติ

ตัวติดตั้งเตรียม Python 3.12, venv, Python libraries, FFmpeg/ffprobe, Node.js,
COLMAP 4.2.1, Brush 0.3.0, viewer/export assets และ mask weights แล้วตรวจ dependencies,
backend ของแต่ละขั้นตอน, checksum และทดลองเปิด HTTP app ในพื้นที่ทดสอบแยก
แสดงลำดับ `[1/7]`–`[7/7]`; log และผล Verify อยู่ใน `tools/install-logs/`
รันซ้ำได้เพื่อทำต่อ/ซ่อมไฟล์ที่ขาด โดยไม่ลบ Sources, Jobs หรือ Models

NVIDIA ที่ driver พร้อมจะเลือก CUDA สำหรับ COLMAP/Mask; AMD/Intel ใช้ CPU COLMAP,
WGPU สำหรับ Views/Brush และ DirectML สำหรับ Mask บน Windows ตามความพร้อมของเครื่อง
**Brush Train ต้องมี GPU และ driver ที่รองรับ**; เครื่อง CPU อย่างเดียวใช้เตรียม Views/poses/masks ได้
ตัวติดตั้งไม่เปลี่ยน NVIDIA display driver หรือ reboot เครื่องเอง หาก driver ยังไม่พร้อม Verify จะแจ้งสิ่งที่ต้องแก้
การตรวจ adapter เป็นความพร้อมเบื้องต้น ยังไม่ใช่ผลทดสอบ Train บน GPU นั้น

ตรวจซ้ำโดยไม่ติดตั้งหรือเริ่มงาน GPU:

```powershell
powershell -ExecutionPolicy Bypass -File .\app_1_install.ps1 -Check
```

```bash
bash app_1_install.sh --check
```

ใช้ `-Plan` / `--plan` เพื่อดูขั้นตอนโดยไม่ทำงานจริง, `-Device cpu|cuda` / `--cpu|--cuda`
เพื่อเลือก backend การติดตั้ง หากมี job กำลังทำงาน ตัวติดตั้งจะหยุดก่อนแก้ packages/tools;
ตัวเลือก Check ยังใช้ได้ จึงไม่ต้องหยุด job เพื่อดูความพร้อม

สคริปต์เดิม `setup*`, `install_brush*`, `install_colmap*` รวม/ย้ายเป็น helpers ใน
[`scripts/install/`](scripts/install) แล้ว ผู้ใช้ทั่วไปไม่ต้องเรียกทีละไฟล์
รายละเอียด Ubuntu/CUDA: [UBUNTU.md](docs/guides/UBUNTU.md) · [คู่มือฟีเจอร์ทั้งหมด](docs/README.md)
เตรียมทดสอบ/พัฒนาต่อบน RTX: [RTX_CHECKLIST.md](docs/guides/RTX_CHECKLIST.md)

โค้ด Python แบ่งตามหน้าที่ใน `app/`, ไฟล์หน้าเว็บใน `assets/app/` และ
`app/web/templates/`, เอกสารใน `docs/` โดยข้อมูลและคำสั่งรันเดิมยังอยู่ที่เดิม
ดู [โครงสร้างและวิธีพัฒนา](docs/DEVELOPMENT.md) สำหรับ import, workers และคำสั่งทดสอบ
