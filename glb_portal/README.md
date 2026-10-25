# Model Room · 3D Model Portal

คลังสำหรับเปิดโมเดลจากโปรแกรมทั่วไป รองรับ GLB/glTF 2.0, OBJ, PLY Mesh/Point Cloud, Gaussian PLY, SPZ v1–3, raw SPLAT และ ZIP ที่รวมไฟล์ประกอบ ใช้ Three.js 0.186.1 กับ Spark 2.3.1 ที่รวมไว้ในโฟลเดอร์นี้ และ Python 3.11+ โดยไม่ต้องติดตั้ง package เพิ่มเพื่อรันเซิร์ฟเวอร์ ไม่ต้องมีข้อมูลเฉพาะหรือ export จากแอปใดแอปหนึ่ง รายการเก็บใน Project และยังอยู่เมื่อปิดหน้าเว็บหรือรีสตาร์ตเซิร์ฟเวอร์

## การใช้งานและไฟล์ที่รองรับ

เลือก **Project → เพิ่มไฟล์โมเดล** แล้วเลือกหรือลากหลายไฟล์ลงในหน้าต่างอัปโหลด ระบบส่งทีละไฟล์ แจ้งผลแยกรายการ และข้ามไฟล์ที่ไม่ถูกต้องเพื่อทำรายการที่เหลือต่อ ปุ่ม **หยุดคิวหลังไฟล์นี้** บันทึกไฟล์ปัจจุบันให้เสร็จก่อนหยุดไฟล์ที่ยังไม่เริ่ม ชื่อที่กรอกเองใช้กับการเพิ่มทีละไฟล์ ส่วนการเพิ่มหลายไฟล์ใช้ชื่อไฟล์เดิม

| ไฟล์ | รองรับ |
| --- | --- |
| GLB / glTF 2.0 | Mesh, วัสดุ, texture, animation, กล้องในไฟล์ และ `KHR_gaussian_splatting` · ถ้าแยก buffer/texture ให้รวมเป็น ZIP |
| OBJ | Mesh · ถ้ามี MTL/texture ให้รวมเป็น ZIP; OBJ เดี่ยวเปิดเฉพาะ geometry |
| PLY | Mesh/Point Cloud แบบ ASCII, binary little-endian และ big-endian; Gaussian ใช้ little-endian float32 พร้อมตำแหน่ง, สี SH0, opacity, scale และ quaternion |
| SPZ | gzip version 1–3, SH degree 0–3 |
| SPLAT | Gaussian แบบ raw 32 bytes ต่อจุด |
| ZIP | glTF พร้อม .bin/texture, OBJ พร้อม MTL/texture หรือหลายโมเดล ไม่ต้องมี manifest เฉพาะ · ตรวจรายการก่อนนำเข้า |

ยังไม่รองรับ glTF ที่ต้องใช้ Draco/Meshopt/KTX2 decoder, SPZ v4 หรือ ZIP ที่ผสม Gaussian GLB กับ SPZ/Gaussian PLY ในฉากเดียว ดู [รายละเอียดรูปแบบและข้อมูลนำทางเพิ่มเติม](FORMATS.md) ZIP จะแสดงตำแหน่งและรายการก่อนบันทึก โดยคงพิกัดของแต่ละไฟล์ ไม่จัดแนวโมเดลให้เอง

ในคลังค้นหาชื่อ กรองชนิดไฟล์ และเรียงตามล่าสุด/ชื่อ/ขนาดได้ กดไอคอนดินสอเพื่อเปลี่ยนชื่อ หรือถังขยะเพื่อลบ (เฉพาะผู้ดูแล) เมนู **⋯** ของโมเดลรวมการดาวน์โหลดต้นฉบับ คัดลอกลิงก์ เปลี่ยนชื่อ จัดกลางภาพ และหมุน Gaussian 180° ส่วน **ตั้งค่า** รวมความเร็ว คุณภาพภาพ และ cache เมนูบัญชีรวมประวัติเข้าสู่ระบบและออกจากระบบ บนมือถือกด **คลังโมเดล** เพื่อเปิดแถบข้าง เลือกโมเดลแล้วแถบจะปิดเอง

ภาพปกสร้างเมื่อผู้ดูแลเปิดโมเดลครั้งแรก และเปลี่ยนได้ที่ **จุดนำทาง → ใช้มุมปัจจุบันเป็นภาพปก** ภาพกับจำนวนจุด/จุดยอดแสดงในคลังโดยไม่ต้องโหลดโมเดลเข้า GPU อีก กดไอคอนเลือกหลายรายการเพื่อย้าย Project หรือลบพร้อมกัน และไอคอนดินสอข้าง Project เพื่อเปลี่ยนชื่อ

