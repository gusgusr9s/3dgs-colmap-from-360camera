# Ubuntu + NVIDIA RTX

ใช้ **Ubuntu 24.04 x86_64 / Python 3.12** สำหรับ dependency ชุดนี้
สคริปต์ติดตั้ง COLMAP 4.2.1 จาก source ลง `tools/colmap`, Brush 0.3.0
พร้อมตรวจ SHA-256, viewer assets และ weights สำหรับ Mask คน
build COLMAP ครั้งแรกอาจใช้เวลานานและ RAM มาก; ค่าเริ่มต้น build พร้อมกัน 2 งาน

## ติดตั้งและรัน

นำ repository มาไว้ในเครื่อง Ubuntu แล้วเปิด Terminal ในโฟลเดอร์โปรเจกต์
หากยังไม่มี NVIDIA driver ให้ติดตั้งก่อน (Ubuntu ที่ลงบนเครื่องจริง):

```bash
sudo apt update
sudo apt install -y ubuntu-drivers-common
sudo ubuntu-drivers install
sudo reboot
```

หลังเปิดเครื่องใหม่ กลับเข้าโฟลเดอร์โปรเจกต์แล้วรัน:

```bash
bash app_1_install.sh --cuda
bash app_2_run.sh --port 8000 --colmap-device cuda --mask-device cuda
```

เปิด `http://127.0.0.1:8000` (หากพอร์ตถูกใช้อยู่ แอปจะแสดงพอร์ตใหม่ใน Terminal)
ไม่ต้อง activate venv เพราะ `app_2_run.sh` เลือก `.venv` และ PATH ของเครื่องมือให้
setup ใช้ `sudo` เฉพาะติดตั้ง system packages; รัน setup และเว็บด้วยผู้ใช้ปกติ

