# ผลทดสอบ GPU สำหรับ Views และ masks

เครื่องทดสอบ: Windows · AMD Radeon RX 6700 XT · DJI 8K 7680×3840

Views: ช่วงคลิป 2.7 วินาที, 21 ตำแหน่ง × 16 มุม = 336 ภาพ, ทำซ้ำ 3 รอบ ใช้ค่ามัธยฐาน รวม decode, projection, GPU upload/readback, JPEG, disk และ startup ของ worker.

| Views | CPU | GPU WGPU/Vulkan | เวลาลดลง | ความต่างพิกเซลก่อน JPEG |
| --- | ---: | ---: | ---: | --- |
| 1024×1024 | 12.104s | 8.003s | 33.88% | max 0 / mean 0.0000 |
| 1536×1536 | 22.558s | 13.250s | 41.26% | max 0 / mean 0.0000 |

Masks ใช้ weights LRASPP เดิมผ่าน DirectML ไม่เปลี่ยนโมเดล/threshold/postprocessing. เวลาในตารางนี้วัด inference หลัง warmup บน batch 4; ไม่รวม decode, export ONNX ครั้งแรก หรือเขียน PNG.

| ชุดภาพ | Torch CPU | DirectML | เวลาลดลง | IoU ที่ threshold คนเดิม |
| --- | ---: | ---: | ---: | ---: |
| 1024 | 0.3006s | 0.0841s | 72.03% | 1.000000 |
| 1536 | 0.2893s | 0.0962s | 66.76% | 0.999945 |

## Pipeline จริงจน export Model

Job ทดสอบใหม่ `ea737c188c`: WGPU Views → COLMAP CPU → DirectML masks → Brush Vulkan → ประเมินผล/export PLY สำเร็จ.
หา poses ได้ 27/27 ตำแหน่ง · 216 Views · 2,001 steps · masked PSNR 27.573 dB · 10,324 splats.

รอบ smoke 201 steps แรกถูก quality gate ปฏิเสธที่ PSNR 15.95 dB ตามเกณฑ์เดิม 18 dB; ใช้ poses/dataset ของ job ทดสอบเดิมแล้วสร้าง model run ใหม่ 2,001 steps ผ่านเกณฑ์ โดยไม่ลดเกณฑ์ production.

Original High model SHA256 ยังเป็น `a388a8b847ac1e378a231a53401c3b4e6e2dbe1d681f02ccd3689475a02cf1ba`.

## การตรวจและข้อจำกัด

- ทดสอบ CPU regression, GPU seams/up/down, memory batch reduction, driver/native crashes, backend retries, atomic masks, upload/names, ETA, Tutorial และ GPU/Cloud comparison.
- หน้า Tutorial ตรวจผ่าน HTTP และ DOM; เซสชันนี้ไม่มี browser เชื่อมต่อสำหรับตรวจภาพ layout จริง.
- RTX/Ubuntu มี dispatch/installation checks แต่ยังไม่มีผลทดสอบ GPU บนเครื่องจริง จึงไม่อ้างผลความเร็วจาก AMD ไปยัง RTX.
- ตัวเลขเป็นผลของคลิปและขนาดที่ทดสอบ ไม่ได้หมายความว่าเวลารวม Model ทั้งงานลดลงในเปอร์เซ็นต์เดียวกัน.

## รันทดสอบซ้ำ

```powershell
.\.venv-win\Scripts\python.exe scripts/check_gpu_pipeline.py --source web_data/uploads/your-360.mp4 --output web_data/capture_review/gpu-report.json --resolution 1536
$env:RUN_GPU_TESTS="1"; $env:VIEW_DEVICE="cpu"; $env:MASK_DEVICE="cpu"
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

รัน hardware tests/benchmark เมื่อไม่มีงาน GPU อื่น และอย่าแก้ค่าของ job ที่ใช้งานจริงเพื่อทดสอบ.
