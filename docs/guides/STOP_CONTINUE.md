# หยุดและทำต่อ / ป้องกันการเสียงาน

ใช้กับ Model run ที่เริ่มด้วยแอปรุ่นนี้ งานที่รันอยู่ก่อนอัปเดตต้องปล่อยให้เสร็จ
ก่อนเปิดแอปรุ่นใหม่ โค้ดบนดิสก์ไม่เปลี่ยน worker ที่เริ่มไปแล้ว และไม่ต้อง restart ระหว่างพัฒนา

## วิธีใช้

1. หน้า Job / Jobs ระหว่างสร้าง Model มีปุ่ม **หยุดและเก็บงานไว้** ใต้ progress
2. หลังกด แสดง **กำลังหยุดอย่างปลอดภัย** ป้องกันกดซ้ำ และยังอ่าน Console ได้
   ต้องรอ worker ออกก่อนจึงปล่อยคิว; Global BA บางรอบอาจใช้เวลาตอบรับนาน
3. เมื่อหยุดแล้ว กด **ทำต่อ** ในหน้า Job หรือ Jobs
   ระบบเลือก checkpoint → dataset พร้อมเทรน → ภาพ/poses/Masks ที่ทำครบ → COLMAP cache
   ตามขั้นล่าสุดที่ตรวจว่าใช้ได้ ไม่ต้องเลือกวิธีกู้เอง
4. การทำต่อสร้าง run ID ใหม่ เก็บ input และ Model รุ่นเดิมไว้

ปุ่มพัก Console พักข้อความบนหน้าจอเท่านั้น ไม่ได้หยุดประมวลผล

## หยุดได้ถึงระดับไหน

| ขั้นตอน | การหยุด / กู้ต่อ |
|---|---|
| รอคิว | ยกเลิกการรอได้ทันทีตามระบบเดิม |
| สร้าง Views / หา pose เพื่อ Preview อย่างเดียว | รอบนี้ยังไม่เพิ่มปุ่มหยุดหรือกู้เฟรมที่ทำค้าง |
| Model: เตรียมข้อมูล | หยุดที่จุดตรวจระหว่างขั้น; ช่วง Python ที่ไม่มี callback ต้องรอจนถึงจุดตรวจถัดไป |
| Model: COLMAP สกัด features / matching | ปล่อยคำสั่งปัจจุบันจบก่อน แล้วบันทึก stage cache; ไม่ตัด SQLite กลางงาน |
| Model: COLMAP mapper | ส่ง SIGINT ให้ COLMAP บันทึก reconstruction ก่อนออก; มี snapshot ทุก 50 กล้องด้วย |
| Model: Masks | หยุดผ่าน progress callback; Mask เขียนใน staging directory จึงไม่แทนชุดเดิมด้วยชุดครึ่งเดียว; ตอนทำต่อจะทำ Masks ที่ยังไม่เสร็จใหม่ |
| Model: Brush Train / Fine tune | หยุด native process แล้วกู้จาก PLY checkpoint ล่าสุดที่เขียนครบ; ก่อน checkpoint แรกต้องเริ่ม Train ใหม่โดยใช้ poses เดิม |
| ตรวจ Render / เผยแพร่ | ตรวจคำขอหยุดก่อนย้าย PLY ไป Models; checkpoint สุดท้ายใช้ Render ที่ประเมินแล้วได้เมื่อไฟล์ครบ |

## Checkpoint ของ Brush

- Export ทุก **5,000 steps** เป็น `brush_export/splat_<step>.ply` คนละชื่อ จึงไม่เขียนทับ checkpoint ก่อนหน้า
- Worker เก็บสอง checkpoint ล่าสุดที่ผ่านการตรวจครบ: header, ขนาด payload, ค่า finite, scale และ quaternion
  ไฟล์ล่าสุดที่เขียนค้างไม่ทำให้ลบ checkpoint ก่อนหน้า
- กู้ใน dataset สำเนาของ run ใหม่ โดยใช้ hardlink สำหรับ pixels ที่ไม่แก้ไขและแยกไฟล์ JSON
  ลดการคัดลอกภาพหลายหมื่นไฟล์; JPEG recovery ใช้การเขียนไฟล์ใหม่แทนไฟล์เดิม
- คงชุด train/evaluation เดิม รวม camera-position split, Mask และคุณภาพที่ตั้งไว้
- ใช้ `--start-iter` ตาม checkpoint และ `--total-steps` ตามเป้าเดิม
  **weights กับเลข iteration กลับมาได้ แต่ optimizer/RNG/data-loader เริ่มใหม่** จึงไม่ใช่ exact resume
- ยังคงตรวจ held-out Render อย่างน้อย 3 ภาพ, PSNR ขั้นต่ำเดิม และ numerical validation ก่อนเผยแพร่
- ไม่รับรองคุณภาพหลัง resume ด้วยจำนวน steps อย่างเดียว ต้องเทียบ Render กับรอบที่ไม่หยุด

## ป้องกันการหยุดผิดงาน / แอปพัง

- HTTP ไม่รับ PID เพื่อ kill; คำขอหยุดต้องตรง token ของ `.task.lock` รอบปัจจุบัน
  คำขอจากหน้าเก่าหรือ run ก่อนหน้าใช้หยุด run ใหม่ไม่ได้
- COLMAP/Brush อยู่ใต้ supervisor แยกจาก web server และมี hidden console ของตัวเองบน Windows
  การส่ง Ctrl+C ไม่กระทบ console ของผู้ใช้หรือ web server
