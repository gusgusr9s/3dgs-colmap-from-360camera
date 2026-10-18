# COLMAP bottleneck / recovery — d36645f4d5

ตรวจวันที่ 17 ตุลาคม 2026 จาก log, process จริง, SQLite แบบ read-only และ sparse model
run เดิม `20261017_195348_62826560`; run กู้ต่อ `20261017_225629_549aba99`

## ข้อค้นพบจากรอบเดิม

| ขั้นตอน | เวลาที่ตรวจได้ |
|---|---:|
| สร้าง Views บน RX 6700 XT / WGPU Vulkan | 7 นาที 4 วินาที |
| เตรียมข้อมูลก่อน COLMAP | 3 นาที 52 วินาที |
| SIFT features บน CPU (8 threads ในโค้ดที่รันเดิม) | 4 นาที 36 วินาที |
| Sequential matching บน CPU | 32 นาที 39 วินาที |
| Mapper จนถูก interrupt | 125 นาที 10 วินาที |
| Mask / Brush Train | ยังไม่เริ่มในรอบเดิม |

- Source 7680×3840, 30 FPS, 163.9 วินาที; เก็บทุก 4 เฟรม = 1,230 ตำแหน่ง / 19,680 perspective Views
- COLMAP ใช้ **1,230 panorama ขนาด 1920×960** ไม่ได้หา pose แยกทั้ง 19,680 Views
- DB ประมาณ 1.83 GB มี 10,302,768 features; 11,208 คู่ภาพที่ mapper โหลดใช้งาน
- พบช่วง `Retriangulation and Global bundle adjustment` 53 รอบ ใช้เวลารวมประมาณ 88% ของ mapper
  โดยวัดจากข้อความเริ่มจนกลับมาลงทะเบียนภาพ/ถูก interrupt; ไม่ใช่ profiling ภายใน solver
- พบ `Linear solver failure / Matrix not positive definite` 229 ข้อความ
  บางรอบยังผ่านแล้วเพิ่มกล้องต่อได้; Global BA รอบยาวสุดที่วัดได้ประมาณ 21 นาที 33 วินาที
- ขณะติด Global BA sampled CPU ประมาณ 1 core แม้มี 28 threads; RAM process ประมาณ 3.9 GB
  และ RAM เครื่องยังเหลือราว 10.7 GB จึงไม่มีหลักฐานว่า RAM เต็มในช่วงที่ตรวจ
- เซิร์ฟเวอร์ review หลายพอร์ตที่พบมี CPU delta 0 ใน sample เดียวกัน ไม่ใช่หลาย job Train แย่ง GPU

คำเตือนหมายถึง solver คำนวณบาง step ไม่สำเร็จ ไม่ได้พิสูจน์ว่า camera path เสียทั้งหมด
กราฟขนาดใหญ่/ภาพซ้ำซ้อนและปัญหาที่มี conditioning ไม่ดีเป็นสาเหตุที่ควรตรวจเพิ่ม
ยังระบุไม่ได้จาก log อย่างเดียวว่าเกิดจากภาพเบลอ กระจก คน หรือจุด 3D กลุ่มใด

## ผลหลัง Terminal ถูก interrupt

พอร์ต 8000 และ COLMAP หยุดแล้วตอนตรวจ 22:40 น. Log ระบุ:

```text
Keeping reconstruction due to interrupt
Graceful shutdown completed after receiving signal 2
```

COLMAP บันทึกไว้ใน `web_data/jobs/d36645f4d5/spherical_colmap/sparse/0/`:

- 879 registered cameras/frames และ 318,009 sparse points
- 3,445,403 observations; mean track length 10.83
- mean reprojection error **0.529 px**, สูงสุดที่ตรวจประมาณ 3.98 px
- ไม่พบ camera center / point coordinate ที่เป็น NaN หรือ Inf และไม่มี track สั้นกว่า 2 observations

counter ในข้อความ `Registering image ... num_reg_frames=878` เป็นจำนวนก่อนลงทะเบียนภาพนั้น
ไฟล์ที่บันทึกจริงจึงมี 879 ไม่ใช่ 878
คะแนน reprojection ต่ำยังไม่รับรอง geometry ทั้งฉากหรือคุณภาพจากมุมใหม่ ต้องตรวจ Preview และ held-out renders ต่อ