**จุดนำทาง** ใช้ได้กับไฟล์ทุกชนิด ผู้ดูแลบันทึกมุมกล้องและโหมด Orbit/Walk/Free ได้สูงสุด 32 จุด ผู้ชมเปิดจุดที่บันทึกไว้ได้ กล้อง perspective มาตรฐานที่ฝังมาใน glTF แสดงในรายการด้วย หากไฟล์มีข้อมูลตรวจชนและ spawn เพิ่มเติม ระบบจะใช้ในโหมดเดิน; ข้อมูลนี้เป็นส่วนเสริม ไม่ใช่เงื่อนไขสำหรับเปิดโมเดล

## ใช้บนเครื่องตัวเอง

```bash
cd glb_portal
python3 server.py set-user admin --role admin
python3 server.py set-user guest --role viewer
python3 server.py serve --host 127.0.0.1 --port 8088
```

เปิด `http://127.0.0.1:8088` บัญชี `admin` อัปโหลดและลบโมเดล รวมทั้งดู login log ได้ ส่วน `viewer` เปิดชมและดาวน์โหลดได้เท่านั้น รหัสผ่านตั้งได้ตั้งแต่ 5 ตัวอักษรและถูกแฮชด้วย PBKDF2 ไม่เก็บรหัสผ่านจริง สำหรับเว็บที่เปิดผ่านอินเทอร์เน็ตควรตั้งรหัสผ่านที่ยาวกว่าขั้นต่ำ

ตัวดูมี **Orbit** (หมุน/ซูม), **Walk** (WASD เดิน) และ **Free view** (WASD บิน, Q/E ลง/ขึ้น) คลิกพื้นที่ภาพเพื่อใช้เมาส์มองรอบตัว กด Esc เพื่อปล่อยเมาส์ ปุ่ม **ตั้งค่า** เปิดตัวปรับความเร็วเดิน/บิน ความไวหมุน ความไว Scroll/ซูม และคุณภาพภาพ ค่าเริ่มต้นเป็นระดับช้าและบันทึกไว้ในเบราว์เซอร์ กด Shift เพื่อเร่ง 3 เท่าชั่วคราว Walk ใช้พื้น/กำแพงและแรงโน้มถ่วงเมื่อมีข้อมูลตรวจชน; ไฟล์ที่ไม่มีข้อมูลนี้รักษาระดับความสูงคงที่ Free view บินผ่านได้อย่างอิสระ

ไฟล์โมเดลและ buffer/texture ของ ZIP เก็บใน browser Cache Storage หลังดาวน์โหลดครั้งแรก (ล่าสุดไม่เกิน 2 รายการในคลัง) ก่อนใช้ cache แต่ละไฟล์จะตรวจสิทธิ์ login และรุ่นไฟล์จากเซิร์ฟเวอร์ ใช้ได้บน HTTPS หรือ localhost และขึ้นกับพื้นที่ว่าง/นโยบาย browser กด **ล้าง cache โมเดล** เพื่อคืนพื้นที่ การ cache ลดการดาวน์โหลดซ้ำ แต่ยังต้องถอดรหัสและอัปโหลดข้อมูลเข้า GPU เมื่อเปิดใหม่

ถ้าโมเดลเป็น Gaussian Splat ขนาดใหญ่ ให้เลือกไฟล์ SPZ v3 ที่บีบอัดจาก app หลักเพื่อลดขนาดการดาวน์โหลดครั้งแรก แล้วอัปโหลดเข้า Project ได้เหมือน GLB การเปิด SPZ ใช้ WebGL2 และ GPU ของเบราว์เซอร์; ความเร็วในการถอดรหัสและแสดงผลขึ้นกับเครื่องของผู้ชม

หลังอัปโหลดไฟล์ หน้าเว็บจะแสดงสถานะขณะเซิร์ฟเวอร์ตรวจไฟล์ แล้วแจ้งเมื่อบันทึกลง Project สำเร็จ โมเดลที่อัปโหลดจะอยู่ในรายการทันที แต่จะเริ่มโหลดเข้า GPU เมื่อคลิกชื่อโมเดลเท่านั้น ไฟล์ที่ใหญ่กว่า 64 MB จะไม่เปิดเองเมื่อเข้าหน้าเว็บใหม่ เพื่อไม่ให้เบราว์เซอร์ต้องเรนเดอร์ไฟล์ใหญ่ทันทีหลังล็อกอิน

เซิร์ฟเวอร์ไม่ต้องมี GPU: รับอัปโหลด ตรวจไฟล์ SPZ แบบสตรีม เก็บข้อมูล และส่งไฟล์ให้ผู้ชม งานหนักด้านภาพอยู่ที่ CPU/GPU ของผู้ชม ฝั่ง EC2 ควรเผื่อดิสก์ตามจำนวนโมเดลและทราฟฟิกตามจำนวนผู้ชมที่เปิดไฟล์ครั้งแรกพร้อมกัน เพราะ cache อยู่แยกในเบราว์เซอร์ของแต่ละคน