- Supervisor ตรวจตัวตนและเวลาเกิดของ process แม่ หากแอปตายจะส่งสัญญาณให้ native worker ออก
  ไม่ใช้ timeout เพื่อ force-kill ระหว่างเซฟ
- Job และคิวกลางยังเห็น native worker ที่กำลังเก็บงานหลัง process แม่ตาย ป้องกันเริ่มงานทับทันที
- ไฟดับหรือ force-kill ทั้งต้นไม้ process ไม่สามารถบังคับให้เซฟสำเร็จได้ ใช้ snapshot/checkpoint ล่าสุดที่ตรวจอ่านได้
- บน Windows ใช้ Python interpreter จริงสำหรับ supervisor เพื่อไม่ให้ venv redirector รับ Ctrl+C
  แล้วจบก่อน worker ที่กำลังเซฟ

## Cache / การลดงานซ้ำ

- เก็บ `stage_cache.json` หลัง features/matching สำเร็จพร้อม input signature
  ทำต่อได้แม้ยังไม่มี sparse model; หาก matching ไม่ครบจะรัน matching ใหม่โดยใช้ features เดิม
- ตรวจ view run, source fingerprint, frame names, image IDs และลายเซ็นชุดภาพก่อนใช้ cache
- รองรับ sparse model ทั้ง `sparse/0/` และ `sparse/` โดยตรง ซึ่ง native mapper ใช้ต่างกันเมื่อ resume
  เก็บ completion marker หลัง mapper จบ เพื่อข้าม BA เมื่อหยุด/พังในขั้นถัดไป
- DB กู้เป็น SQLite backup ใน run ใหม่ ไม่แก้ DB ต้นฉบับ
- กู้ Train ไม่รัน COLMAP/Mask/คัดภาพ/แยก evaluation ซ้ำ
- ก่อน checkpoint แรก ใช้ dataset/split/initialization ที่เตรียมแล้วได้
  หากภาพและ poses ครบจะไม่เตรียม JPEG ทั้งชุดใหม่เพียงเพราะเป็นการกู้จาก COLMAP
- ภาพที่ยังเตรียมไม่ครบใช้ bounded workers และ cache JPEG ที่ตรวจ checksum
  รายละเอียดและผล benchmark อยู่ใน [PIPELINE_RESUME.md](PIPELINE_RESUME.md)
- `splat.ply` หลัง Train เป็น alias ผ่าน hardlink เมื่อระบบไฟล์รองรับ เพื่อลดการคัดลอก PLY ใหญ่
- ค่าความละเอียด, feature cap และ quality gates เดิมยังใช้เหมือนเดิม
  การลด keyframes/features เพิ่มเติมควร benchmark กับ held-out renders ใน job แยกตาม [COLMAP_RECOVERY.md](COLMAP_RECOVERY.md)

## หลักฐานการทดสอบ

อยู่ใน `web_data/capture_review/stop_continue_20261017/`:

- Full Python regression 271 tests ผ่าน (GPU opt-in skipped 1); หลังปรับการเก็บ error/options เดิม
  ตรวจชุด COLMAP recovery เพิ่ม 10 tests ผ่าน และ JavaScript ทั้ง 18 ชุดผ่าน

- Unit/integration regression รวมการส่งสัญญาณจริงบน Windows ให้ child fixture เซฟและออก
  การตายของ process แม่, stale token, การถือสิทธิ์คิวของ worker ที่ยังไม่ออก
- ตรวจ checkpoint ขาด/NaN, การเก็บรุ่นก่อน, dataset/config mismatch, reuse ชุด evaluation เดิม
  และปฏิเสธเผยแพร่เมื่อไม่มีภาพประเมินที่ใช้ได้
- Chromium: กด Stop ให้ child fixture จริงเซฟ, จากนั้นกด Continue จาก 5,000 steps ผ่าน HTTP
  ส่วนผล Brush ใน browser test จำลอง; ไม่เพิ่มงาน GPU ระหว่าง job ของผู้ใช้
- ตรวจ flags `--start-iter`, `--export-every`, `--export-name` กับ Brush executable ที่ติดตั้งจริง
  และตรวจ parser ด้วย PLY ที่ Brush เคยสร้างจริง

ผลทดสอบ GPU train แบบหยุดกลางทางและเทียบคุณภาพกับ uninterrupted run
ติดตามใน [PIPELINE_RESUME.md](PIPELINE_RESUME.md) ใช้คิวกลางและ input สั้นในพื้นที่แยก
ยังต้องยืนยัน RTX/Ubuntu บนเครื่องเป้าหมาย

ผลจริง DJI รอบกู้ `20261017_225629_549aba99`: mapper สำเร็จ 1,230/1,230 กล้อง,
458,490 points, mean reprojection 0.543 px, ไม่พบ matrix warning ใช้เวลา native mapper 30.699 นาที
เป็นการทำต่อจาก 879 กล้อง ไม่ใช่ benchmark เริ่มใหม่เทียบกับรอบเดิม
หลังแก้การอ่าน sparse layout ใช้ผลนี้ใน run `20261017_233058_d88775af` ต่อได้โดยข้าม mapper/BA จริง
ยังไม่ถือว่า Model ผ่านจน Train และ held-out evaluation จบ

อ้างอิง: [Brush training/export](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-process/src/train_stream.rs),
[Brush start iteration](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-process/src/config.rs),
[Windows console signals](https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent)
