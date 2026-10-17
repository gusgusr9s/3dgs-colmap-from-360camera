"""Small export screen shared by the initial response and stable status polling."""
import html
import json


def export_markup(identifier, record, state, crop_url=None, package_state=None):
    name = html.escape(record.get('model', 'Model'))
    initial = json.dumps(state, ensure_ascii=False).replace('<', '\\u003c')
    package_initial = json.dumps(package_state or {'state':'unavailable'}, ensure_ascii=False).replace('<', '\\u003c')
    crop_action = f'<p><a class="button" href="{html.escape(crop_url, quote=True)}">ตัดขอบห้อง / ลบส่วนฟุ้งรอบนอก</a></p>' if crop_url else ''
    return f'''<section class="panel" id="glb-export" data-id="{identifier}">
<div class="panel-head"><h2>ส่งออก GLB สำหรับ Three.js</h2></div>
<p>{name} · <a href="/compression/{identifier}">กลับไป Model บีบอัด</a></p>
{crop_action}
<p>เก็บ Gaussian Splat และ SH ทุกระดับจาก SPZ ชุดนี้ ไม่ต้องเทรนใหม่ การส่งออกใช้ CPU และมีคิวของตัวเอง</p>
<p>GLB นี้ใช้ <code>KHR_gaussian_splatting</code> ต้องเปิดด้วยตัวโหลดที่รองรับ ไฟล์อาจใหญ่กว่า SPZ
เพราะขยายข้อมูลกลับเป็นตัวเลข float32 หากเน้นดาวน์โหลดเร็ว ใช้ SPZ กับ Spark ต่อได้</p>
<form method="post" action="/actions"><input type="hidden" name="action" value="export_glb">
<input type="hidden" name="compression" value="{identifier}"><input type="hidden" name="return_to" value="/exports/glb/{identifier}">
<button type="submit" id="glb-start" {'disabled' if state['state'] in ('queued','running','ready') else ''}>เตรียม GLB</button></form>
<p id="glb-status" role="status" aria-live="polite"></p><progress id="glb-progress" max="100" value="0" aria-label="ความคืบหน้าส่งออก GLB"></progress>
<div class="actions" id="glb-ready" {'hidden' if state['state'] != 'ready' else ''}>
<a class="button primary" href="/glb-download/{identifier}.glb" download="model.glb">ดาวน์โหลด model.glb</a>
<a class="button" href="/glb-viewer/{identifier}" target="_blank" rel="noopener">เปิดดูใน Three.js</a>
<a class="button" href="/glb-example" download="index.html">ดาวน์โหลดตัวอย่าง index.html</a></div>
<p id="glb-details"></p></section>
<section class="panel" id="glb-package"><h2>ชุดพร้อมเปิดต่อ · ZIP</h2>
<p>รวม model.glb, ตัวอย่าง Three.js และไฟล์แสดงผลทั้งหมด เปิดดูแบบออฟไลน์ได้หลังแตก ZIP
พร้อมตัวเปิดสำหรับ Windows / Ubuntu และคู่มือ ต้องมี Python 3.10 ขึ้นไปเพื่อเปิดเว็บในเครื่อง</p>
<form method="post" action="/actions"><input type="hidden" name="action" value="package_glb">
<input type="hidden" name="compression" value="{identifier}"><input type="hidden" name="return_to" value="/exports/glb/{identifier}">
<button type="submit" id="package-start" disabled>เตรียม ZIP พร้อมเปิด</button></form>
<p id="package-status" role="status" aria-live="polite"></p>
<progress id="package-progress" max="100" value="0" aria-label="ความคืบหน้าเตรียม ZIP" hidden></progress>
<a class="button primary" id="package-download" download="threejs-model.zip" hidden>ดาวน์โหลด ZIP พร้อมเปิด</a>
<p>แตก ZIP ทั้งหมด → Windows เปิด START_WINDOWS.cmd / Ubuntu รัน bash start_ubuntu.sh
→ เบราว์เซอร์เปิดโมเดลให้ ไม่ต้องจัดไฟล์เอง</p></section>
<section class="panel"><h2>นำไปเปิดในงานของคุณ</h2><ol>
<li>ดาวน์โหลด <strong>model.glb</strong> และ <strong>index.html</strong> เก็บในโฟลเดอร์เดียวกัน</li>
<li>เปิด Terminal ในโฟลเดอร์นั้น แล้วรัน <code>python -m http.server 8080</code></li>
<li>เปิด <a href="http://localhost:8080" target="_blank" rel="noopener">http://localhost:8080</a> ตัวอย่างโหลด Three.js 0.186.1 จาก CDN จึงต้องเชื่อมอินเทอร์เน็ต</li>
<li>ลากเพื่อหมุนดู เลื่อนลูกล้อเพื่อซูม ลากปุ่มขวาเพื่อเลื่อน หรือกดกลับมุมเริ่มต้น หากนำไปเพิ่มในโปรเจกต์ ให้ดูโค้ดใน index.html</li></ol>
<p>ใช้ <code>GLTFLoader</code> ร่วมกับ <code>GLTFGaussianSplatLoaderExtension</code> และ <code>WebGPURenderer</code>
ตัวอย่างเลือก WebGPU เมื่อใช้ได้ และลอง WebGL2 ผ่าน WebGPURenderer เมื่อเริ่ม WebGPU ไม่สำเร็จ</p>
<p>OBJ และ GLB แบบ Mesh มีผิวสามเหลี่ยม ต้องสร้าง Mesh เพิ่มจากภาพ/ข้อมูลความลึกก่อน
ไฟล์นี้ยังใช้แก้ผิวหรือวัดระยะจริงแทน Mesh ไม่ได้ และไม่ได้เพิ่มรายละเอียดที่สูญเสียไปตอนบีบอัด SPZ</p></section>
<script id="glb-initial" type="application/json">{initial}</script>
<script id="package-initial" type="application/json">{package_initial}</script><script type="module" src="/assets/glb_export.mjs"></script>'''
