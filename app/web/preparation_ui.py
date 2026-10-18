"""Simple optional review before starting a model; defaults need one click."""
import html
from urllib.parse import quote


def preparation_content(job, label, report, busy=False, panorama=True, actual_masks=()):
    key = quote(job, safe='')
    samples = report['samples']
    flagged = sum(bool(s['warning']) for s in samples)
    chosen = sorted(samples, key=lambda s: (not bool(s['warning']), s['frame_index']))[:6]
    cards = []
    for item in chosen:
        image = f'/api/preparation-image/{key}?frame={quote(item["name"], safe="")}'
        actual = f'<a href="{image}&actual=1" target="_blank" rel="noopener">ดู Mask คนและพื้นที่ที่คำนวณไว้เดิม ↗</a>' if item['name'] in actual_masks else ''
        cards.append(f'''<figure class="preparation-card"><img loading="lazy" src="{image}&floor=standard" data-floor-preview="{image}" alt="ภาพตำแหน่ง {item['frame_index']} พร้อมพื้นที่ข้าม"><figcaption>เฟรม {item['frame_index']} · {html.escape(item['warning'] or 'ตรวจภาพจริงประกอบ')}<br>{actual}</figcaption></figure>''')
    disabled = ' disabled' if busy else ''
    detail_disabled = '' if panorama else ' disabled'
    return f'''<section class="panel preparation-panel"><a href="/job/{key}">← กลับไป Views</a>
      <h2>ตรวจภาพก่อนสร้าง Model</h2><p>{html.escape(label)} · {report['positions']:,} ตำแหน่ง</p>
      <p role="status">ตรวจตัวอย่าง {len(samples)} ตำแหน่ง · {flagged} ตำแหน่งควรดูเพิ่ม คะแนนเบื้องต้นอาจต่ำจากผนังเรียบได้</p>
      <form method="post" action="/actions" data-working>
        <input type="hidden" name="action" value="generate3d"><input type="hidden" name="job" value="{key}">
        <input type="hidden" name="return_to" value="/job/{key}"><input type="hidden" name="preparation_options" value="1">
        <fieldset{disabled}><legend>ใช้ค่าที่แนะนำแล้วเริ่มได้เลย</legend>
          <label class="field">รายละเอียดภาพ<select name="detail"><option value="existing">ใช้ Views ปัจจุบัน · แนะนำเริ่มที่นี่</option><option value="source_2048"{detail_disabled}>ดึงจากวิดีโอ 360 ต้นฉบับ · PNG 2048 · ใช้พื้นที่และเวลามากขึ้น</option></select></label>
          <label class="field">พื้นที่พื้น / ไม้ถือกล้อง (วิดีโอ 360)<select name="floor" id="preparation-floor"{detail_disabled}><option value="standard">ปกติ · ซ่อนบริเวณล่าง</option><option value="more_floor">เก็บพื้นเพิ่ม · ตรวจว่ามีไม้หรือเท้าติดมาหรือไม่</option><option value="person_only">เก็บพื้นทั้งหมด · ใช้เฉพาะ Mask คน</option></select></label>
          <p class="muted">สีแดงเป็นแนวพื้นที่ล่างที่จะข้าม ส่วน Mask คนจะคำนวณตอนเตรียม Model พรีวิวนี้ยังไม่ใช่ Mask คนจริง</p>
          <div class="preparation-grid">{''.join(cards) or '<p>ยังไม่มีภาพตัวอย่าง</p>'}</div>
          <details><summary>ตัวเลือกเพิ่มเติม</summary><label class="check"><input type="checkbox" name="filter_blur" value="1" checked>ข้ามตำแหน่งที่รายละเอียดต่ำมาก เมื่อยังรักษาระยะภาพซ้อนทับได้</label><p class="muted">เก็บจุดต้นทาง ปลายทาง และจุดทดสอบไว้ ตรวจทั้งชุดก่อนเทรน และบันทึกเฟรมที่ข้ามในประวัติ</p></details>
          <p>ระบบจัดคิวให้และตรวจภาพจากตำแหน่งที่ไม่ได้ใช้เทรนก่อนเผยแพร่ แต่ละรอบได้ Model รุ่นใหม่</p>
          <button type="submit" class="primary">{'งานนี้กำลังทำงานอยู่' if busy else 'สร้าง Model ด้วยค่าที่เลือก'}</button>
        </fieldset>
      </form></section><script type="module" src="/assets/preparation.mjs"></script>'''
