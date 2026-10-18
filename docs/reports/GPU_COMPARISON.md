# RX 6700 XT เทียบค่าประมาณ GPU และ Insta360 Cloud

ข้อมูลผลจริงจาก run status และเวลา `Done training!` ของ Brush; ตรวจ 13 ต.ค. 2026

## ผลจริง (เวลา นาที:วินาที)

| งาน | ช่วงคลิป (วินาที) | ตำแหน่ง / Views | ขนาด | Steps | Train | Views | Model รวมเตรียมข้อมูล | ทั้งงาน | Masked PSNR |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DJI 8K · High 1024 | 65.03 | 326 / 5,216 | 1024×1024 | 20,000 | 10:51 | 2:16 | 25:48 | 28:04 | 30.694 dB |
| DJI 8K · Ultra 1536 | 65.03 | 488 / 7,808 | 1536×1536 | 30,000 | 33:34 | 6:32 | 62:22 | 68:54 | 31.715 dB |
| Sample 360 · ช่วง 2 วินาที | 2.00 | 13 / 208 | 768×768 | 20,000 | 5:53 | 0:05 | 6:47 | 6:52 | 29.086 dB |
| Insta360 X4 · ช่วง 4 วินาที | 4.00 | 20 / 320 | 768×768 | 20,000 | 6:58 | 0:06 | 8:03 | 8:09 | 29.239 dB |

### เวลารายขั้นของ Ultra 1536

| ขั้น | เวลา นาที:วินาที |
| --- | ---: |
| สร้าง Views | 6:32 |
| เตรียม / spherical keyframes | 1:13 |
| COLMAP features | 1:11 |
| COLMAP matching | 1:51 |
| COLMAP camera poses | 3:35 |
| เตรียม dataset | 4:50 |
| Person masks · CPU | 14:20 |
| ช่วง Training ของแอป | 34:40 |
| ตรวจ / Export | 0:42 |

ช่วง Training ของแอปรวม startup/evaluation รอบ trainer จึงต่างจากเวลาที่ Brush รายงานใน `Done training!`; ไม่บวกสองค่านี้ซ้ำ
COLMAP และ person masks ใช้ CPU ส่วน Brush ใช้ RX 6700 XT / Vulkan จึงเปลี่ยน GPU อย่างเดียวไม่ลดทุกขั้นในสัดส่วนเดียวกัน

ทั้งงาน = Views + Model; Model รวม COLMAP, dataset, masks, Brush, evaluation และ export แล้ว จึงห้ามบวก Train ซ้ำ
เวลา local เริ่มจากไฟล์ที่อยู่ในเครื่องแล้ว ไม่รวมถ่าย, stitch/export MP4, อัปโหลด, ติดตั้ง และเปิด viewer

## ตารางจากสรุป AI

สมมติ 8K 120 วินาที, 300–600 ภาพที่ extract, 30K steps, COLMAP + gsplat/Nerfstudio. ทุกแถว GPU ด้านล่างเป็นค่าประมาณจาก AI ไม่ใช่ผลทดสอบไฟล์เรา

| GPU | VRAM | Train 30K (นาที) | ทั้งงาน (นาที) | ที่มา |
| --- | --- | --- | --- | --- |
| RTX 3060 | 12 GB | 35–90 | 60–180 | ประมาณจาก AI |
| RTX 3090 | 24 GB | 20–45 | 35–120 | ประมาณจาก AI |
| RTX 4090 | 24 GB | 10–25 | 25–100 | ประมาณจาก AI |
| RTX 5090 | 32 GB | 7–20 | 15–90 | ประมาณจาก AI |
| NVIDIA L4 | 24 GB | 25–60 | 45–150 | ประมาณจาก AI |
| A100 80GB | 80 GB | 10–30 | 25–100 | ประมาณจาก AI |
| H100 80GB | 80 GB | 6–20 | 20–90 | ประมาณจาก AI |
| H200 141GB | 141 GB | 6–18 | 20–90 | ประมาณจาก AI |
| RX 6700 XT · DJI 8K · High 1024 | 12 GB | 10:51 (20,000 steps) | 28:04 | วัดจริง Brush/Vulkan; input ต่างจากสมมติฐาน AI |
| RX 6700 XT · DJI 8K · Ultra 1536 | 12 GB | 33:34 (30,000 steps) | 68:54 | วัดจริง Brush/Vulkan; input ต่างจากสมมติฐาน AI |
| Insta360 Cloud | ไม่เปิดเผย | ไม่เปิดเผย | 60–120 (อ้างอิง 90) | จากสรุป AI; ไม่มีผลวัดฉากเดียวกัน |

AI เดิมไม่มีค่าของ RX 6700 XT และไม่ให้สัมประสิทธิ์สูตรครบ จึงคำนวณ % error ของ AI เดิมสำหรับ RX ไม่ได้ และผลจริงนี้ไม่ยืนยันความเร็ว RTX 5090

## เครื่องคำนวณจาก baseline จริง

ใช้ DJI High 1024 เป็น baseline ของ RX 6700 XT / Brush; สูตรใหม่ที่ระบุชัด ไม่ใช่การสร้างสูตร AI เดิมกลับมา

- Views = เวลา Views baseline × อัตราจำนวน Views × อัตราจำนวนพิกเซล
- Train = เวลา Train baseline × อัตรา steps × อัตราจำนวนพิกเซล
- งานอื่น = (Model baseline − Train baseline) × อัตราจำนวนตำแหน่ง

