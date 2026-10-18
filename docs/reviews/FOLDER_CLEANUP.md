# ตรวจการจัดโครงสร้างโฟลเดอร์ — 18 ต.ค. 2026

ย้ายไฟล์จาก root 99 ไฟล์เข้า `app/`, `assets/app/`, `docs/`
root เหลือ 12 ไฟล์ รวมสคริปต์ install/run, requirements, README, LICENSE,
Git configuration และ compatibility entry points สองตัว
ดู [โครงสร้างสำหรับพัฒนา](../DEVELOPMENT.md)

## ขอบเขต

- Python แบ่งเป็น pipeline, runtime, jobs, models และ web; imports อ้างชื่อ package ชัดเจน
- พาธ repository ใช้ `app.paths` ร่วมกัน ป้องกันเขียนข้อมูล/ค้น tools ผิดโฟลเดอร์หลังย้าย module
- HTML อยู่ใน `app/web/templates/`; JavaScript/CSS อยู่ใน `assets/app/`
- แยก workspace CSS/JS ที่ฝังใน server ออกเป็นไฟล์ และตรวจข้อความ runtime ตรงเดิม
- รวม route ส่งไฟล์หน้าเว็บเป็น allowlist โดยคง public URLs เดิม และไม่เปิดให้โหลด source/path traversal
- Worker เรียกผ่าน `python -m`; CPU/GPU workers กำหนด working directory ชัดเจน
  native supervisor เก็บ import paths ของ venv และ repository แม้ทำงานจากโฟลเดอร์ dataset
- คง `web_app.py` / `pano_to_views.py` เป็นตัวเรียกบาง ๆ
  การ import แบบเดิมอ้าง module เดียวกับ package จึงใช้คิวและสถานะเดียวกัน
- ปรับ imports ใน tests/scripts, พาธตัวสร้างรายงาน/ค่า preset และลิงก์เอกสารตามที่อยู่ใหม่
- รักษา Sources, Jobs, Model versions, checkpoints, venv และ engines ไว้ตำแหน่งเดิม
- ไม่เพิ่ม Electron/updater ในรอบนี้ และไม่เปลี่ยนสูตรสร้างภาพ/poses หรือค่า Train

ก่อนเปลี่ยนเว็บเป็นโค้ดใหม่ตรวจว่าไม่มี job ทำงาน และเปิดใหม่หลังจัดโครงสร้างเสร็จ
ระหว่างเตรียมการย้ายยังเก็บไฟล์เดิมให้แอปที่เปิดอยู่ใช้งานได้

## ผลทดสอบ

| รายการ | ผล |
|---|---|
| Python regression หลังนำไฟล์ root เดิมออกจริง | 293 tests ผ่าน; skip GPU opt-in 1 |
| JavaScript UI checks | 19 ชุดผ่าน |
| Native Stop / parent death | ผ่าน Windows CPU child fixture รวมการรอปิด log handle ก่อน cleanup |
| CPU Views worker จาก working directory อื่น | ผ่าน ภาพ 360 ขนาดเล็ก 2 Views |
| Packaged GPU Views / CPU Mask workers จริง | ผ่าน WGPU/Vulkan 2 Views และ CPU Masks ครบ 2 ภาพ |
| Entry points เดิมจาก working directory อื่น | `--help` ผ่านทั้งสองตัว |
| Installer scripts | Windows fixtures + Bash syntax/plan ผ่านใน regression |
| HTTP installer smoke | Dashboard/Sources/Jobs/Models/assets ผ่านในพื้นที่แยก |
| Environment Verify | `ready=true`; พบ runtime และ adapter เดิม |
| Chromium บนเว็บจริง | 12 หน้า HTTP 200; ไม่มี page error หรือ asset หาย |
| Chromium Stop → Continue | ปุ่มยืนยัน Stop, CPU worker เซฟ, checkpoint 5,000 → done; ผล Brush จำลอง |

หน้าที่ตรวจด้วย Chromium: Dashboard, Sources, Jobs, Models, Tutorial, Benchmarks,
Experiments, Project overview, Camera Preview, Model Viewer, Model Crop และ Quality
เปิดอ่าน/ดูเท่านั้น ไม่ส่ง action ให้ jobs จริง

## GPU จริงหลังย้าย worker

ใช้คิวกลางเดียวกับแอปและ fixture สำเนาเดิม บน AMD Radeon RX 6700 XT / Vulkan:

- 16 ภาพ Train / 3 ภาพประเมิน, 128 px, 1,000 steps
- หยุดหลัง checkpoint 250 และเปิด worker ใหม่ทำต่อถึง 1,000
- Masked PSNR รอบต่อเนื่อง **27.620 dB**, รอบหยุด/ต่อ **27.588 dB** ต่าง **−0.032 dB**
- ได้ภาพประเมินครบ 3/3, weights เปลี่ยนจริง, Source checkpoint SHA-256 เดิม

เป็นการตรวจเส้นทาง worker/checkpoint หลัง refactor ด้วย fixture ขนาดเล็ก
ไม่ใช่ผลรับรอง RTX/Ubuntu หรือ exact resume; optimizer/RNG ยังเริ่มใหม่ตามเดิม
ผล GPU รอบใหญ่กว่านี้ก่อนจัดโครงสร้างดู [PIPELINE_RESUME.md](../guides/PIPELINE_RESUME.md)

หลักฐานในเครื่อง:

- `web_data/capture_review/folder_cleanup_20261018/`: regression, Verify, ภาพหน้าจอและผล browser
- `web_data/capture_review/folder_cleanup_gpu_20261018/result.json`: GPU renders, checkpoint และ logs

ไฟล์หลักฐาน/runtime ไม่รวมใน Git ตาม `.gitignore`; เอกสารนี้เก็บสรุปผลไว้ให้ clone ใหม่อ่านได้
