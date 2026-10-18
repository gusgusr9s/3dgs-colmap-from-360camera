# แผน Desktop installer และระบบอัปเดต

สถานะ: **แผนเท่านั้น** ยังไม่ได้สร้าง Electron, installer หรือ updater ในรอบนี้

เป้าหมายแรกคือ Windows 11 x64: ติดตั้งครั้งเดียว เปิดใช้งานได้จากไอคอน โดยใช้ UI,
Python backend, FFmpeg, COLMAP และ Brush เดิม รุ่น Ubuntu / Docker แยกตามระบบ
และฮาร์ดแวร์หลัง Windows ผ่านการทดสอบแล้ว

## 1. โปรแกรมที่ผู้ใช้เห็น

- Electron เปิดหน้าจอเว็บในหน้าต่างเดียว Launcher เปิด backend ที่ loopback พร้อมพอร์ตว่าง
  รอ health endpoint ก่อนแสดง UI และแสดงข้อผิดพลาดพร้อมปุ่มเปิด log เมื่อเริ่มไม่ได้
- เปิดหน้า Verify เป็นขั้นตอนแรก: Python environment, driver / adapter และ backend แต่ละขั้น
  แยกผลตรวจเบื้องต้นออกจากผล GPU smoke test ที่ผู้ใช้เลือกทำเมื่อเครื่องว่าง
- จัดเก็บ Source, Jobs, checkpoints, Models และ settings ใน workspace ที่ผู้ใช้เลือก
  ซึ่งอยู่นอกโฟลเดอร์ติดตั้ง แสดงพื้นที่ว่างก่อนรับไฟล์ใหญ่
- ตรวจว่าเปิด workspace เดียวกันอยู่แล้วหรือไม่ Launcher ดูแลเฉพาะ process ที่ตนเปิด
  ไม่ปิด Python / COLMAP / Brush ของแอปเดิมที่ผู้ใช้กำลังใช้งาน
- ปิดหน้าต่างขณะสร้าง Model: แจ้งงานที่กำลังทำและให้เลือกเปิดทิ้งเบื้องหลัง
  ไม่บังคับปิด worker เพื่อออกจากหน้าจอ

## 2. Runtime และ engine

- Build Python runtime แยกต่อ OS / architecture ตรวจ dependencies ที่ใช้งานจริง
  และแยกส่วนที่ pipeline ไม่เรียกใช้ หลัง regression tests ยืนยันแล้ว
- Version manifest ระบุ app, backend, schema และ engine ที่ทำงานร่วมกันได้ พร้อม checksum
  ไม่เปลี่ยน engine ที่ job ปัจจุบันกำลังเรียกใช้อยู่
- มีตัวติดตั้งขนาดเล็กที่ดาวน์โหลด engine ตามเครื่อง และชุด offline สำหรับเครื่องไม่มีอินเทอร์เน็ต
- Views: WGPU → CPU ใน auto; Masks: CUDA → DirectML บน Windows → CPU;
  COLMAP: CUDA เมื่อ binary และ driver รองรับ มิฉะนั้น CPU ตาม implementation ปัจจุบัน
- Brush ต้องมี GPU ที่รองรับ WGPU; CPU-only ไม่ได้หมายถึงสามารถ train ทั้ง pipeline ได้
  หากต้องการให้เครื่องไม่มี GPU ใช้ Train ต้องออกแบบ GPU server เป็นอีกงานหนึ่ง
- ใช้ฐาน Chromium เดียวกันทดสอบ Spark / Three.js / GLB, WebGL2 / WebGPU และ input devices

## 3. Patch และ rollback

- ใช้ electron-builder / electron-updater และ Windows NSIS installer;
  เตรียม code signing และลงนาม release metadata / artifacts ตามช่องทางเผยแพร่
- แยกแอป / UI / backend patch ออกจาก engine ขนาดใหญ่ ดาวน์โหลดเฉพาะรุ่นที่เปลี่ยน
  differential download ใช้เมื่อช่องทางและรูปแบบแพ็กเกจรองรับ ไม่รับประกันขนาดทุก patch
- ตรวจ version / checksum / signature และพื้นที่ว่างก่อนติดตั้ง ดาวน์โหลดลง staging
  ถ้าอินเทอร์เน็ตขาดให้ลองต่อได้ โดยยังเปิดเวอร์ชันเดิมได้
- ดาวน์โหลดอัปเดตได้ขณะทำงาน แต่ **ติดตั้งเมื่อไม่มี Upload, Views, Pose, Mask,
  Train, evaluation, export หรือ queue ที่กำลังใช้งาน** ตรวจข้าม process / workspace ที่เกี่ยวข้อง
- แสดงปุ่ม “อัปเดตเมื่อทำงานเสร็จ” พร้อม release notes ผู้ใช้เลื่อนได้ ไม่ restart เองระหว่าง train
- เก็บรุ่นก่อนหน้า ใช้ health check หลังติดตั้ง และ rollback เมื่อเริ่มไม่สำเร็จ
  ถ้า schema เปลี่ยนต้องมี backup / migration ที่ย้อนกลับได้ หรือห้ามย้อน app ไปอ่านข้อมูลใหม่
- ทดสอบ patch จากอย่างน้อยสองรุ่นก่อนหน้า รวม interrupted download, disk full,
  signature ไม่ตรง, update ระหว่าง train และ restart เครื่องหลังติดตั้ง

## 4. ความปลอดภัยของ Desktop

- โหลดเฉพาะ local app ที่ควบคุมไว้, contextIsolation เปิด, nodeIntegration ปิด
  และ preload IPC มีคำสั่งที่อนุญาตชัดเจน
- Backend bind loopback, ใช้ token ของ session และตรวจ origin สำหรับคำขอที่เปลี่ยนข้อมูล
  ไม่ปล่อย API ประมวลผลในเครื่องให้เว็บไซต์ภายนอกเรียกใช้
- ลิงก์ภายนอกเปิด browser ของระบบ, จำกัด path ของ workspace และปิดช่องทาง arbitrary command
- เก็บ signing keys ไว้ในระบบ release; ตรวจ license และ notices ของ engine ที่บรรจุมา

## 5. ลำดับส่งมอบและเกณฑ์ผ่าน

1. ต้นแบบ Launcher + Verify + persistent workspace โดยยังไม่ทำ updater
2. Installer บน Windows เครื่องสะอาด ไม่ต้องมี Python / Node ที่ผู้ใช้ติดตั้งเอง
3. ทดสอบ AMD / NVIDIA / Intel, CPU-only, ไม่มี driver, backend ใช้ไม่ได้,
   พื้นที่ไม่พอ, ชื่อ path ภาษาไทย และไฟล์ 8K ใหญ่ พร้อมรักษางานเดิม
4. Updater และ rollback ตามข้อ 3 หลัง installer และ jobs ผ่าน regression
5. Ubuntu native / Docker แยก image CPU tools, NVIDIA CUDA + Vulkan และ AMD Vulkan
   ทดสอบจริงแต่ละกลุ่มก่อนอ้างว่ารองรับ

อ้างอิงสำหรับขั้น implementation:
[Electron autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater/),
[electron-builder NSIS](https://www.electron.build/docs/nsis/),
[Docker Desktop GPU](https://docs.docker.com/desktop/features/gpu/).