## สิ่งที่แก้แล้ว

1. **Resume COLMAP จาก saved model** ผ่านปุ่ม “ทำต่อจาก Camera Pose ที่บันทึกไว้” บนหน้า Job
   ใช้ run ID ใหม่ เก็บ run ก่อนหน้าไว้ พร้อมบันทึกว่าถูก interrupt และถูกกู้โดย run ใด
2. **ไม่ใช้ DB/model ต้นฉบับเป็น output**: ตรวจ view-run/signature, source fingerprint ผ่าน guard เดิม,
   frame names และ image IDs, features/matches, camera model และข้อมูลตัวเลขก่อนเริ่ม
   ใช้ SQLite backup และสำเนา sparse model ใน `model_runs/<run>/recovery/`
3. ข้าม decode panorama, feature extraction และ matching ที่เสร็จแล้วในรอบกู้
4. งาน panorama ใช้ Views เดิมโดยตรง ไม่คัดลอก 19,680 ภาพไป `_input_images` ที่ไม่ได้ใช้ในเส้นทาง spherical
5. เปิด **snapshot ทุก 50 registered frames** สำหรับการรัน mapper ใหม่; saved snapshot อยู่ใน recovery run
6. โหมดกู้ `balanced`: เพิ่ม global BA frame/point growth ratio จาก 1.1 เป็น 1.2
   และตั้ง global function tolerance `1e-6` ให้หยุดเมื่อ cost เปลี่ยนน้อย
   ไม่ตัดจุดเดิม ไม่ลดจำนวนกล้อง ไม่ลด Ceres iteration cap และคง final BA/quality gates เดิม
7. Fresh spherical restart เก็บ work directory เก่าเป็น `.archive-<id>` แทนการลบ checkpoint
8. หน้า progress/console ตรวจ owner ที่ตายแล้ว ไม่แสดง `running` จาก status JSON เก่าต่อไป

การเปลี่ยน BA ratios/tolerance เป็น tradeoff ที่ต้องวัดคุณภาพ ไม่ใช่การพิสูจน์ว่าแก้ numerical warnings ได้หมด
ตอนเริ่มกู้ 22:56 น. ใช้ features ครบ 1,230 ภาพและ matched pairs 11,208 คู่เดิม
ถึง 23:00 น. log counter เพิ่มเป็น 1,015 และมี snapshot ใหม่ที่อ่านได้จริง 979 กล้อง
ช่วงที่ตรวจยังไม่มี matrix warning แต่ยังไม่ควรสรุปผล Global BA รอบถัดไป

## ลดเวลาได้อีกอย่างไร

| ลำดับ | แนวทาง | เงื่อนไข/ผลกระทบ |
|---|---|---|
| 1 | เลือก keyframe ตามการเคลื่อนและภาพคม ลดภาพเกือบซ้ำ | ยังต้องรักษา overlap และทางเชื่อมระหว่างห้อง/ชั้น |
| 2 | ทดลอง sample ทุก 6 / 8 เฟรมกับช่วงเดิม | ประมาณ 820 / 615 ตำแหน่ง แทน 1,230; ต้องเทียบ pose coverage/held-out renders ไม่รับประกันเวลาลดตามสัดส่วน |
| 3 | เปรียบเทียบ feature cap 12,000 → 8,000 และ matching overlap | ลดกราฟ BA แต่เสี่ยงเสีย correspondences; ทดลองใน job แยก |
| 4 | RTX CUDA สำหรับ extraction/matching และ Masks | ช่วยขั้นเหล่านี้; ไม่ได้เปิด GPU BA ให้เอง |
| 5 | สร้าง Ceres CUDA/cuDSS และ benchmark BA ของ spherical input | ต้องตรวจ build/backend จริงและคุณภาพ ไม่ควรเปิดแฟล็กจากชื่อ RTX อย่างเดียว |
| 6 | Cache และ checkpoint รายขั้นสำหรับ Views/Mask/Train | ต้องมี input/config/model signature และบันทึกไฟล์แบบไม่เสียหายเมื่อแอปดับ |

