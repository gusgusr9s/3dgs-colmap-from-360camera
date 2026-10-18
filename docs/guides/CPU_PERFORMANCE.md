# CPU เต็มกำลังและ log ต่อขั้นตอน

`full` เป็นค่าเริ่มต้นของ processing workers ใช้ logical CPUs ที่โปรเซสเข้าถึงได้
โดยตรวจ affinity และ CPU quota ของ container ที่รองรับ เครื่องพัฒนานี้ตรวจได้ 28 CPUs
เลือก `balanced` เพื่อใช้ประมาณ 75% หรือกำหนด `CPU_THREADS` / `--cpu-threads` เองได้
จำนวนที่ระบุไม่เกิน CPU ที่เข้าถึงได้ ไม่เปลี่ยน affinity หรือ priority ของงานที่กำลังรัน

```powershell
.\app_2_run.ps1 -CpuMode full
.\app_2_run.ps1 -CpuMode balanced -CpuThreads 8
```

Ubuntu และ Windows ใช้ตัวเลือกเดียวกันผ่าน `web_app.py`:

```bash
python web_app.py --cpu-mode full
python web_app.py --cpu-mode balanced --cpu-threads 8
python web_app.py --view-device cpu --colmap-device cpu --mask-device cpu --cpu-mode full
```

คำสั่งสุดท้ายเลือก CPU สำหรับ Views / COLMAP / Masks เท่านั้น **Brush Train ยังต้องมี GPU**
Ubuntu wrapper ส่ง flags ต่อได้: `bash app_2_run.sh --cpu-mode full`.
Environment variables คือ `CPU_MODE=full|balanced` และ `CPU_THREADS=<จำนวนบวก>`
Verify บน Dashboard และสถานะงานบอก budget ที่เลือกไว้

## ส่วนที่ปรับ

- **CPU Views**: งานเว็บเรียก worker แยก คำนวณ pixel maps แบบ fixed point ครั้งเดียว
  แล้วทำ projection + JPEG หลายมุมพร้อมกัน ใช้ sampling / FOV / คุณภาพ JPEG เดิม
  จำกัด pending tasks ไม่เกินสองเท่าของ workers และแชร์ panorama ระหว่างมุม
  ไม่สร้างชุดเฟรมทั้งคลิปใน RAM และไม่ให้แต่ละ remap แตก native threads ซ้อน pool อีก
- **COLMAP CPU**: ยกเลิกเพดาน extraction 8 threads ใช้ budget สำหรับ extraction,
  matching และ mapper ทั้ง COLMAP 3.12 / 4.2 โดยใช้ชื่อ option ที่ตรงรุ่น
  ไม่ลดจำนวน features, overlap, iterations หรือเปลี่ยน camera model / intrinsics
- **Masks CPU**: ทำงานใน subprocess เช่นเดียวกับ GPU ตั้ง Torch / OpenCV threads
  ตาม budget และรักษาโมเดล / batch size / เกณฑ์ mask เดิม
- **Brush**: ตั้ง budget ของ CPU libraries / Rayon สำหรับส่วนเตรียมข้อมูลภายใน process
  GPU training / steps / ขนาดภาพไม่เปลี่ยน
- Web server และ interactive preview ไม่รับการตั้ง native threads แบบเต็มเครื่องตาม worker
  พรีวิวยังใช้ decoder จำกัดหนึ่งตัว ไม่เข้าคิว GPU แต่ CPU / RAM / ดิสก์ยังแชร์กับงานอื่น

การเปิดทุก thread ไม่รับประกัน CPU 100% ตลอดงาน: decode แบบต่อเนื่อง, disk I/O,
memory bandwidth, การจับคู่บางช่วง และ bundle adjustment มีข้อจำกัดในการทำพร้อมกัน
บนบาง CPU จำนวน threads มากขึ้นอาจช้าลง จึงมีตัวเลือก balanced / จำกัด threads ให้เทียบผลจริง
RAM เพิ่มตามจำนวน workers และขนาดภาพ ควรลด threads ถ้าเครื่องเริ่มใช้ swap

## Log และเวลาที่เก็บ

หน้า Job มี **Logs / เวลาแต่ละขั้นตอน** ดูเวลาเริ่ม–จบ, ระยะเวลา, สถานะ และเปิดไฟล์ได้:

- `view_runs/<run>/process.log`, `process.jsonl`, `process_summary.json`, `views.log`
- `model_runs/<run>/process.log`, `process.jsonl`, `process_summary.json`, `3d.log`
- `pose_runs/<run>/process.log`, `process.jsonl`, `process_summary.json`, `poses.log`

ไฟล์ทั้งหมดอยู่ใต้ `web_data/jobs/<job>/` รอบใหม่ใช้รหัสใหม่ ไม่เขียนทับ log รอบก่อน
Human log / JSONL ใช้ UTC ที่ระบุ offset ส่วนตารางหน้าเว็บแสดง UTC+7
เก็บ phase transitions, progress ที่จำกัดความถี่, backend / fallback / CPU budget,
เวลารวมและข้อผิดพลาด และเพิ่ม start/end/elapsed ของคำสั่ง COLMAP / Brush ลงใน raw log
Views เก็บ decode time, ผลรวมเวลา worker projection / JPEG และ wall time ใน `view_compute.timings`
ผลรวม worker time อาจมากกว่า wall time เพราะทำงานขนาน จึงห้ามบวกแทนเวลารวม

งานเก่าหรืองานที่เริ่มอยู่ก่อนอัปเดตใช้ log / เวลาที่มีอยู่จริง ไม่มีการสร้างเวลาแต่ละขั้นย้อนหลัง
ค่าของงานเดิมไม่เปลี่ยนจนจบ เปิด backend ใหม่เมื่อสะดวกเพื่อใช้กับงานรอบต่อไป

## ตรวจผลโดยไม่ใช้ GPU หรือแก้ Jobs

```powershell
.\.venv-win\Scripts\python.exe -X utf8 scripts/benchmark_cpu_views.py --threads 2 --positions 20 --resolution 768
```

สร้างคลิปสังเคราะห์และ output แยกใน `web_data/capture_review/cpu_benchmark_<เวลา>/`
เทียบเวลารวม decode / projection / JPEG / disk รวม startup ของ CPU worker และตรวจ JPEG ทุกไฟล์ตรงกัน
ใช้ 2 threads เป็นค่าเริ่มต้นเพื่อไม่แย่ง CPU ทั้งเครื่องระหว่างมีงานเดิม
เปลี่ยนเป็น `--threads 28` หลังงานเดิมจบเพื่อวัดบนเครื่องนี้โดยตรง

ผลทดสอบสองครั้ง: **320 Views 768 px, 2 threads**, แบบเดิม **5.09–5.22 s** → แบบขนาน
**3.30–3.54 s (1.44–1.58×)**, JPEG ตรงกันทุกไบต์ เป็นผลของคลิปสังเคราะห์ ไม่ใช่ benchmark COLMAP
หรือ Model ทั้ง pipeline และไม่รับรองอัตราเร่งเท่ากันทุกเครื่อง

Regression Python 234 รายการผ่าน (ข้าม GPU opt-in 1), JavaScript 18 ชุดผ่าน
ตรวจ Chromium ของ CPU job ในพื้นที่แยก: log / JSONL / summary / worker log เปิดได้,
ตารางเวลาบน desktop / mobile และ Verify แสดง 28 CPUs; ไม่มี page errors
หลักฐานอยู่ใน `web_data/capture_review/cpu_optimization_20261017/`
และ `web_data/capture_review/cpu_optimization_tests.log` (ไม่เก็บ artifacts ใน Git)