ไฟล์และฐานข้อมูลอยู่ใน `data/` โดยค่าเริ่มต้น เปลี่ยนได้ด้วย `GLB_PORTAL_DATA` ค่าเริ่มต้นจำกัดไฟล์ 2 GB เปลี่ยนด้วย `GLB_PORTAL_MAX_MB` หน้าอัปโหลดแสดงเพดานที่เซิร์ฟเวอร์ใช้จริง ตัวอย่าง Compose, systemd และ Nginx กำหนด 2048 MB ให้สอดคล้องกัน หากมี `.env` เดิมที่ใช้ 1024 ให้แก้เมื่อจำเป็นต้องรับไฟล์ใหญ่ขึ้น หน้า login log แสดง 200 รายการล่าสุดจากฐานข้อมูล SQLite (success/failure, เวลา UTC, ชื่อผู้ใช้, IP, user agent ในฐานข้อมูล) สำรองทั้งโฟลเดอร์ข้อมูลเพื่อเก็บโมเดลและ log การอัปโหลดจากเวอร์ชันก่อนถูกย้ายเข้ากลุ่ม **ทั่วไป** อัตโนมัติ

## Docker Compose บน EC2

Compose เปิดแอปที่ `127.0.0.1:8088` เพื่อให้ Nginx บนเครื่อง EC2 รับ HTTPS แล้วส่งต่อไปยังแอป โดยตั้งชื่อ container เป็น `3dgs-portal` (ชื่อ service สำหรับคำสั่ง `docker compose exec` ยังเป็น `portal`) และผูก named volume `portal_data` กับ `/data` ภายใน container ซึ่งเก็บทั้ง `portal.sqlite3`, ไฟล์ GLB/SPZ, Project และ login log โดย volume ยังอยู่หลัง `docker compose down`, `up --build` หรือสร้าง container ใหม่

```bash
cd glb_portal
cp .env.example .env
# แก้ GLB_PORTAL_ORIGIN ใน .env เป็น https://โดเมนจริง
docker compose build
docker compose run --rm --no-deps portal python server.py set-user admin --role admin
docker compose up -d
docker compose ps
```

ใช้ [ตัวอย่าง Nginx](deploy/nginx.conf) โดยแทน `YOUR_DOMAIN` แล้วติดตั้ง TLS ให้โดเมนเดียวกับ `.env` อย่าใช้ `docker compose down -v` เพราะ `-v` ลบ volume ข้อมูลด้วย สำรอง volume นี้ก่อนย้าย EC2 หรือเปลี่ยนเครื่อง และหากเคยรันแบบ Python ตรง ๆ ในโฟลเดอร์นี้ ข้อมูลเดิมอยู่ใน `glb_portal/data/` ซึ่งต้องคัดลอกเข้า volume ก่อนเปลี่ยนวิธีรัน

ตรวจข้อมูลที่ container ใช้จริงได้ด้วย `docker compose exec portal python server.py inspect` คำสั่งนี้แสดงที่เก็บข้อมูล จำนวน Project/โมเดล และจำนวนไฟล์ที่หาไม่พบ ถ้าเข้าเว็บใหม่แล้วรายการว่าง ให้ตรวจคำสั่งนี้และเลือก Project เดิมในหน้าเว็บ

ทดสอบ Docker บนเครื่องตัวเอง: ตั้ง `.env` เป็น `GLB_PORTAL_ORIGIN=http://127.0.0.1:8088` และ `GLB_PORTAL_SECURE_COOKIE=0` ก่อน จากนั้นรัน `docker compose up -d --build` และ `python smoke_docker.py` สคริปต์นี้อัปโหลด GLB/SPZ ทดสอบ สร้าง container ใหม่ ตรวจว่าข้อมูลยังอยู่ แล้วลบบัญชีและโมเดลทดสอบออก หากมี Chromium/Chrome บนเครื่อง ให้รัน `node chromium_smoke.mjs` สำหรับ GLB และตั้ง `CHROMIUM_TEST_FORMAT=spz` ก่อนรันอีกครั้งสำหรับ SPZ เปิดเว็บได้ที่ `http://127.0.0.1:8088` หากต้องการใช้งานจริงให้สร้างบัญชีด้วย `docker compose exec portal python server.py set-user admin --role admin`

หากอัปโหลดแล้วแท็บค้างหรือรายการว่าง ให้ดู `docker compose logs --since=15m --tail=80 portal` และ `docker compose exec portal python server.py inspect` บนเครื่องที่เปิดเว็บจริง `POST /api/models` สถานะ 201 หมายถึงบันทึกสำเร็จ; หากไม่มี POST ให้ตรวจ URL เว็บและ proxy ที่ใช้งาน ถ้าอัปโหลดเข้า Project อื่น ให้เลือก Project เดิมในเมนูด้านซ้ายก่อนตรวจรายการ

