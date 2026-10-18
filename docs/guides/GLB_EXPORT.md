# ส่งออกไปใช้กับ Three.js

เปิด **Models → Models บีบอัด (SPZ) → ⋯ → ส่งออก GLB / Three.js**
หรือเปิด Record ของรุ่นบีบอัดแล้วกด **ส่งออก GLB / Three.js** จากนั้นกด **เตรียม GLB**.
หน้าแสดงคิวและความคืบหน้า เมื่อเสร็จมีปุ่มดาวน์โหลดและเปิดดูใน Three.js.
ปิดหน้าได้ งานยังทำต่อ; กลับมาเปิดหน้าส่งออกเดิมเพื่อดูผลหรือกดลองใหม่หากล้มเหลว.

หากซูมออกแล้วเห็นหมอกนอกห้อง ให้กด **ตัดขอบห้อง / ลบส่วนฟุ้งรอบนอก**
ในหน้าส่งออก (เมื่อยังมี PLY ต้นทางที่ตรงกัน) แล้วบันทึกเป็นรุ่นใหม่ก่อนส่งออกต่อ.
GLB รุ่นเก่าจะคงข้อมูลเดิม ดู [วิธีตัดขอบ](MODEL_CROP.md).

## ZIP พร้อมเปิดต่อ (แนะนำ)

หลัง **เตรียม GLB** เสร็จ กด **เตรียม ZIP พร้อมเปิด** แล้วดาวน์โหลดจากหน้าส่งออกเดิม.
มีสถานะรอคิว/เปอร์เซ็นต์, ลองใหม่ได้ และไม่เปลี่ยน DOM จนเมาส์หรือปุ่มที่ใช้อยู่กระโดด.
ZIP รวม `model.glb`, `index.html`, Three.js 0.186.1 พร้อม dependencies/ใบอนุญาต,
`serve.py`, ตัวเปิด Windows/Ubuntu, README และ manifest SHA-256.

แตก ZIP ทั้งหมด แล้ว Windows เปิด `START_WINDOWS.cmd`; Ubuntu รัน `bash start_ubuntu.sh`.
ต้องมี **Python 3.10+** แต่ไม่ต้องติดตั้ง pip packages/Node.js และไม่ต้องใช้อินเทอร์เน็ตขณะดู.
ตัวเปิดใช้ localhost, เปิด browser ให้ และเลือกพอร์ตว่างเมื่อ 8080 ถูกใช้แล้ว.
ยังต้องใช้ browser/GPU ที่รองรับ WebGPU หรือ WebGL2 และ import maps.
หากนำไปลงเว็บ ให้เก็บโครงสร้าง `vendor/three/` กับ GLB แล้วเปิดผ่าน HTTP/HTTPS.

การแพ็กใช้คิว CPU แยกและเขียน ZIP แบบ streaming. แต่ละชุดผูกกับ checksum ของ GLB
และไฟล์ตัวอย่าง, ตรวจ assets ก่อนแพ็ก, ป้องกันคำขอซ้ำ และไม่เขียนทับโมเดล.
ไฟล์ npm ที่รวมอยู่เก็บแบบไม่แปลง LF/CRLF ใน Git เพื่อให้ตรวจ checksum ได้ทั้งสองระบบ.
ติดตั้ง assets ซ้ำได้ด้วย `python scripts/install_export_assets.py` (ต้องใช้อินเทอร์เน็ตเฉพาะตอนติดตั้ง).
สคริปต์ตรวจ SHA-512 ของ npm archive และรวม dependencies ที่ตัวโหลดต้องใช้เท่านั้น.

ทดสอบ: `python -m unittest discover -s tests -p test_model_package.py`.

## ดาวน์โหลดแยกไฟล์

1. ดาวน์โหลด `model.glb` และตัวอย่าง `index.html` จากหน้าส่งออก เก็บในโฟลเดอร์เดียวกัน.
2. เปิด Terminal ในโฟลเดอร์นั้นแล้วรัน `python -m http.server 8080`.
3. เปิด `http://localhost:8080` ใช้เมาส์ลากหมุน ลูกล้อซูม ปุ่มขวาลากเลื่อน
   และปุ่มกลับมุมเริ่มต้นเพื่อกลับไปกล้องที่บันทึกไว้.

ตัวอย่างใช้ Three.js **0.186.1** จาก CDN จึงต้องมีอินเทอร์เน็ต.
ถ้าใช้งานผ่าน npm ให้ติดตั้งเวอร์ชันเดียวกันด้วย `npm install three@0.186.1`
แล้วนำโค้ดส่วน module ใน [ตัวอย่าง](../../examples/threejs-splat.html) ไปใช้ในโปรเจกต์.
หากเปลี่ยนชื่อไฟล์หรือที่เก็บ ให้แก้ URL ใน `loader.loadAsync()`.
ให้บริการไฟล์ผ่าน HTTP/HTTPS; เมื่อแยกโดเมนต้องตั้ง CORS ที่เซิร์ฟเวอร์ไฟล์ด้วย.

ส่วนสำคัญของตัวโหลด:

```js
import { WebGPURenderer } from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { GLTFGaussianSplatLoaderExtension } from 'three/addons/loaders/GLTFGaussianSplatLoaderExtension.js';

const renderer = new WebGPURenderer({ forceWebGL: !navigator.gpu });
await renderer.init();
const loader = new GLTFLoader();
loader.register(parser => new GLTFGaussianSplatLoaderExtension(parser));
const gltf = await loader.loadAsync('./model.glb');
scene.add(gltf.scene);
```

