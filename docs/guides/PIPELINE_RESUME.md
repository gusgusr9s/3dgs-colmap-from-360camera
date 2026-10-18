# ทำต่อจากขั้นล่าสุดและลดเวลาเตรียมภาพ

ใช้โค้ดรุ่นนี้หลังเปิดแอปครั้งถัดไป การแก้ไฟล์ไม่เปลี่ยน worker ที่กำลังรันอยู่
จึงไม่ต้อง restart หรือตัดงาน Model ปัจจุบันเพื่อพัฒนาและทดสอบ

## ปุ่มเดียว

เมื่อ Model หยุดหรือเกิดข้อผิดพลาด ให้กด **ทำต่อ** ในหน้า Jobs หรือรายละเอียด Job
ระบบตรวจข้อมูลก่อนเริ่ม run ใหม่ และเลือกตามลำดับนี้:

1. Training checkpoint ล่าสุดที่อ่านได้ครบ ใช้ dataset, Masks และชุดประเมินของรุ่นนั้น
2. Dataset พร้อมเทรนที่เก็บไว้แล้ว ถ้ายังไม่มี checkpoint ใช้ initialization เดิม
3. ภาพที่เตรียมพร้อม Camera Poses และ Masks ที่ทำครบแล้ว ข้าม COLMAP และการเตรียมภาพ
4. COLMAP features/matches/sparse snapshot ที่ตรวจใช้ได้ โดยไม่เขียนทับฐานข้อมูลเก่า

ตรวจ Views revision, profile, ตัวเลือก Train และสาย run ที่กู้ต่อกัน
ไม่เลือก checkpoint จากงานอื่นหรือชุดภาพคนละรุ่น หากข้อมูลเปลี่ยนจนกู้ไม่ได้จะแจ้งสาเหตุ
ขั้นที่เลือกและเหตุผลที่ข้าม checkpoint บันทึกใน `continuation` ของ status
และ event `continuation_selected` ใน Console/process log

ชุดภาพเดิมที่ยังไม่มี completion receipt ใช้การตรวจ preview signature, ลำดับภาพ,
เลขเฟรม, เมทริกซ์กล้อง, ไฟล์ภาพ/seed และเวลาแก้ไข Source เทียบภาพที่เตรียมไว้
ส่วนชุดใหม่มี `dataset_complete.json` ตรวจลายเซ็น input/metadata และ file stamps เพิ่ม
ชุด Masks ต้องมีไฟล์ครบทุกภาพและผลสำเร็จจาก worker จึงข้ามขั้นได้

ปุ่ม **สร้าง Model ใหม่** ยังใช้เมื่อต้องการเริ่มรุ่นใหม่หรือเปลี่ยนตัวเลือก
ดูรายละเอียดการหยุดและข้อจำกัดใน [STOP_CONTINUE.md](STOP_CONTINUE.md)

## เตรียม dataset

- รักษาวิธีอ่านภาพและ JPEG quality 95 เดิม เพื่อคงพิกเซลที่ใช้เทรน
- ใช้ worker สูงสุด 8 ตัว จำกัดด้วย CPU budget และประมาณการ buffer ภาพ 256 MiB
  มีงานรอสูงสุดสองเท่าของ worker; เปลี่ยนด้วย `DATASET_WORKERS` เช่น `4`
- เก็บผล JPEG ตาม hash ของ Source และสูตร OpenCV ใน `.dataset_cache/jpeg95/`
  ตรวจ checksum ก่อนใช้ซ้ำ และใช้ hardlink หรือ copy เมื่อ filesystem ไม่รองรับ
- ไม่ decode/encode ซ้ำเมื่อผลเดิมยังใช้ได้ รวมถึงหลังหยุดกลางขั้นเตรียมภาพ
- สร้างใน staging directory ก่อนสลับชุดที่เสร็จแล้วเข้าที่ ชุดครึ่งเดียวไม่แทนชุดเดิม
- เก็บลำดับภาพ, matrices, intrinsics และ sparse seeds ตามเดิม
  รายงาน workers/cache hits/จำนวน encode/เวลาใน quality และ `3d.log`

Cache ไม่ได้สร้างรายละเอียดใหม่หรือเปลี่ยน JPEG เป็น lossless
ครั้งแรกยัง encode ตามวิธีเดิม การทดลอง PNG จากต้นฉบับเป็นคนละตัวเลือก
การตรวจ reuse ใช้ timestamps/ขนาดไฟล์ร่วมกับลายเซ็น metadata;
ไม่ได้อ้างว่าตรวจจับการแก้ไฟล์แบบจงใจคงทั้งขนาดและ timestamp ได้ทั้งหมด

## ผลวัด DJI จริง — 18 ต.ค. 2026

ทดสอบ Views ของ `d36645f4d5` จำนวน 64 ภาพ ขนาด 1536×1536:

| วิธี | เวลา | Encode | Cache hit |
|---|---:|---:|---:|
| เรียงทีละภาพ | 3.336 วินาที | 64 | 0 |
| ขนาน 4 workers | 0.920 วินาที | 64 | 0 |
| ใช้ cache, 4 workers | 0.250 วินาที | 0 | 64 |