ไม่ได้สมมติว่า GPU ทุกรุ่นเร็วตาม TFLOPS; งานอื่นรวม CPU pose/masks, startup, eval และ export
สูตรยังไม่คิด scene complexity, splat count, VRAM, nonlinear matching หรือผลของ resolution ต่อ masks
ค่า baseline ย่อมตรง baseline เอง จึงใช้รอบ Ultra เป็นตัวตรวจสูตรอีกหนึ่งรอบโดยไม่ปรับ coefficient ให้เข้ากับ Ultra

| ขั้น | คำนวณ High → Ultra | Ultra วัดจริง |
| --- | ---: | ---: |
| Views | 7:39 | 6:32 |
| Train | 36:37 | 33:34 |
| งานอื่น | 22:22 | 28:48 |
| ทั้งงาน | 66:38 | 68:54 |

รวมคลาดเคลื่อน -3.28% (ค่าคำนวณ − ค่าจริง เทียบค่าจริง). ใกล้กันสำหรับห้องนี้หนึ่งคู่เท่านั้น; error รายขั้นหักล้างกัน จึงยังรับรองความแม่นในฉากอื่นไม่ได้

## เวลา Cloud เทียบผลจริง

90 นาทีเป็นเวลาอ้างอิงจากสรุปที่แนบ ไม่ใช่เวลา cloud ที่เราวัด และยังไม่ยืนยันว่ารวม upload/queue เท่าใด
- DJI 8K · High 1024: 28:04 เทียบ 90 นาที = อัตราส่วน 3.21 เท่า; เทียบช่วง 60–120 นาที = 2.14–4.28 เท่า (ต่าง input/trainer/ขอบเขตเวลา)
- DJI 8K · Ultra 1536: 68:54 เทียบ 90 นาที = อัตราส่วน 1.31 เท่า; เทียบช่วง 60–120 นาที = 0.87–1.74 เท่า (ต่าง input/trainer/ขอบเขตเวลา)

## ระบบ auto เทียบเท่า Insta360 แล้วหรือยัง?

เทียบเคียงด้านขั้นตอนหลัก: stitched panorama MP4 → Views → spherical COLMAP poses → masks/seed checks → Brush GPU → evaluation/export PLY → local web viewer ต่ออัตโนมัติ โดย runs ที่เลือกเปิด auto_model=true
ยังยืนยันว่าเทียบเท่าผลิตภัณฑ์หรือคุณภาพไม่ได้: ไม่มี same-scene cloud A/B, คุณภาพ novel views/กระจกยังต้องตรวจ และ local viewer ไม่ใช่บริการ cloud upload/queue/public share ของ Insta360
raw .insv ยังต้อง stitch/export เป็น MP4 ตาม workflow นี้; ผลรอบ 1024 มี review ภาพ แต่ PSNR ของสองรอบใช้ชุด eval/masks ต่างกัน จึงสรุปว่าคุณภาพดีขึ้น 1.02 dB แบบเทียบตรงไม่ได้

## หลักฐานและแหล่งอ้างอิง

- DJI 8K · High 1024: [web_data/jobs/832dfb8b31/model_runs/20261013_203220_964927f8/status.json](../../web_data/jobs/832dfb8b31/model_runs/20261013_203220_964927f8/status.json), [Views](../../web_data/jobs/832dfb8b31/view_runs/20261013_203004_7299fc8b/status.json), [Brush log](../../web_data/jobs/832dfb8b31/model_runs/20261013_203220_964927f8/3d.log)
- DJI 8K · Ultra 1536: [web_data/jobs/3b74c7f7c0/model_runs/20261013_215442_e4691045/status.json](../../web_data/jobs/3b74c7f7c0/model_runs/20261013_215442_e4691045/status.json), [Views](../../web_data/jobs/3b74c7f7c0/view_runs/20261013_214809_a7bab88f/status.json), [Brush log](../../web_data/jobs/3b74c7f7c0/model_runs/20261013_215442_e4691045/3d.log)
- Sample 360 · ช่วง 2 วินาที: [web_data/jobs/a147ed6bb2/model_runs/20261008_012920_c9975336/status.json](../../web_data/jobs/a147ed6bb2/model_runs/20261008_012920_c9975336/status.json), [Views](../../web_data/jobs/a147ed6bb2/view_runs/20261008_012916_041d562c/status.json), [Brush log](../../web_data/jobs/a147ed6bb2/model_runs/20261008_012920_c9975336/3d.log)
- Insta360 X4 · ช่วง 4 วินาที: [web_data/jobs/bb41eed643/model_runs/20261008_013616_352f99d7/status.json](../../web_data/jobs/bb41eed643/model_runs/20261008_013616_352f99d7/status.json), [Views](../../web_data/jobs/bb41eed643/view_runs/20261008_013610_689cd997/status.json), [Brush log](../../web_data/jobs/bb41eed643/model_runs/20261008_013616_352f99d7/3d.log)
- [gsplat: RTX 4090 / Splatfacto 30K = 13:30, ต่าง dataset และ trainer](https://docs.gsplat.studio/versions/0.1.11/tests/eval.html)
- [gsplat: TITAN RTX / 30K = 35:49, ต่าง dataset และ trainer](https://docs.gsplat.studio/main/tests/eval.html)
- [Insta360: Spatial Capture และโควตา 4 ครั้งต่อเดือน (ตรวจ 13 ต.ค. 2026)](https://www.insta360.com/us/product/insta360-x6)

รอบ baseline ข้างต้นสร้างก่อนเพิ่ม WGPU Views / DirectML masks เวลาของขั้นเตรียมข้อมูลจึงยังอ้างอิง CPU ของรอบเดิม ดูผลเร่งเตรียมข้อมูลเพิ่มเติมใน GPU_PREPARATION.md
