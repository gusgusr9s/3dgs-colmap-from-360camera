# Installation helpers

เรียกผ่าน `app_1_install.ps1` / `app_1_install.sh` ที่โฟลเดอร์หลักเท่านั้นสำหรับการติดตั้งทั่วไป
เปิดแอปด้วย `app_2_run.ps1` / `app_2_run.sh`

| Helper | หน้าที่ |
|---|---|
| `windows.ps1` / `ubuntu.sh` | จัดลำดับ prerequisite → venv → tools → assets → Verify |
| `manage.py` | กันติดตั้งระหว่างมี task, ติดตั้ง Python libraries/weights, ตรวจ readiness |
| `colmap.ps1` | Windows COLMAP CPU/CUDA archive พร้อม SHA-256 |
| `brush.ps1` / `brush.sh` | Brush archive พร้อม SHA-256 |
| `node.sh` | Ubuntu Node.js LTS แบบ local เมื่อ system Node เก่าหรือไม่มี |
| `smoke_app.py` | HTTP smoke test ด้วยพอร์ตสุ่มและฐานข้อมูล/โฟลเดอร์ชั่วคราว ไม่มี training |

`requirements.txt` ตรึง NumPy 1.26.4 และ OpenCV headless 4.10.0.84 เพื่อให้เข้ากับ
Nerfstudio 1.1.5 / nuscenes-devkit ซึ่งกำหนด NumPy <2; ไม่ติดตั้ง cv2 สอง providers ซ้อนกัน
Torch/torchvision/torchaudio ตรึงเป็น 2.7.1/0.22.1/2.7.1 (CPU หรือ cu128) ผ่าน constraints
ก่อน resolve libraries อื่น ไม่แก้ requirements หรือ versions ของ environment ที่กำลังรันอยู่

แหล่งเวอร์ชันและการติดตั้ง:
[PyTorch](https://pytorch.org/get-started/previous-versions/),
[COLMAP 4.2.1](https://github.com/colmap/colmap/releases/tag/4.2.1),
[COLMAP Ubuntu prerequisites](https://github.com/colmap/colmap/blob/4.2.1/doc/install.rst),
[Nerfstudio dependencies](https://github.com/nerfstudio-project/nerfstudio/blob/v1.1.5/pyproject.toml),
[nuscenes-devkit metadata](https://pypi.org/pypi/nuscenes-devkit/1.2.0/json),
[Node.js checksums](https://nodejs.org/dist/v24.22.0/SHASUMS256.txt),
[NVIDIA CUDA package installation](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/)
