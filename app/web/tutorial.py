"""Current Thai capture and processing guide; preset values come from the app."""
from html import escape


def tutorial_content(view_profiles, training_profiles):
    chapters = [('record','ถือกล้องและเดินถ่าย'),('people','ถ่ายในพื้นที่คนเยอะ'),('upload','เพิ่ม Source'),
                ('profile','ค่าเก็บเฟรม / Profile'),('test-map','แผนที่ชั่วคราว'),('views','สร้างงานและคิว'),
                ('poses','ตรวจ Pose / Mask / Train'),('recovery','หยุดและทำต่อ'),('model','ตรวจ Model / Render'),
                ('cleanup','ตัดขอบและลบจุด'),('export','ส่งออกและเปิดที่อื่น'),('troubleshoot','แก้ปัญหาคุณภาพ')]
    navigation = ''.join(f'<a href="#{key}">{i} · {title}</a>' for i,(key,title) in enumerate(chapters,1))
    rows = ''.join(f'<tr><th>{escape(name.title())}</th><td>{preset["resolution"]}×{preset["resolution"]}</td>'
                   f'<td>{preset["video_resolution"]:,} px</td><td>{training_profiles[name]["steps"]:,}</td>'
                   f'<td>{training_profiles[name]["max_splats"]:,}</td></tr>' for name,preset in view_profiles.items())
    return f'''<div class="tutorial-layout">
    <nav class="tutorial-chapters" aria-label="บทเรียน">{navigation}</nav>
    <article class="tutorial-body">
    <section class="panel" id="record"><span class="eyebrow">01 / RECORD</span><h2>ถือกล้องและเดินถ่ายให้ได้ฉาก 3D</h2>
    <p>เริ่มจากวิดีโอ 360 ที่ stitch เป็น panorama อัตราส่วน 2:1 เช่น 7680×3840 กล้องต้องเปลี่ยนตำแหน่งให้เห็นวัตถุจากหลายจุด เพื่อคำนวณความลึก ฉากควรมีพื้นผิวที่เห็นซ้ำและแสงใกล้เคียงกัน</p>
    <h3>วิธีถือกล้อง</h3><ul>
      <li>เช็ดเลนส์ทั้งสองด้าน ใช้ไม้ถือกล้องที่มั่นคง ให้กล้องพ้นแนวศีรษะและลำตัวเท่าที่ถือได้สะดวก เพื่อลดพื้นที่ที่ผู้ถือบัง ตรวจคลิปสั้นว่ามือ แขน และเสื้อกินพื้นที่ภาพมากหรือไม่</li>
      <li>รักษาความสูงและแนวกล้องให้สม่ำเสมอ ลดการแกว่งไม้ การเอียงเร็ว และการยกขึ้นลง กล้อง 360 เห็นรอบตัว จึงไม่ต้องสะบัดกล้องตามวัตถุทุกชิ้น</li>
      <li>ใช้ความละเอียดต้นฉบับที่เหมาะกับกล้องและแสงให้เพียงพอ ตรวจขอบป้ายหรือเสาว่าคมขณะเดิน หากเบลอให้เดินช้าลงหรือปรับการถ่ายให้หยุดการเคลื่อนไหวได้ดีขึ้น</li>
      <li>หากกล้องรองรับ ให้รักษา exposure และ white balance ให้คงที่เมื่อแสงในบริเวณนั้นใกล้เคียงกัน ตรวจส่วนมืดและสว่างก่อนเริ่มทางยาว</li>
    </ul>
    <h3>วิธีเดิน</h3><ul>
      <li>ก้าวสั้นและสม่ำเสมอ เลี้ยวเป็นแนวโค้งช้า ๆ โดยเฉพาะเมื่ออยู่ใกล้ร้านค้า โต๊ะ หรือเสา เพราะภาพเปลี่ยนเร็วกว่าเมื่อมองวัตถุไกล</li>
      <li>เดินให้ด้านหน้า ด้านข้าง และด้านหลังของวัตถุสำคัญปรากฏจากหลายตำแหน่ง พยายามให้พื้นผิวเดิมเห็นได้อย่างน้อย 3 ภาพติดกันหรือใกล้กัน</li>
      <li>เว้นระยะจากผนังและสิ่งของให้เห็นรายละเอียดซ้อนกัน ผ่านประตูและจุดเชื่อมพื้นที่อย่างต่อเนื่อง ไม่กระโดดข้ามด้วยการตัดต่อหรือเปลี่ยนตำแหน่งเร็ว</li>
      <li>กลับมาบริเวณเริ่มต้นได้เพื่อเชื่อมเส้นทาง รอบเพิ่มควรเยื้องแนวเดิมหรือเก็บด้านที่ขาด การเดินทับรอยเดิมหลายรอบอาจเพิ่มเวลาโดยยังเหลือจุดบอดเดิม</li>
    </ul>
    <div class="tutorial-route-controls" role="group" aria-label="ตัวอย่างเส้นทาง"><button type="button" data-route="room" aria-pressed="true">ห้องเดียว</button><button type="button" data-route="corridor" aria-pressed="false">ทางเดิน</button><button type="button" data-route="rooms" aria-pressed="false">หลายห้อง</button><button type="button" data-route="repeat" aria-pressed="false">วนซ้ำแนวเดิม</button></div>
    <figure class="tutorial-route"><svg id="tutorial-route-svg" viewBox="0 0 680 330" role="img" aria-labelledby="tutorial-route-title tutorial-route-description"><title id="tutorial-route-title">เส้นทางจำลองสำหรับห้องเดียว</title><desc id="tutorial-route-description">เดินรอบห้อง แล้วเพิ่มแนวผ่านกลางให้เห็นฉากจากหลายตำแหน่ง</desc><defs><marker id="route-arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="m0 0 10 5-10 5Z" fill="#92e6c6"/></marker></defs><g id="tutorial-room-walls"><path d="M70 45H610V285H70Z" fill="#111e32" stroke="#577088" stroke-width="5"/><rect x="280" y="130" width="120" height="65" rx="10" fill="#233a50"/></g><path id="tutorial-route-path" d="M130 230L130 90L550 90L550 240L140 240L225 165L460 165" fill="none" stroke="#92e6c6" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#route-arrow)"/><circle id="tutorial-start" cx="130" cy="230" r="9" fill="#ffbc65"/><text id="tutorial-start-label" x="145" y="225" fill="#ffbc65">เริ่ม</text></svg><figcaption id="tutorial-route-caption" aria-live="polite">ภาพจำลอง: เดินรอบห้องแล้วเพิ่มแนวผ่านกลาง เพื่อเห็นฉากจากหลายตำแหน่ง</figcaption></figure>
    <p class="muted">หลักการอ้างอิง: <a href="https://colmap.github.io/tutorial.html#structure-from-motion" target="_blank" rel="noopener">แนวทางถ่ายภาพที่มีพื้นผิว ภาพซ้อน และหลายมุมมองของ COLMAP</a> คำแนะนำการถือและเดินข้างต้นนำหลักการนี้มาปรับใช้กับกล้อง 360</p></section>

    <section class="panel" id="people"><span class="eyebrow">02 / CROWDED SCENES</span><h2>ถ่ายในพื้นที่ที่มีคนเยอะ</h2>
    <p>คนที่เดินผ่านเปลี่ยนตำแหน่งระหว่างเฟรม ส่วนผู้ถือกล้องอาจบังฉากใกล้กล้องเป็นพื้นที่ใหญ่ โปรแกรมอาจจับจุดบนตัวคนและหา pose ผิด หรือเรียนรู้ภาพคนซ้อนระหว่าง Train</p>
    <ol><li>เลือกเวลาที่คนน้อยเมื่อทำได้ หากมีคนบังพื้นที่สำคัญ ให้รอจนเห็นฉากชัดแล้วเดินต่ออย่างนุ่มนวล</li><li>เลือกแนวที่ยังเห็นเสา ผนัง ป้าย หรือพื้นผิวเดิมต่อเนื่อง เลี่ยงการเดินชิดหรือเดินตามหลังคนจนเขาเต็มภาพ</li><li>เมื่อจุดสำคัญถูกบัง ให้กลับมาเก็บจากแนวเยื้องอีกด้านที่เห็นฉากจริง ตรวจช่วงเลี้ยวและช่วงกลับเข้าพื้นที่เดิมด้วย</li><li>ทดสอบแผนที่จากช่วงที่คนเยอะจริงและจุดเชื่อมฉากเพิ่มเติม ผลดีจากช่วงโล่งช่วงเดียวใช้ยืนยันทั้งคลิปไม่ได้</li></ol>
    <p>งานหา pose ใหม่ของวิดีโอ 360 จะ Mask คนบน panorama ก่อน COLMAP ดึงจุดภาพ และงาน Train มี Mask คนของภาพแต่ละมุมอีกขั้น Mask ช่วยข้ามข้อมูลคน แต่สร้างพื้นผิวที่ถูกบังตลอดคลิปกลับมาไม่ได้ หากฉากนิ่งเหลือน้อยยังต้องเลือกช่วงใหม่หรือถ่ายเพิ่ม</p>
    <p class="muted">อ้างอิงวิธีข้ามจุดภาพด้วย Mask: <a href="https://colmap.github.io/faq.html#mask-image-regions" target="_blank" rel="noopener">COLMAP · Mask image regions</a></p></section>

    <section class="panel" id="upload"><span class="eyebrow">03 / SOURCE</span><h2>เพิ่ม Source และตรวจวิดีโอ</h2>
    <ol><li>Export วิดีโอ 360 แบบ stitch แล้วจากซอฟต์แวร์ของกล้อง รักษาความละเอียดและคุณภาพ ไฟล์ reframe มุมเดียวจะไม่มีภาพรอบตัวครบ ส่วนไฟล์ดิบ INSV/INSP ต้อง Export ก่อน</li><li>เปิด <strong>Sources → + เพิ่ม Source</strong> อัปโหลดไฟล์ ดูเปอร์เซ็นต์และจำนวน bytes แล้วรอขั้นตรวจและบันทึกจนสำเร็จ</li><li>เปิดวิดีโอ ตรวจภาพ 2:1, FPS, ช่วงเบลอ, แสงเปลี่ยน และรอยต่อภาพ ก่อนกดสร้างงานจาก Source</li><li>กำหนดเวลาเริ่ม–จบจากคลิปจริง ใช้ช่วงสั้นที่ครอบคลุมบริเวณเดียวกันเมื่อเริ่มทดลอง การเลือกช่วงไม่เขียนทับไฟล์ต้นฉบับ</li></ol>
    <p>หน้าตั้งค่าแบ่งเป็น 3 ขั้น: <strong>ตั้งค่าเก็บเฟรม → ทดสอบแผนที่ → ขั้นสูงและสร้าง</strong> เลือกเวลาหรือมุมเพื่อดูภาพตัวอย่างก่อนเริ่มประมวลผลได้</p><a class="button" href="/sources">เปิด Sources</a></section>

    <section class="panel" id="profile"><span class="eyebrow">04 / SAMPLING &amp; PROFILE</span><h2>ตั้งค่าความถี่และความละเอียด</h2>
    <p>ค่าแนะนำเริ่มต้นคือประมาณ <strong>3 ตำแหน่งต่อวินาที</strong>: วิดีโอ 30 FPS เก็บทุก 10 เฟรม, 60 FPS ทุก 20 เฟรม และ 24 FPS ทุก 8 เฟรม คำนวณตาม FPS แล้วปัดเป็นจำนวนเต็ม วิดีโอ FPS ต่ำจะเก็บได้สูงสุดตามจำนวนเฟรมจริง คุณแก้ค่าเองได้</p>
    <p>เลข <strong>เก็บทุกกี่เฟรม</strong> ยิ่งน้อยยิ่งได้ตำแหน่งถี่ เช่น 30 FPS จากทุก 30 เป็นทุก 10 จะเพิ่มตำแหน่งประมาณ 3 เท่า แต่ไม่ได้เพิ่มความคมให้ภาพที่เบลออยู่แล้ว จำนวนภาพรวมยังคูณด้วยมุมรอบ panorama ดูแถบประมาณการและช่วงเวลาห่างก่อนสร้างจริง</p>
    <div class="tutorial-table"><table><thead><tr><th>Profile</th><th>ภาพ 360 ต่อมุม สูงสุด</th><th>วิดีโอปกติ ด้านยาวสูงสุด</th><th>Train steps</th><th>Splats สูงสุด</th></tr></thead><tbody>{rows}</tbody></table></div>
    <p><strong>Preview</strong> เหมาะกับการทดลองและตรวจทางกล้อง <strong>High</strong> ใช้สร้างภาพรวมและตรวจคุณภาพ <strong>Ultra</strong> เพิ่มงบภาพและ Train เมื่อฉากและ pose พร้อม ค่าที่ระบบคำนวณจริงอาจเล็กกว่าเพดานตามความละเอียด Source</p>
    <p>Source 8K กระจายพิกเซลรอบ 360° ทั้งใบ ส่วน Views เป็นภาพแต่ละมุม High/Ultra เพิ่มมุมเงยและก้มจากแนวนอน ใน Advanced ปรับขนาดภาพ FOV จำนวนมุม หรือใช้ Template จากงานสำเร็จได้ แล้วตรวจภาพซ้อนอีกครั้ง</p>
    <p>คำแนะนำหลังทดสอบ map ใช้ความถี่เริ่มต้นเดียวกันและอาจขอให้เก็บถี่ขึ้นเมื่อจุดภาพซ้อนไม่พอ ผลทดสอบมี pose กระโดดต้องตรวจแผนที่ก่อนใช้คำแนะนำ</p></section>

    <section class="panel" id="test-map"><span class="eyebrow">05 / TEMPORARY MAP</span><h2>ทดสอบแผนที่ชั่วคราวระหว่างทำงานอื่น</h2>
    <ol><li>ตั้งจุดเริ่มของช่วงที่ต้องการ แล้วเลือกทดสอบ <strong>5 หรือ 10 วินาที</strong> ต้องมีตำแหน่งอย่างน้อย 6 จุด หาก 5 วินาทีได้ภาพน้อย ระบบอาจเลือก 10 วินาทีให้</li><li>กด <strong>ทดสอบและคำนวณแผนที่ชั่วคราว</strong> พรีวิวเริ่มทันทีแยกจากคิว Views/Model โดยใช้ CPU และจำกัดงานพรีวิวไว้ 2 threads จึงทดสอบและเตรียมงานถัดไประหว่าง Train ได้</li><li>ดูเส้นทาง จุดกล้อง และภาพจากจุดที่เลือกในแผนที่ เปลี่ยนช่วงไปตรวจบริเวณคนบัง ช่วงเลี้ยว และทางเชื่อมด้วย</li><li>หากต้องการใช้ค่าที่แนะนำ กดปุ่มใช้ค่านั้น แล้วตรวจ Preview อีกครั้ง การเปลี่ยนฟอร์มหลังทดสอบทำให้ผลเดิมไม่ตรงกับค่าปัจจุบัน ต้องทดสอบใหม่</li></ol>
    <p>แผนที่ชั่วคราวมี Mask คนก่อนหา pose ภาพในหน้าตรวจเป็นภาพย่อเฉพาะมุมแนวนอน ส่วน pose คำนวณจาก panorama แบบเดียวกับงานจริง เก็บผลประมาณ 1 ชั่วโมง และใช้ผลเดิมสำหรับ Source/ค่าชุดเดียวกันที่ยังไม่หมดอายุ ไม่เพิ่ม Job และไม่ Train โมเดล การเปิดหน้าตั้งค่าเฉย ๆ ไม่เริ่มประมวลผล</p>
    <p>พรีวิวแยกคิวแต่ยังใช้ CPU และ RAM ของเครื่องร่วมกัน งานจริงจะคำนวณ pose จากช่วงทั้งหมดที่เลือกใหม่ ผลช่วงสั้นยังไม่รับรองคุณภาพทั้งคลิป</p></section>

    <section class="panel" id="views"><span class="eyebrow">06 / JOBS &amp; QUEUE</span><h2>สร้างงานจริงและเตรียมคิว</h2>
    <p>ในขั้น <strong>ขั้นสูงและสร้าง</strong> เลือกว่าจะประมวลผลถึงไหน:</p><ul><li><strong>สร้าง Model ครบทุกขั้นตอน:</strong> Views → Mask คนก่อนหา pose → Camera Pose → เตรียม Mask/ชุดภาพ Train → Train → ตรวจ Render และบันทึก Model</li><li><strong>สร้าง Views + Camera Pose แล้วหยุดตรวจ:</strong> ได้ภาพและตำแหน่งกล้องสำหรับตรวจและเลือกก่อน Train</li><li><strong>สร้างเฉพาะ Views:</strong> ตรวจภาพแต่ละมุมก่อนสั่งหา pose หรือสร้าง Model ต่อ</li></ul>
    <p>งานจริงเข้า <strong>คิวประมวลผล</strong> และเริ่มตามลำดับ คุณเตรียม Source อื่น ทดสอบ map และส่งงานเพิ่มเข้าคิวได้ระหว่างมีงานรัน ถ้างานยังรออยู่ ใช้ <strong>ยกเลิกรอคิว</strong> เพื่อถอนงานนั้น</p>
    <figure><img class="tutorial-image" src="/assets/tutorial/dji-view.jpg" width="1024" height="1024" loading="lazy" alt="ตัวอย่างภาพ Perspective View จากวิดีโอ 360"><figcaption>ภาพ View จริงตัวอย่าง ใช้ดูความคมและสิ่งที่บังฉาก ภาพแต่ละทิศจาก panorama เดียวกันมีตำแหน่งกล้องเดียวกัน</figcaption></figure>
    <p>เปิด Job เพื่อดูประวัติ Views, Models จากชุดนั้น, Check config และ log ค่าในประวัติเป็นค่าที่ใช้กับรอบนั้น การแก้ค่า Source สำหรับงานใหม่ไม่เปลี่ยนภาพของรอบเก่า</p><a class="button" href="/jobs">เปิด Jobs</a></section>

    <section class="panel" id="poses"><span class="eyebrow">07 / POSES &amp; TRAIN</span><h2>ตรวจตำแหน่งกล้องและภาพที่ใช้ Train</h2>
    <p><strong>Pose</strong> คือตำแหน่งและทิศกล้องที่คำนวณให้แต่ละภาพ กล้องลงทะเบียนครบไม่ได้รับรองว่าทุกตำแหน่งถูกต้อง ตรวจเส้นที่กระโดดไปไกลแล้วกลับทันที และเทียบภาพสองข้างว่าการเคลื่อนจริงต่อเนื่องหรือไม่ สเกลแผนที่เป็นหน่วยสัมพัทธ์ ยังไม่ใช่เมตร</p>
    <figure><img class="tutorial-image tutorial-pose-image" src="/assets/tutorial/dji-poses.svg" width="900" height="520" loading="lazy" alt="เส้นทางกล้องและจุดฉากจาก Camera Preview"><figcaption>ข้อมูลจริงจาก Camera Preview ตัวอย่าง จุด sparse ช่วยตรวจโครงฉากและตำแหน่ง ไม่ใช่ภาพหน้าจอหรือพื้นผิว Model สำเร็จ</figcaption></figure>
    <p>ลากหมุนภาพรวม ลากขวาเลื่อน และใช้ล้อเมาส์ซูม เลือกจุดกล้องเพื่อเปิดภาพจริง เลือกช่วงหรือลากวงเลือกตำแหน่งที่จะ Train ได้ โดยแต่ละตำแหน่งรวมทุกมุมของ panorama นั้น ต้องเลือกหลายตำแหน่งที่ยังเห็นฉากร่วมกัน</p>
    <p>เปิด <strong>ตรวจภาพ / Mask ก่อนสร้าง Model</strong> เพื่อดูภาพตัวอย่างและเลือกการเตรียมภาพ:</p><ul><li><strong>ใช้ Views ปัจจุบัน:</strong> เริ่มจากชุดภาพที่เตรียมไว้</li><li><strong>PNG 2048 จากต้นฉบับ:</strong> ดึงรายละเอียดจากวิดีโอ 360 ใหม่ ใช้เวลาและพื้นที่เพิ่ม และยังใช้ pose เดิม</li><li><strong>พื้นที่พื้น:</strong> ซ่อนบริเวณล่างตามปกติ, เก็บพื้นเพิ่ม, หรือเก็บพื้นทั้งหมดโดยใช้ Mask คน ตรวจว่าเท้าและไม้ถือกล้องยังติดหรือไม่</li><li><strong>ข้ามภาพรายละเอียดต่ำ:</strong> ระบบคัดออกอย่างระมัดระวังและบันทึกตำแหน่งที่ข้าม ผนังเรียบอาจมีคะแนนต่ำได้เช่นกัน</li></ul>
    <p>สีแดงในพรีวิวพื้นที่พื้นเป็นแนวที่จะข้าม ยังไม่ใช่ผลตรวจคนจริงของรอบใหม่ Mask สำหรับหา pose และ Mask สำหรับ Train เป็นคนละขั้นและใช้คนละภาพ การเลือกพื้นที่พื้นก่อน Train ไม่ได้คำนวณ pose เดิมใหม่</p></section>

    <section class="panel" id="recovery"><span class="eyebrow">08 / STOP &amp; RESUME</span><h2>หยุด ทำต่อ และอ่านความคืบหน้า</h2>
    <p>สถานะงานแสดงขั้นเตรียมภาพ, Mask, features/matching, poses, training และ export พร้อมเวลา อุปกรณ์ และ log ใช้ปุ่ม <strong>หยุด</strong> ของงานเมื่อต้องการให้ระบบเก็บส่วนที่ทำเสร็จ แล้วรอจนสถานะหยุดเรียบร้อย</p>
    <p>หากมี checkpoint ที่ใช้ได้ แอปจะแสดงทางเลือกทำต่อจาก COLMAP หรือ Train ตามข้อมูลที่เหลือ การกู้ pose ใช้ features/matches เดิม จึงไม่เพิ่ม Mask คนให้ฐานข้อมูลเก่าย้อนหลัง หากต้องการเปลี่ยนสูตรหา pose ให้เริ่มคำนวณใหม่จาก Views</p>
    <p>ใช้ <strong>ปรับละเอียดต่อ</strong> จาก Model เมื่อภาพและ pose ดีแต่ยังต้องการฝึกเพิ่ม แล้วเทียบผลกับรุ่นเดิม งานที่ทำต่อจะมีรุ่นและประวัติของตัวเอง เพิ่ม steps ไม่สามารถแก้ pose ที่วางผิดหรือพื้นผิวที่ไม่เคยเห็นได้</p></section>

    <section class="panel" id="model"><span class="eyebrow">09 / REVIEW</span><h2>เปิด Model และตรวจ Render</h2>
    <ol><li>เปิด Models แล้วเลือกผลงาน ใช้ <strong>มองจากจุดถ่าย</strong> และ Capture timeline เลื่อนไปตามแนวกล้อง เปิดภาพจริงเพื่อเทียบ</li><li><strong>Walk + ล็อกเมาส์:</strong> มองด้วยเมาส์ เดินด้วย WASD และกด Esc เพื่อปล่อยเมาส์ หากล็อกไม่ได้ใช้การลากมองแทน <strong>Free view</strong> ใช้บินสำรวจ และ <strong>Orbit</strong> ใช้หมุนภาพรวม</li><li>เปิด Mini-map และ Inspector เพื่อดูตำแหน่งกล้อง config อุปกรณ์และคุณภาพ Capture timeline/Mini-map ต้องมี metadata กล้อง หากอัปโหลด PLY อย่างเดียวให้เริ่มจาก Orbit/Free view</li><li>เปิด <strong>ตรวจภาพ Render</strong> เทียบ View อ้างอิงหรือภาพต้นฉบับกับ render ซูม 150% / 200% ตรวจเสา ขอบป้าย ผนัง พื้น และเงาคน เปิดมุมเดียวกันใน PLY เต็มหรือรุ่นโหลดเร็วได้</li></ol>
    <p>รุ่นใหม่แยกตำแหน่งทดสอบออกจากตำแหน่งที่ Train Gaussian ค่า PSNR วัดเฉพาะพิกเซลที่ไม่ถูก Mask จึงต้องดูสัดส่วนพิกเซลที่เหลือและภาพจริงร่วมกัน คะแนนดีในบริเวณที่เหลือไม่ยืนยันว่าฉากส่วนที่ถูกบังสมบูรณ์</p>
    <p>ตรวจทั้งมุมตามแนวถ่ายและมุมเยื้องออกมา หากภาพตามแนวถ่ายดีแต่พอออกนอกแนวแล้วแตก อาจต้องเก็บมุมเพิ่ม แสงสะท้อน กระจก และผิวเรียบยังเป็นบริเวณที่สร้างยาก</p><a class="button" href="/models">เปิด Models</a></section>

    <section class="panel" id="cleanup"><span class="eyebrow">10 / EDIT</span><h2>ตัดขอบห้องและลบจุดฟุ้ง</h2>
    <ol><li>จาก Model เปิด <strong>ตัดขอบห้อง</strong> ปรับกรอบ หมุนตามแนวห้อง และตรวจว่าพื้นผิวที่ต้องการยังอยู่ครบ</li><li>ใช้ <strong>แปรงลบ</strong> หรือ <strong>ลากวงเลือก</strong> จุดที่เลือกจะแสดงสีแดงก่อนกดลบ ค่า <strong>เลือกชั้นด้านหน้า</strong> เป็นการประมาณจากตำแหน่งจุด ปิดเพื่อเลือกทะลุได้ แต่ต้องหมุนตรวจด้านหลังด้วย</li><li>ใช้ <strong>ย้อนกลับ / ทำซ้ำ</strong> กับการเลือกและการลบได้ เมื่อพร้อมกด <strong>บันทึกเป็นรุ่นใหม่</strong> แล้วเปิดเทียบต้นฉบับ</li></ol>
    <p>กรอบเริ่มต้นเป็นเพียงตัวช่วย ยังไม่ได้ตรวจแนวผนังจริง หากผนังหรือพื้นหายให้ขยายกรอบหรือตรวจตัวเลือกเก็บเฉพาะก้อนที่อยู่ในกรอบทั้งก้อน การตัดขอบช่วยเอาส่วนเกินออก แต่ไม่เติมพื้นผิวที่ขาด และรุ่นหลังตัดต้องประเมิน Render แยกจากต้นทาง</p></section>

    <section class="panel" id="export"><span class="eyebrow">11 / EXPORT</span><h2>ส่งออก PLY, SPZ, GLB และ ZIP พร้อมเปิด</h2>
    <p>ดาวน์โหลด <strong>PLY</strong> ของรุ่นที่ต้องการ หรือสร้าง <strong>SPZ</strong> เพื่อลดขนาดไฟล์และตรวจผลรุ่นบีบอัด การบีบอัดไม่ได้ลดจำนวน Gaussian ที่ต้องแสดงเสมอไป</p>
    <p>ในหน้ารุ่นบีบอัดส่งออกเป็น <strong>GLB</strong> ได้ ไฟล์นี้บรรจุ Gaussian Splat และต้องใช้ตัวแสดงผลที่รองรับการแสดง splat อย่าคาดว่าจะเปิดเป็นผิว mesh ทั่วไปได้ทุกโปรแกรม</p>
    <p>กด <strong>เตรียม ZIP พร้อมเปิด</strong> เพื่อรวม Model, ตัวอย่าง Three.js, ไฟล์แสดงผลและคู่มือในชุดเดียว แตก ZIP แล้วใช้ <strong>START_WINDOWS.cmd</strong> บน Windows หรือ <strong>start_ubuntu.sh</strong> บน Ubuntu ต้องมี Python 3.10+ และ browser/GPU ที่รองรับ เปิดผ่านตัวเซิร์ฟเวอร์ในชุด ไม่ดับเบิลคลิก HTML โดยตรง เมื่อเตรียมชุดครบแล้วใช้ดูแบบไม่ต่ออินเทอร์เน็ตได้</p></section>

    <section class="panel" id="troubleshoot"><span class="eyebrow">12 / TROUBLESHOOT</span><h2>เลือกแก้ให้ตรงกับอาการ</h2>
    <div class="tutorial-table"><table><thead><tr><th>อาการ</th><th>สิ่งที่ควรตรวจและลองก่อน</th></tr></thead><tbody>
      <tr><td>ภาพซ้อน เสาบิด หรือกล้องกระโดด</td><td>ดู pose กับภาพจริง ตรวจคนบัง ลดระยะเก็บเฟรม และทดลอง map ช่วงนั้นใหม่</td></tr>
      <tr><td>คนเป็นเงา หรือฉากหลังคนหาย</td><td>ตรวจ Mask และช่วงที่เห็นฉากนิ่ง ถ้าถูกบังทุกภาพต้องเก็บภาพเพิ่ม</td></tr>
      <tr><td>เบลอตั้งแต่ View ต้นฉบับ</td><td>แก้การเดิน ความนิ่ง แสง และช่วงคลิปก่อนเพิ่ม resolution หรือ steps</td></tr>
      <tr><td>พื้นหรือขอบห้องหาย</td><td>ตรวจพื้นที่ที่ Mask ข้ามและกรอบตัดโมเดล เทียบกับภาพต้นทาง</td></tr>
      <tr><td>รอนานและจำนวนภาพมาก</td><td>ตรวจจำนวนตำแหน่ง × มุม เลือกช่วงตามพื้นที่ ใช้ map ชั่วคราวตรวจค่าก่อนส่งงานจริง</td></tr>
    </tbody></table></div><p>เริ่มจากภาพที่ชัด ฉากที่เห็นซ้ำ และ pose ที่ต่อเนื่อง แล้วค่อยเพิ่มคุณภาพ Train ใช้ Check config และประวัติรุ่นเพื่อเปรียบเทียบผลจากค่าที่เปลี่ยนจริง</p>
    <a class="button" href="/sources">เริ่มจาก Source</a><a class="button" href="#record">กลับไปวิธีถ่าย</a></section>
    </article></div><script type="module" src="/assets/tutorial.mjs"></script>'''
