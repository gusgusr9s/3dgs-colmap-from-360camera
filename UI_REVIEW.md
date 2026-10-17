# UI review และตรวจงานก่อนหน้า — 17 ตุลาคม 2026

สรุปความสามารถพร้อมเปิดแยกอยู่ใน [PROJECT_OVERVIEW.html](PROJECT_OVERVIEW.html).
สร้าง HTML ซ้ำด้วย `python scripts/build_project_overview.py` จาก template และรายการความสามารถ.
ในแอปเปิด `/project-overview` หรือดาวน์โหลดที่ `/project-overview/download`.
HTML รวม CSS/JavaScript ในไฟล์เดียว ไม่มี CDN และค้นหา/กรองสถานะ/เรียงชื่อได้.

## รูปแบบปุ่ม

| หน้าที่ | ลักษณะ |
| --- | --- |
| เริ่มประมวลผล / ยืนยันบันทึก | สีเขียว พร้อมข้อความและไอคอน |
| เปิดหน้าอื่น / เปิด Model | สีฟ้า พร้อมไอคอนบอกปลายทาง |
| ดาวน์โหลด PLY / SPZ / GLB / ZIP | สีเหลือง พร้อมลูกศรดาวน์โหลด |
| ลบ / ยืนยันลบจุด | สีแดง พร้อมถังขยะ |
| ตั้งค่า / ปรับการแสดงผล | สีกลาง; สีสถานะแสดงเมื่อเปิด/เลือก |

SVG เป็นส่วนตกแต่ง (`aria-hidden`) ชื่อปุ่มยังอ่านได้จากข้อความ/aria-label.
ไอคอนที่เพิ่มไม่เปลี่ยน event listener และเติมกลับได้เมื่อ polling เปลี่ยนข้อความปุ่ม.
เมนูเพิ่มเติมแยก เปิดหน้าอื่น / ดาวน์โหลด / จัดการ / ลบรายการ และวางภายใน viewport.
ย้าย DOM เดิมแทนสร้างปุ่มใหม่ เพื่อรักษา state และ listener.

## ตรวจและปรับตามหน้า

| หน้า | การปรับ |
| --- | --- |
| Dashboard | งานหลักเริ่มที่เพิ่ม Source; แยกทางลัดเปิด Sources และคู่มือ |
| Sources / Jobs / Models / SPZ | จำกัดปุ่มเด่นต่อรายการ เก็บเครื่องมือรองในเมนู; ค้นหา/เรียงชื่อ เวลา ขนาด; URL เก็บตัวกรอง |
| Source preview | คงวิดีโอต้นฉบับและ progress; ค้นหา/เรียงงานและ Models แยกชุด |
| Job detail | แยก สร้าง / เปิดดู / ตั้งค่า; ยุบไฟล์เทคนิค; ค้นหาประวัติโดยรักษาหัวกลุ่ม Views |
| Gallery / Model inputs | ค้นหาชื่อ/เฟรม/มุมทั้งชุดก่อนแบ่ง 48 ภาพ; เรียงเฟรมหรือชื่อ; pagination คงตัวกรอง |
| Camera Preview | แยกเครื่องมือมุมมองกับวงเลือก; ปุ่ม Train และลิงก์ Model ต่างจากกัน |
| Preparation / Mask | ปุ่มสร้าง Model เป็นงานหลัก; ลิงก์กลับ Views และ Mask แสดงเป็นการนำทาง |
| Model viewer | ย้ายปุ่มซ้ำ Walk/Free ไปเพิ่มเติม; Export อยู่ใน Inspector; จอยแยกหมวด; เปลี่ยน breakpoint แล้วปิด Inspector บนมือถือ |
| Crop / brush / lasso | ไอคอนวาด เลือก ลบ Undo/Redo และ Save; ตัดปุ่มกรอบแนะนำที่ทำงานซ้ำกับคืนค่าเริ่มต้น |
| Quality | แยกภาพอ้างอิง ขนาดภาพ และลิงก์เปิด Model มุมเดียวกัน |
| Compression / GLB / ZIP | สีดาวน์โหลดสม่ำเสมอ; ยุบคำแนะนำแยกไฟล์สำหรับนักพัฒนา; ซ่อนปุ่มเตรียมเมื่อพร้อมแล้ว |
| Tutorial | ค้นหาเนื้อหาและหัวข้อ โดยคงลำดับการเรียน |
| Experiments | ค้นหา/เรียงผลทดสอบใน batch; ไม่เริ่ม training จากการใช้ตัวกรอง |
| Benchmarks | ค้นหาแถวและเรียงตามคอลัมน์; แยกค่าประมาณกับผลวัดตามข้อความเดิม |
| Project overview | HTML รวม 45 ความสามารถ สถานะ ข้อจำกัด ทางเข้าฟีเจอร์ และลิงก์แอปปรับพอร์ตได้ |