ลด resolution ของ **perspective Views** อย่างเดียวไม่ลดขนาด panorama 1920×960 ที่ COLMAP ใช้อยู่
และเพิ่ม CPU threads ไม่รับรองว่า sparse solver ทุกช่วงจะใช้ครบทุก core
Caspar ใน COLMAP 4.2.1 ยังรองรับเฉพาะ SIMPLE_RADIAL/PINHOLE จึงไม่ควรสลับใช้กับ EQUIRECTANGULAR ชุดนี้

## Stop / Continue: สิ่งที่มีจริงและงานถัดไป

อัปเดตต่อจากรายงานนี้: เพิ่มปุ่มหยุด Model, native supervisor, features/matches stage cache
และ Brush weight checkpoints แล้ว ดูขอบเขตล่าสุดใน [STOP_CONTINUE.md](STOP_CONTINUE.md)
ตารางด้านล่างเป็นสถานะ ณ รอบกู้ `20261017_225629_549aba99` ซึ่งเริ่มก่อนอัปเดตนี้

| ช่วง | ตอนนี้ | สิ่งที่ควรทำเพิ่ม |
|---|---|---|
| รอคิว | ยกเลิกการรอได้ | — |
| COLMAP features/matches เสร็จแล้ว | กู้ด้วย DB เดิมที่ตรวจแล้วได้ | Stage cache ที่บันทึกครบ/ไม่ครบแยกชัดเจน |
| COLMAP mapper | Resume จาก sparse/snapshot ด้วย `--input_path` ได้ | ปุ่มหยุดอย่างสุภาพพร้อม native signal handling ที่ทดสอบทั้ง Windows/Ubuntu |
| ปิด Terminal แบบ graceful | รอบนี้ COLMAP เซฟ reconstruction ได้จริง | อย่ารับรองว่า force-kill/ไฟดับจะเซฟได้; ใช้ snapshot ล่าสุดที่อ่านได้ |
| Views/Mask ทำค้าง | ยังไม่มี Pause/Continue ครบทุกขั้น | เก็บ completion index + signature แล้วทำเฉพาะไฟล์ที่ยังไม่สมบูรณ์ |
| Brush Train | คำสั่งปัจจุบัน export ตอนจบ; Fine tune ใช้ PLY ของรุ่นที่สร้างสำเร็จแล้ว | Periodic exports และการกู้จากไฟล์ที่ตรวจแล้ว; full checkpoint ต้องรวม optimizer/scheduler/RNG |

การเริ่ม Brush จาก PLY เป็น warm start ไม่ใช่ exact resume ของ training state เดิม
ปุ่มพัก **Console** พักการแสดงข้อความเท่านั้น ไม่ได้หยุด process
รอบกู้เปิดเซิร์ฟเวอร์แยกจาก Terminal เดิมแล้ว แต่ยังไม่ใช่ service ที่มีระบบ restart/recovery อัตโนมัติ

## Verification

- Python regression 257 tests ผ่าน (GPU opt-in skipped 1); JavaScript 18 ชุดผ่าน
- ชุด recovery อ่าน sparse fixture จริง ตรวจ DB copy, ID/signature mismatch, unsafe paths,
  ไม่รัน features/matching ซ้ำ, คง quality threshold และรักษา original files
- Chromium เปิดหน้า Job ตรวจสถานะ interrupted แล้วกด recovery จริงสำเร็จ ไม่มี page error
- เริ่ม mapper จริงใน run ใหม่จาก 879 กล้องได้ และบันทึก snapshot ใหม่ 929 / 979 กล้องแล้ว
  ต้องติดตามจน pose/Mask/Train/evaluation จบก่อนรับรอง Model

หลักฐาน: `web_data/capture_review/colmap_recovery_tests_final.log`,
`web_data/capture_review/colmap_recovery_20261017/` และ log ของแต่ละ Model run

อ้างอิง:
[COLMAP BA performance / GPU / Caspar limits](https://colmap.github.io/faq.html#speedup-bundle-adjustment),
[COLMAP continue mapper](https://colmap.github.io/faq.html#register-localize-new-images-into-an-existing-reconstruction),
[COLMAP 4.2.1 interrupt/snapshot implementation](https://github.com/colmap/colmap/blob/4.2.1/src/colmap/controllers/incremental_pipeline.cc),
[Brush 0.3 training initializes a new trainer](https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-process/src/train_stream.rs)
