# เปิดโมเดล Gaussian Splat ด้วย Three.js

1. แตก ZIP ทั้งหมดไว้ในโฟลเดอร์เดียวกันก่อนเปิด
2. ต้องมี Python 3.10 ขึ้นไป (ไม่ต้องติดตั้ง pip packages หรือ Node.js)
3. Windows: ดับเบิลคลิก `START_WINDOWS.cmd` / Ubuntu: รัน `bash start_ubuntu.sh`
4. เบราว์เซอร์จะเปิดให้อัตโนมัติ ถ้าไม่เปิด ให้ใช้ URL ที่แสดงใน Terminal
5. ลากเพื่อหมุน เลื่อนลูกล้อเพื่อซูม ลากปุ่มขวาเพื่อเลื่อน กดกลับมุมเริ่มต้นได้
6. ปิด Terminal หรือกด Ctrl+C เพื่อหยุดเซิร์ฟเวอร์

หรือรันเอง: `python serve.py --port 8080` แล้วเปิด http://127.0.0.1:8080/
Ubuntu อาจใช้ `python3` แทน `python` หากพอร์ตถูกใช้ โปรแกรมจะเลือกพอร์ตว่างให้
ห้ามเปิด index.html ด้วย file:// เพราะ browser ต้องโหลดโมเดลผ่าน HTTP

รวม Three.js 0.186.1 และ dependencies ไว้แล้ว ไม่ต้องเชื่อมอินเทอร์เน็ตขณะดูโมเดล
ใช้เบราว์เซอร์ที่รองรับ WebGPU หรือ WebGL2 และ import maps พร้อมเปิด hardware acceleration
การแสดงผลยังต้องใช้ GPU ที่รองรับ Gaussian Splat ของ Three.js

## นำไปใช้ในโปรเจกต์ Three.js

ดูโค้ดทั้งหมดใน index.html: ใช้ GLTFLoader พร้อม GLTFGaussianSplatLoaderExtension
และ WebGPURenderer (ลอง WebGL2 ผ่าน renderer นี้เมื่อเริ่ม WebGPU ไม่สำเร็จ)
คัดลอก model.glb และ vendor ไปกับหน้า HTML หรือเปลี่ยน import map ให้ตรงกับโปรเจกต์
หากเผยแพร่เว็บ ให้ใช้ HTTPS และเก็บ LICENSE ของ Three.js ไปด้วย
ไฟล์นี้เป็น KHR_gaussian_splatting ไม่ใช่ mesh สำหรับ OBJ / แก้ผิวสามเหลี่ยม
หน่วยจาก reconstruction ยังไม่ใช่เมตรจริงถ้าไม่มีระยะอ้างอิง

## Package contents / English

Extract the entire ZIP. Install Python 3.10+, then run START_WINDOWS.cmd (Windows)
or `bash start_ubuntu.sh` (Ubuntu). No pip dependencies, Node.js, CDN or internet
connection is required for viewing. Use a browser with import maps and WebGPU or
WebGL2 support. Keep the terminal open; Ctrl+C stops the local server.

- model.glb: immutable Gaussian Splat export from this model version
- index.html: editable Three.js example with local dependencies
- vendor/three/: pinned Three.js source modules and MIT LICENSE
- serve.py and launchers: localhost server, automatically finds a free port
- manifest.json: SHA-256 and byte size for each packaged file

The ZIP preserves the selected exported model; it does not retrain or increase
detail. The GLB requires a Gaussian Splat aware loader and renderer.