โค้ดข้างบนเฉพาะตัวโหลด; ตัวอย่าง HTML มี scene, camera, canvas, progress, error,
OrbitControls และ render loop ครบ พร้อมลอง WebGL2 ผ่าน WebGPURenderer เมื่อเริ่ม WebGPU ไม่ได้.
`WebGLRenderer` ปกติใช้กับ GaussianSplat รุ่นนี้ไม่ได้.
อ้างอิง [Three.js GLTFGaussianSplatLoaderExtension](https://threejs.org/docs/pages/GLTFGaussianSplatLoaderExtension.html)
และ [GaussianSplat](https://threejs.org/docs/pages/GaussianSplat.html).

## ชนิดไฟล์และขอบเขต

GLB นี้เป็น Gaussian Splat ตาม
[KHR_gaussian_splatting](https://github.com/KhronosGroup/glTF/blob/main/extensions/2.0/Khronos/KHR_gaussian_splatting/README.md).
ตัวโหลด GLB ต้องรองรับ extension นี้จึงจะแสดงผลได้.
ยังไม่มีการสร้างผิวสามเหลี่ยมสำหรับ OBJ หรือ GLB แบบ Mesh.
หากต้องการแก้ผิวในโปรแกรมโมเดล, collision หรือส่งไป CAD ต้องมีขั้นสร้างและตรวจ Mesh เพิ่ม.

ส่งออกจาก **SPZ ของ record ที่เลือก** โดยตรง ไม่ต้องมี PLY ต้นทางอยู่และไม่รัน training.
เก็บจำนวน splats, ตำแหน่ง, rotation, scale, opacity และ SH ทุกระดับที่ SPZ มี (0–3).
อ่านค่า SPZ v3 ตามรูปแบบ [Niantic SPZ](https://github.com/nianticlabs/spz)
เป็น float32 โดยไม่ลดจุดหรือบีบอัดซ้ำผ่านข้อมูล GPU ของ viewer.
ความสูญเสียจากการบีบอัด SPZ เดิมไม่สามารถกู้คืนด้วยการส่งออก.
ภาพจาก Three.js กับ Spark อาจต่างกันตาม renderer/การจัดลำดับ/การแปลงสี ต้องเทียบมุมเดียวกัน.

GLB ใช้ข้อมูลไม่บีบอัด เพราะตัวโหลด Three.js รุ่นที่ทดสอบยังไม่ได้ถอด SPZ ภายใน GLB.
จึงมีขนาดใหญ่กว่า SPZ: SH 3 ใช้ประมาณ **236 ไบต์ต่อ splat** บวก JSON เล็กน้อย
(หนึ่งล้าน splats ประมาณ 236 MB). ถ้าต้องการไฟล์เล็ก ให้โหลด SPZ ด้วย Spark โดยตรง.
การส่งออกไม่ทำให้โมเดลคมขึ้นหรือเพิ่ม FPS โดยอัตโนมัติ.

มุมกล้องเริ่มต้นเก็บใน `scene.extras.initial_view` เมื่อ record มี metadata นั้น.
หากไม่มี ตัวอย่างจัดกล้องจากขอบเขตโมเดล. พิกัดคงเดิมและยังเป็นหน่วย reconstruction
ไม่ได้อ้างว่าเป็นเมตรจริงหรือจัดแนวพื้นใหม่.

ผลส่งออกอยู่ใน `web_data/model_variants/records/<id>/glb_v1/` พร้อม checksum
และข้อมูลต้นทาง มีคิว CPU แยก, ป้องกันส่งซ้ำข้ามเซิร์ฟเวอร์, ตรวจไฟล์ก่อนเผยแพร่,
เรียกใช้ cache เดิมและลองใหม่หลังงานถูกขัดจังหวะได้. งานอาจใช้ RAM และ disk ตามขนาดโมเดล.
รองรับ SPZ v3 ที่แอปสร้าง ไม่เปิดรับ SPZ arbitrary upload ในหน้าส่งออกนี้.

## ตรวจสอบ

```powershell
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -p test_model_exports.py
```

ทดสอบแปลงจริง SH 0–3, พิกัด/rotation/scale/opacity/สีและลำดับ SH,
cache, ส่งซ้ำ, retry, ไฟล์ต้นทางเปลี่ยน, PLY ต้นทางหาย, งานขัดจังหวะ,
และ HTTP Range สำหรับดาวน์โหลด. บน Ubuntu ใช้ Python environment ของแอปแทนคำสั่ง Windows.
การส่งออกต้องติดตั้ง Node.js เช่นเดียวกับการบีบอัด SPZ.

ผลตรวจ browser วันที่ 17 ตุลาคม 2026: Chromium เปิดชุดทดสอบ 10,396 splats
และ DJI High 949,769 splats / SH 3 ผ่าน Three.js ได้ครบ.
ตัวอย่าง HTML เปิดแยกจากแอปผ่าน HTTP และ WebGL2 fallback ได้.
GLB ของ DJI มีขนาด 224,149,264 ไบต์ เทียบ SPZ ต้นทาง 21,521,770 ไบต์.
ภาพหน้าจอจาก GLB เทียบ PLY เต็มด้วย renderer/มุมกล้อง/ขนาดภาพเดียวกันได้
PSNR 37.94 dB; ค่านี้วัดความต่างระหว่างรูปแบบไฟล์ ไม่ใช่ความถูกต้องเทียบสถานที่จริง.
หลักฐานใน workspace อยู่ที่ `web_data/capture_review/glb_export_20261017/`.
ชุด Python รวม 198 tests ผ่าน (ข้าม 1 GPU opt-in); JavaScript 15 ชุดผ่าน.
