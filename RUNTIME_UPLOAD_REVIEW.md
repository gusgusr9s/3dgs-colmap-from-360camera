# Upload, Views preview, progress และ Verify — 17 ตุลาคม 2026

พัฒนาโดยไม่ปิดหรือ restart แอปพอร์ต 8000 และไม่สั่ง training / GPU inference เพิ่ม
รุ่นสำหรับตรวจเปิดที่ `http://127.0.0.1:8008/` แยก listener จากแอปเดิม
การเปิดแอปเดิมใหม่ภายหลังจึงจะโหลด backend ใหม่ทั้งหมด

## ปัญหาที่แก้

### Upload ไม่แสดงสถานะ

พบจาก Chromium จริงว่า hidden input `name="action"` ทับ property `form.action`.
ตัวกรอง URL ของ action feedback จึงไม่เข้าเส้นทาง XHR และเกิด native form navigation
ที่หมุนรอผลอย่างเดียว แก้ให้อ่าน attribute ของฟอร์มแทน พร้อม regression ของ DOM contract นี้

ทุกหน้าที่ใช้ฟอร์มร่วมกัน เช่น Dashboard / Sources / Models แสดงกล่องแจ้งก่อนเตรียม
FormData / ส่งไฟล์ โดยเว้นให้ browser paint ก่อน เริ่มที่ 0%, แสดงชื่อไฟล์และขนาด,
อัปเดตเปอร์เซ็นต์และ bytes ที่ส่ง จากนั้นแยก “ส่งครบ 100% / ตรวจและบันทึก” พร้อมเวลารอ
ป้องกัน submit ซ้ำ ยกเลิกได้ก่อนส่งครบ และกดลองใหม่ด้วยไฟล์เดิมได้
หลังส่งครบแล้วปิดการยกเลิกเพื่อรอคำตอบจาก server; error ที่ยืนยันผลไม่ได้แจ้งให้ตรวจรายการก่อนลองซ้ำ

### พรีวิว Views

เดิมการแสดงครั้งเดียวขอภาพต้นฉบับ, มุมที่เลือก, เฟรมข้างเคียงถึง 7 เฟรม และหลายทิศ
พร้อมกัน การ cache เดิมไม่ได้กัน cache miss ที่พร้อมกัน จึง decode คลิปใหญ่ซ้ำได้
และไม่มีเวลาสิ้นสุดของ native decode ที่แน่นอน

เปลี่ยนเป็นขอภาพมุมเดียวที่เลือกและเฟรมต้นฉบับตามลำดับ ใช้ cache ของเฟรมร่วมกัน
แถบเฟรม/ทิศเป็นปุ่มเลือกโดยไม่ขอ thumbnails เพิ่ม ยกเลิก fetch เก่าเมื่อเปลี่ยนค่า/ปิดหน้าต่าง
และป้องกันผลเก่าทับผลล่าสุด ภาพเก่าซ่อนระหว่างโหลดเพื่อไม่เข้าใจว่าเป็นมุมใหม่

แยก CPU decoder subprocess หนึ่งตัวต่อ server, จำกัด threads, ขนาดเฟรมใน cache สูงสุด
2048 ด้านยาวและ cache 8 เฟรม, จบการอ่านเมื่อเกิน 25 วินาที
เมื่อ decoder ไม่ว่างตอบ 429 พร้อมเหตุผล หน้าเว็บมี Loading / error / Retry ชัดเจน
metadata มี timeout ฝั่ง browser 30 วินาที และรูป 35 วินาที

พรีวิวไม่เข้าคิว GPU ของ Model และไม่แก้ dataset แต่ยังใช้ CPU / RAM / ดิสก์ร่วมกัน
การ abort fetch หยุดรอผลทาง browser; decoder ที่เริ่มแล้วอาจทำต่อจนสำเร็จหรือครบ timeout
การลดความละเอียดนี้ใช้เฉพาะ preview ไม่ลดความละเอียด train

### Progress