ขนานเร็วขึ้น 3.63 เท่า และ cache 13.32 เท่าในตัวอย่างนี้
ตรวจ SHA-256 ผล JPEG ตรงกันทั้งสามวิธี และ Source ไม่เปลี่ยน
เทียบ workers 1 กับ 4 ในโค้ดเตรียมภาพชุดเดียวกัน ไม่ใช่เวลารวมของแอปรุ่นเก่า
ไม่ได้ล้าง OS disk cache และมีงานอื่นทำงานอยู่ จึงไม่ใช่เวลาที่รับรองสำหรับทั้ง job

```powershell
.\.venv-win\Scripts\python.exe scripts/benchmark_dataset_preparation.py --source web_data/jobs/d36645f4d5 --output web_data/capture_review/dataset_benchmark_new --samples 64 --workers 4
```

หลักฐาน: `web_data/capture_review/dataset_benchmark_20261018/result.json`

## การทดสอบ

- Python regression 284 tests ผ่าน (skip GPU opt-in 1) ก่อนเพิ่มกรณีตรวจเพิ่มเติม
  จากนั้นตรวจชุด dataset progress/poses 8, resume 6 และ COLMAP recovery 10 ผ่าน
- JavaScript 19 ชุดผ่าน
- Chromium ในเซิร์ฟเวอร์ทดสอบแยก: ยืนยัน Stop → native CPU fixture เซฟและออก
  → กด **ทำต่อ** ปุ่มเดียว → เลือก checkpoint 5,000 → ผ่าน evaluation 3 ภาพ
  ส่วนผล Brush ใน browser test จำลอง และไม่มี page error
- ตรวจหน้า Jobs และ Job ที่เสร็จแล้วผ่าน Chromium บนแอปรุ่นใหม่ที่พอร์ต 8000
  รวมปุ่มทำต่อบน Jobs และหน้าจอแคบ โดยไม่ส่ง action ให้งานจริง
- เปรียบเทียบภาพแบบขนานกับ JPEG เดิมทุกไบต์ รวม transforms/เมทริกซ์/ค่าเลนส์และ seed
- ตรวจ cache เสีย, Source เปลี่ยน, metadata หาย, Stop กลางขั้น, ลำดับภาพ และ
  การใช้ tracks จาก recovered sparse root โดยต้องตรงกับตำแหน่งกล้อง
- ตรวจ live Mask progress ให้ใช้ค่าจาก callback ระหว่างที่ Masks ยังอยู่ใน staging

หลักฐาน UI: `web_data/capture_review/auto_resume_20261018/`

## ทดสอบ Stop/Continue บน GPU จริง

ใช้ `scripts/check_gpu_resume.py` กับสำเนาชุดข้อมูลขนาดเล็กจาก Model ที่มีอยู่
สคริปต์เข้าคิวกลางเดียวกับแอป จึงรอ Model ปัจจุบันออกก่อนใช้ GPU
ไม่หยุดงานผู้ใช้และไม่เผยแพร่ Model ทดลองในรายการจริง

```powershell
.\.venv-win\Scripts\python.exe scripts/check_gpu_resume.py --model <existing-model.ply> --output web_data/capture_review/gpu_resume_new
```

เปรียบเทียบ Train ต่อเนื่องกับหยุดหลังมี checkpoint แล้วทำต่อจนเป้าเดียวกัน
ใช้ initialization, ภาพ, Mask และ held-out split ชุดเดียวกัน ตรวจ GPU ที่ Brush เลือกจริง,
ไฟล์ checkpoint, การเปลี่ยน weights, PSNR และ Source ไม่เปลี่ยน
รายงาน `quality_within_1db` แยกจากความสำเร็จของการ Stop/Continue

**ผลรอบนี้:** ผ่านบน AMD Radeon RX 6700 XT / Vulkan หลังงานหลักจบ
ใช้ fixture fine tune 128 px, 16 ภาพ Train / 3 ภาพประเมิน, 4,000 steps
หยุดจริงหลัง checkpoint 500 แล้วเริ่ม native process ใหม่ด้วย `--start-iter 500`

| แบบ | ภาพประเมินที่ใช้ได้ | Masked PSNR เฉลี่ย |
|---|---:|---:|
| Train ต่อเนื่อง | 3/3 | 27.780 dB |
| Stop → Continue | 3/3 | 27.692 dB |

ต่าง −0.088 dB อยู่ในเกณฑ์ทดลอง ±1 dB; valid pixels เฉลี่ย 97.73% เท่ากัน
weights เปลี่ยนจริง ไม่ใช่การหยุด learning rate และ Source checkpoint ไม่เปลี่ยน
ดู `web_data/capture_review/gpu_resume_20261018/result.json` พร้อม logs/ภาพ Render

ผลนี้ยืนยันเส้นทาง Stop/Continue จริงและคุณภาพใกล้กันใน fixture นี้
ไม่ใช่การรับรอง RTX/Ubuntu หรือ Model เต็มทุกฉาก
Brush กู้ weights และ iteration ได้ แต่ optimizer/RNG เริ่มใหม่