CUDA Toolkit (`nvcc`) ใช้ **build COLMAP**; CUDA wheel ของ Torch ใช้ **Mask คน**
ส่วน **Brush train Gaussian Splat บน RTX ผ่าน WGPU/Vulkan**.
COLMAP เร่งขั้นตอน SIFT extraction/matching ด้วย CUDA; mapper ยังใช้การตั้งค่าเดิม
ตาม [เอกสาร COLMAP](https://github.com/colmap/colmap/blob/4.2.1/doc/install.rst),
แพ็กเกจ COLMAP จาก apt ไม่มี CUDA จึงต้อง build เอง
Torch ชุดที่ตรึงไว้คือ 2.7.1 / torchvision 0.22.1 / torchaudio 2.7.1,
CUDA 12.8 ตาม [คำสั่งติดตั้ง PyTorch](https://pytorch.org/get-started/previous-versions/).
Brush รองรับ NVIDIA ด้วยเทคโนโลยี WebGPU ตาม [เอกสาร Brush](https://github.com/ArthurBrussee/brush/tree/v0.3.0).

สำหรับ **RTX 50 series** ตัวติดตั้งตรวจ `sm_120` และเตรียม CUDA Toolkit 12.8
จาก NVIDIA repository หาก toolkit เดิมยังไม่รองรับ โดยใช้แพ็กเกจ toolkit-only
ไม่เปลี่ยน display driver; GPU รุ่นก่อนใช้ toolkit ที่มีหรือแพ็กเกจ Ubuntu
หาก compiler ไม่เข้ากับ toolkit ให้ตรวจคำแนะนำ NVIDIA และตั้ง `CUDAHOSTCXX` ก่อน build

## เลือกอุปกรณ์และตรวจปัญหา

```bash
# เลือก CUDA เมื่อ COLMAP build รองรับและ nvidia-smi ใช้งานได้; มิฉะนั้นใช้ CPU
bash app_2_run.sh --colmap-device auto --mask-device auto

# CPU สำหรับ COLMAP/Mask; Brush ยัง train บน GPU
bash app_1_install.sh --cpu
bash app_2_run.sh --colmap-device cpu --mask-device cpu

# เลือกการ์ดสำหรับ COLMAP และ Torch แยกกัน
bash app_2_run.sh --colmap-device cuda --gpu-index 0 --mask-device cuda:0

# ตรวจ installation โดยไม่แก้ไขระบบ
bash app_1_install.sh --check --cuda
```

`--colmap-device cuda` และ `--mask-device cuda` จะรายงานข้อผิดพลาดเมื่อ CUDA
ไม่พร้อม; โหมด `auto` เลือก CPU เมื่อไม่พบความสามารถ CUDA ก่อนเริ่มงาน
หากเลือก CUDA ได้แล้วแต่ขั้นตอนประมวลผลล้มเหลว (เช่น VRAM ไม่พอ)
งานจะรายงาน error ให้ตรวจ log และเลือก CPU หรือปรับข้อมูลก่อนลองอีกครั้ง
Torch CUDA ไม่จำเป็นสำหรับ Brush; เช็ก `compute_device` / `compute_backend`
ในผลงานและ `3d.log` เพื่อยืนยันการ์ดที่ Brush ใช้จริง
`colmap_backend` และ `mask_device` เก็บไว้ใน quality record แยกกัน

ใช้ environment variables ได้ด้วย: `COLMAP_DEVICE`, `COLMAP_GPU_INDEX`,
`MASK_DEVICE`, `COLMAP_BIN` (โฟลเดอร์ bin หรือ executable), `BRUSH_CLI` (executable),
`VENV_DIR` และ `WGPU_BACKEND` (ค่าเริ่มต้น `vulkan` บน Linux)
ตัวเลือก CLI มีลำดับก่อนค่า environment; `CUDA_ARCHITECTURES=native` ใช้ build
สำหรับการ์ดในเครื่องนั้น หากย้าย binary ไปเครื่องอื่นให้ build ใหม่หรือกำหนด
architecture ที่ต้องการตามเอกสาร COLMAP

สำหรับ Ubuntu ที่รันผ่าน SSH ให้ใช้ tunnel จากเครื่องที่จะเปิด browser:

```bash
ssh -L 8000:127.0.0.1:8000 user@ubuntu-host
```

หากใช้ WSL ให้ใช้ driver จาก Windows ตาม
[NVIDIA CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)
และข้ามคำสั่งติดตั้ง Linux NVIDIA driver ด้านบน

## ทดลองไฟล์กล้อง DJI

โปรเจกต์อ่าน MP4/MOV/JPG/PNG ผ่านระบบนำเข้าปัจจุบัน
สำหรับกล้อง 360 ให้ stitch/export เป็น **equirectangular 2:1** ก่อน เช่น
`5760×2880` หรือขนาด 2:1 ของกล้อง; ไฟล์ raw dual-fisheye ยังใช้ตรง ๆ ไม่ได้
หากเป็น DJI Action / Pocket / โดรนที่ถ่ายภาพปกติ ให้เลือก **Perspective**
และใช้วิดีโอที่เดินหรือเคลื่อนกล้องรอบฉากเพื่อให้เกิด parallax
ต้องตรวจไฟล์จริงตามรุ่นกล้องอีกครั้ง

1. เปิด **Sources → Upload** แล้วนำเข้า MP4/MOV ที่ export เสร็จ
2. เลือกช่วงสั้นสำหรับลองครั้งแรกและ preset **Preview**
3. ตรวจ **Camera Preview** ว่าตำแหน่งกล้องและภาพตรงฉากก่อน **Generate 3D**
4. เปิด model และดูมุมใหม่ ตรวจรูปทรงและ quality record พร้อมชื่อ GPU

การทดสอบโค้ดและสคริปต์บนเครื่องพัฒนายังไม่ใช่ผลทดสอบ CUDA บน RTX จริง
ต้องรัน readiness check และ pipeline จากคลิป DJI บนเครื่องเป้าหมายเพื่อยืนยัน

## Views GPU เพิ่มเติม

`app_1_install.sh` ติดตั้ง `requirements-gpu.txt` ซึ่งมี WGPU สำหรับแตก panorama บน Vulkan.
เริ่มด้วย `bash app_2_run.sh --view-device auto --colmap-device auto --mask-device auto`.
หาก Vulkan/device ใช้ไม่ได้ auto กลับ CPU พร้อมเหตุผล; `--view-device cpu` ใช้เส้นทางเดิม หรือ `--view-device wgpu` บังคับตรวจ GPU.
DirectML สำหรับ masks ใช้เฉพาะ Windows; Ubuntu RTX ใช้ Torch CUDA ส่วน Ubuntu AMD ที่ไม่มี CUDA จะใช้ CPU สำหรับ masks.
การตรวจบน Windows AMD ไม่ได้ยืนยัน RTX/Ubuntu: ต้องรัน readiness และ benchmark บนเครื่องเป้าหมายจริงด้วย.