## งานเดิม: ตรวจสำเร็จถึงระดับไหน

| งาน | ผลตรวจ / ขอบเขต |
| --- | --- |
| ชื่อไม่ชน, upload progress, ยกเลิก/ลองใหม่ | มีโค้ดและ regression; รอบนี้คง flow/form เดิม |
| GPU Views / COLMAP / masks / Brush | มี backend dispatch และ fallback tests; ไม่ได้ทดสอบ RTX/CUDA จริงหรือ Ubuntu เพิ่มในรอบนี้ |
| คิวกลาง GPU | มี OS lock/FIFO ข้าม job/server; ใช้คิวอนุกรม ไม่ใช่ scheduler แบ่ง VRAM หรือกระจายหลาย GPU |
| ETA Views/Model | มี estimator และ regression; เวลาเป็นการประมาณตามข้อมูล ไม่รับประกันเวลาจบ |
| Video Play และ config links | มี regression และเปิดหน้าจริง; ไม่เริ่มแปลงวิดีโอขนาดใหญ่ซ้ำเพื่อทดสอบ UI รอบนี้ |
| Quality gate / split ตามตำแหน่ง | โค้ดและ tests มีแล้ว ใช้กับรุ่นใหม่; ไม่เปลี่ยน scores/split ของรุ่นเก่าย้อนหลัง |
| Preview Mask / blur/duplicate / PNG 2048 | มีหน้าเตรียมและ tests; ผลความคมขึ้นอยู่กับฉาก ไม่อ้างว่าค่าที่สูงกว่าดีเสมอ |
| ภาพเทียบมุมเดียวกัน | มีหน้า Quality และลิงก์เว็บ PLY/รุ่นโหลดเร็ว; ตรวจผ่าน Chromium |
| Brush/lasso/Undo/Redo | มี regression และหลักฐาน DJI ก่อนหน้า; รอบนี้ตรวจว่าไอคอนและการจัดกลุ่มไม่ทำให้ Editor เสีย |
| GLB / ZIP | มี format/property/integrity tests; ZIP ออฟไลน์ผ่าน Chromium ในรอบก่อน |
| รุ่นแก้ไขในประวัติ | รอบนี้แก้ให้ไม่ poll สถานะ/Render ของ parent run และไม่แสดงคะแนนต้นทางเป็นผลหลังตัด |
| OBJ, triangle mesh, collision, แยกชั้น/เดินตามบันได | ยังไม่มี แสดงเป็นข้อจำกัดใน HTML |

## การตรวจสอบรอบนี้

Chromium แยกจาก browser profile ของผู้ใช้ ตรวจ 19 หน้าทั้ง desktop 1440×1000
และ mobile 390×844. ตรวจสี/ไอคอน เมนู ค้นหา/เรียง URL restore Gallery pagination
ประวัติรุ่นแก้ไข Inspector ตาม breakpoint ตาราง Benchmarks Tutorial และ HTML download.
ไม่มีการเริ่ม/หยุด training หรือแก้ข้อมูลต้นฉบับเพื่อทดสอบ UI.

หลักฐานใน workspace: `web_data/capture_review/ui_review_20261017/` (ไม่เข้า Git).
มี inventory ก่อน/หลัง, screenshots, interactions.json และ Python regression log.
Browser in-app ของ session ไม่พร้อม จึงใช้ Chromium แยกตามวิธีที่ผู้ใช้อนุญาตไว้ก่อนหน้า.

ตรวจ backend: `python -m unittest discover -s tests -p "test_*.py"`.
ตรวจ UI scripts: รันทุกไฟล์ `tests/*.mjs` ด้วย Node.js.
ผลสุดท้าย: **Python 213 tests ผ่าน (ข้าม GPU opt-in 1), JavaScript 18 ไฟล์ผ่าน**.
Chromium ตรวจทั้ง 19 หน้าไม่มี page error หรือ horizontal overflow ใน viewport ที่ทดสอบ;
HTML เปิดตรงจาก disk บนมือถือโดยไม่มีคำขอ HTTP ภายนอก และดาวน์โหลดจากพอร์ต 8007 ได้.
Tests ใหม่เน้นการค้นหาข้ามหน้า ชุดภาพ immutable, stable ordering และไม่ลิงก์ประเมิน parent
ให้รุ่นหลังตัด. ตรวจ staging ว่าไม่รวม `web_data/`, models หรือ screenshot ขนาดใหญ่.