แสดงเปอร์เซ็นต์จากตัวนับจริงของ Views, COLMAP features / sequential matching,
Masks, การเก็บภาพ PNG จากต้นฉบับ และ training steps เมื่อมีจำนวนงานรวม
Model มี “ภาพรวมโดยประมาณ” แยกจากเปอร์เซ็นต์ของขั้นปัจจุบัน
Pose / dataset / evaluation / export ที่ไม่มีตัวนับยังเป็น indeterminate พร้อมเวลา
log / status ล่าสุด ไม่สร้างเปอร์เซ็นต์สมมติ และไม่ใช้เวลานิ่งเป็นหลักฐานว่างานล้มเหลว
ยังรักษาการอัปเดต DOM เดิมและพักการเปลี่ยนส่วนอื่นขณะผู้ใช้เปิดเมนู/โต้ตอบ

### Verify เครื่อง

`scripts/verify_environment.py` ใช้ interpreter / environment เดียวกับแอป
Dashboard เรียกเป็น background subprocess เมื่อกดปุ่มเท่านั้น; เปิดหน้าเฉย ๆ ไม่เริ่ม probe
GET `/api/runtime-verification` อ่านสถานะ, POST เริ่มตรวจและรวมคำขอซ้ำใน server เดียว
มี timeout รวม 70 วินาที และแสดงข้อผิดพลาดให้กดลองใหม่ได้

รายงาน OS / Python executable, packages, hardware adapters, backend ของ Upload,
Preview, Views, COLMAP (360 และวิดีโอปกติ), Masks, Brush และ Export พร้อมเหตุผล
ตรวจ adapter / package / executable เท่านั้น ไม่มี train, inference, ดาวน์โหลด weights
หรือการสร้าง CUDA context เพื่อถามชื่อการ์ด ผล GPU จึงระบุว่าต้องยืนยันจากงานจริง
ไม่อ้างว่า Brush มี CPU training fallback

## ผลทดสอบ

- Python suite: **226 tests, OK, skipped 1 GPU opt-in** (`runtime_upload_tests.log`).
  หลังเพิ่มการป้องกัน CUDA context ตรวจชุด Verify อีกครั้ง **7/7** และ progress **4/4** ผ่าน
- JavaScript: **18 ชุดผ่าน** รวม DOM named-property collision, first paint,
  ยกเลิกก่อนส่ง, retry, timeout/stale response และ stable polling
- Chromium แยกข้อมูล: อัปโหลด Dashboard / Sources / Models ผ่านการจำลอง upload ช้า,
  เห็นเวลารอบันทึกเพิ่มจริง, ป้องกันกดซ้ำ, ยกเลิกกลางส่งและลองใหม่ได้
- พรีวิวขอเพียง 2 ภาพของตำแหน่งที่เลือก, เปลี่ยนทิศ, ทดสอบ 429 และ Retry,
  Verify desktop / mobile, และ progress เปลี่ยน 24 → 53% โดยไม่ reload; ไม่มี page errors
- ตรวจไฟล์ DJI จริง `1017.mp4` **5,419,504,062 bytes, 7680×3840**:
  metadata **0.813 s**, ภาพแรก **2.625 s**, JPEG preview **58,477 bytes**;
  size / mtime ของ Source ไม่เปลี่ยน ผลเวลาเป็นเพียงหนึ่งครั้ง ไม่ใช่ benchmark ทั้ง pipeline
- ตรวจ UI สดบน 8008: DJI preview ขึ้น, Verify มี 7 ขั้นตอน,
  job เดิมแสดง COLMAP poses และสถานะจาก 8000 ยัง `running`

Regression เดิมหนึ่งชุดผูกกับ `data/raw/test_360_tram.mp4` ที่ถูกลบผ่านงานผู้ใช้:
ปรับ test ให้สร้างคลิปของตัวเอง ไม่กู้คืนไฟล์ที่ผู้ใช้ลบและไม่พึ่ง media ใน workspace

หลักฐาน/screenshots อยู่ใน `web_data/capture_review/runtime_upload_20261017/`,
`runtime_upload_tests.log` และ `runtime_upload_browser.log` (ไม่เก็บ artifacts ใน Git)

Electron / installer / update / rollback จัดทำเฉพาะ [DESKTOP_PLAN.md](DESKTOP_PLAN.md)
ยังไม่ได้ติดตั้งหรือพัฒนาส่วน Desktop