หาก PowerShell อยู่ที่ `D:\3dconvert` โดยยังไม่ได้ `cd glb_portal` ให้ระบุไฟล์ Compose และ `.env` โดยตรง:

```powershell
docker compose --env-file glb_portal/.env -f glb_portal/docker-compose.yml exec portal python server.py set-user admin --role admin
```

## ติดตั้งบน EC2 (Ubuntu)

1. ให้ DNS ชี้มาที่ EC2 และเปิด Security Group เฉพาะ SSH, HTTP 80, HTTPS 443 ไม่ต้องเปิดพอร์ต 8088
2. คัดลอก **ทั้งโฟลเดอร์ `glb_portal`** ไป `/opt/glb-portal` (รวม `vendor/three` และ `vendor/spark`) แล้วติดตั้ง Python 3.11+ และ Nginx
3. สร้าง user และพื้นที่ข้อมูล:

   ```bash
   sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin glbportal
   sudo mkdir -p /var/lib/glb-portal
   sudo chown glbportal:glbportal /var/lib/glb-portal
   sudo chmod 750 /var/lib/glb-portal
   sudo chown -R root:root /opt/glb-portal
   sudo chmod -R a+rX /opt/glb-portal
   sudo -u glbportal env GLB_PORTAL_DATA=/var/lib/glb-portal python3 /opt/glb-portal/server.py set-user admin --role admin
   ```

4. คัดลอก `deploy/glb-portal.service` ไป `/etc/systemd/system/` และ `deploy/nginx.conf` ไป `/etc/nginx/sites-available/glb-portal` แทน `YOUR_DOMAIN` ด้วยโดเมนจริงทั้งสองไฟล์ จากนั้น:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now glb-portal
   sudo ln -s /etc/nginx/sites-available/glb-portal /etc/nginx/sites-enabled/glb-portal
   sudo nginx -t
   sudo systemctl reload nginx
   ```

5. ติดตั้ง TLS certificate (เช่น Certbot สำหรับ Nginx) ให้ใช้งานผ่าน `https://YOUR_DOMAIN` เท่านั้น `GLB_PORTAL_ORIGIN` ใน service ต้องตรง URL จริง และ `GLB_PORTAL_SECURE_COOKIE=1` ต้องคงไว้ หลังแก้ service ให้ `sudo systemctl daemon-reload && sudo systemctl restart glb-portal`

6. ทดสอบล็อกอิน, อัปโหลด GLB/SPZ จริงจาก 360 Studio, เปิดดูทั้งสามโหมด และตรวจ login log บนเว็บ รวมทั้ง `sudo journalctl -u glb-portal -n 50`

ถ้าเปลี่ยนเพดานอัปโหลด ให้ปรับ `GLB_PORTAL_MAX_MB` ใน service และ `client_max_body_size` ใน Nginx ให้สอดคล้องกัน บน EC2 ต้องมีพื้นที่ดิสก์เพียงพอสำหรับ GLB และสำรอง `/var/lib/glb-portal` สม่ำเสมอ

## จัดการบัญชี

รัน `set-user` ซ้ำเพื่อเปลี่ยนรหัสผ่านหรือ role; session เดิมของบัญชีนั้นจะถูกยกเลิก ใช้ `delete-user USERNAME` เพื่อลบบัญชี บัญชีผู้ชมไม่มีสิทธิ์อัปโหลดหรือลบโมเดล

## ทดสอบและแผนพัฒนา

ดู [แผนและขอบเขตงาน](PLAN.md) การทดสอบ HTTP ใช้เฉพาะ Python standard library:

```bash
python -m unittest discover -s glb_portal -p "test_*.py" -q
```

ทดสอบหน้าจอและ GPU ด้วย Playwright/Chromium (ติดตั้งเฉพาะในสภาพแวดล้อมทดสอบ ไม่จำเป็นต่อการรันเซิร์ฟเวอร์):

```bash
python glb_portal/test_browser.py --chromium /path/to/chrome --output tools/browser-qa/portal-review
# ทดสอบไฟล์ที่ export จาก Scene Editor เพิ่มเติม
python glb_portal/test_browser.py --chromium /path/to/chrome --scene-glb /path/to/scene.glb
```

สคริปต์สร้างผู้ใช้และฐานข้อมูลทดสอบใน temporary directory แยกจากข้อมูลจริง ทดสอบคิวหลายชนิดไฟล์ ไฟล์เสีย การหยุดคิว แยก Project การเรนเดอร์ การเปลี่ยนชื่อ สิทธิ์ผู้ชม คีย์บอร์ด และหน้าจอหลายขนาด พร้อมบันทึกภาพและ `report.json`
