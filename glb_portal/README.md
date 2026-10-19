# GLB Model Portal

เว็บแยกสำหรับเปิดไฟล์ `.glb` ที่ส่งออกจาก 360 Studio รองรับทั้ง Mesh GLB และ `KHR_gaussian_splatting` ผ่าน Three.js 0.186.1 ที่รวมไว้ในโฟลเดอร์นี้ ใช้ Python 3.11+ โดยไม่ต้องติดตั้ง package เพิ่ม แต่ละไฟล์ที่อัปโหลดเป็นโมเดลแยกกันใน Project ที่เลือก และยังอยู่เมื่อปิดหน้าเว็บหรือรีสตาร์ตเซิร์ฟเวอร์

## ใช้บนเครื่องตัวเอง

```bash
cd glb_portal
python3 server.py set-user admin --role admin
python3 server.py set-user guest --role viewer
python3 server.py serve --host 127.0.0.1 --port 8088
```

เปิด `http://127.0.0.1:8088` บัญชี `admin` อัปโหลดและลบโมเดล รวมทั้งดู login log ได้ ส่วน `viewer` เปิดชมและดาวน์โหลดได้เท่านั้น รหัสผ่านตั้งได้ตั้งแต่ 5 ตัวอักษรและถูกแฮชด้วย PBKDF2 ไม่เก็บรหัสผ่านจริง สำหรับเว็บที่เปิดผ่านอินเทอร์เน็ตควรตั้งรหัสผ่านที่ยาวกว่าขั้นต่ำ

ตัวดูมี **Orbit** (หมุน/ซูม), **Walk** (WASD เดินแนวราบ) และ **Free view** (WASD บิน, Q/E ลง/ขึ้น) คลิกพื้นที่ภาพเพื่อใช้เมาส์มองรอบตัว กด Esc เพื่อปล่อยเมาส์ Walk ใช้ระดับความสูงคงที่และไม่มี collision เพราะ Gaussian Splat ไม่มีพื้นผิวที่ใช้ตรวจชน

ไฟล์และฐานข้อมูลอยู่ใน `data/` โดยค่าเริ่มต้น เปลี่ยนได้ด้วย `GLB_PORTAL_DATA` ค่าเริ่มต้นจำกัดไฟล์ 1 GB เปลี่ยนด้วย `GLB_PORTAL_MAX_MB` หน้า login log แสดง 200 รายการล่าสุดจากฐานข้อมูล SQLite (success/failure, เวลา UTC, ชื่อผู้ใช้, IP, user agent ในฐานข้อมูล) สำรองทั้งโฟลเดอร์ข้อมูลเพื่อเก็บโมเดลและ log การอัปโหลดจากเวอร์ชันก่อนถูกย้ายเข้ากลุ่ม **ทั่วไป** อัตโนมัติ

## Docker Compose บน EC2

Compose เปิดแอปที่ `127.0.0.1:8088` เพื่อให้ Nginx บนเครื่อง EC2 รับ HTTPS แล้วส่งต่อไปยังแอป โดยตั้งชื่อ container เป็น `3dgs-portal` (ชื่อ service สำหรับคำสั่ง `docker compose exec` ยังเป็น `portal`) และผูก named volume `portal_data` กับ `/data` ภายใน container ซึ่งเก็บทั้ง `portal.sqlite3`, ไฟล์ GLB, Project และ login log โดย volume ยังอยู่หลัง `docker compose down`, `up --build` หรือสร้าง container ใหม่

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

ทดสอบ Docker บนเครื่องตัวเอง: ตั้ง `.env` เป็น `GLB_PORTAL_ORIGIN=http://127.0.0.1:8088` และ `GLB_PORTAL_SECURE_COOKIE=0` ก่อน จากนั้นรัน `docker compose up -d --build` และ `python smoke_docker.py` สคริปต์นี้อัปโหลด GLB ทดสอบ สร้าง container ใหม่ ตรวจว่าข้อมูลยังอยู่ แล้วลบบัญชีและโมเดลทดสอบออก เปิดเว็บได้ที่ `http://127.0.0.1:8088` หากต้องการใช้งานจริงให้สร้างบัญชีด้วย `docker compose exec portal python server.py set-user admin --role admin`

หาก PowerShell อยู่ที่ `D:\3dconvert` โดยยังไม่ได้ `cd glb_portal` ให้ระบุไฟล์ Compose และ `.env` โดยตรง:

```powershell
docker compose --env-file glb_portal/.env -f glb_portal/docker-compose.yml exec portal python server.py set-user admin --role admin
```

## ติดตั้งบน EC2 (Ubuntu)

1. ให้ DNS ชี้มาที่ EC2 และเปิด Security Group เฉพาะ SSH, HTTP 80, HTTPS 443 ไม่ต้องเปิดพอร์ต 8088
2. คัดลอก **ทั้งโฟลเดอร์ `glb_portal`** ไป `/opt/glb-portal` (รวม `vendor/three`) แล้วติดตั้ง Python 3.11+ และ Nginx
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

6. ทดสอบล็อกอิน, อัปโหลด GLB จริงจาก 360 Studio, เปิดดูทั้งสามโหมด และตรวจ login log บนเว็บ รวมทั้ง `sudo journalctl -u glb-portal -n 50`

ถ้าเปลี่ยนเพดานอัปโหลด ให้ปรับ `GLB_PORTAL_MAX_MB` ใน service และ `client_max_body_size` ใน Nginx ให้สอดคล้องกัน บน EC2 ต้องมีพื้นที่ดิสก์เพียงพอสำหรับ GLB และสำรอง `/var/lib/glb-portal` สม่ำเสมอ

## จัดการบัญชี

รัน `set-user` ซ้ำเพื่อเปลี่ยนรหัสผ่านหรือ role; session เดิมของบัญชีนั้นจะถูกยกเลิก ใช้ `delete-user USERNAME` เพื่อลบบัญชี บัญชีผู้ชมไม่มีสิทธิ์อัปโหลดหรือลบโมเดล
