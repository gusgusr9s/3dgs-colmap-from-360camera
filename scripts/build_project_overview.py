"""Generate the standalone Thai project capability overview (no external assets)."""
import html
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# Status distinguishes implemented behavior from support that depends on input/hardware.
FEATURES=[
('นำเข้าและจัดการ','อัปโหลดภาพและวิดีโอ','พร้อมใช้งาน','รับไฟล์ภาพ/วิดีโอพร้อมเปอร์เซ็นต์ จำนวนไบต์ ยกเลิกระหว่างส่ง และลองใหม่ ก่อนเพิ่มไฟล์ลงรายการเมื่อรับครบ','/sources'),
('นำเข้าและจัดการ','DJI / Insta360 360','มีเงื่อนไข','ใช้ MP4/MOV หรือภาพ panorama ที่ stitch แล้วแบบ equirectangular 2:1; INSV/INSP ต้อง export จากโปรแกรมกล้องก่อน','/tutorial#upload'),
('นำเข้าและจัดการ','ไฟล์ตัวอย่างและวิดีโอทดลอง','พร้อมใช้งาน','มีคลิปสั้นใน data/raw และสคริปต์ดาวน์โหลดฉากเพิ่ม พร้อมบันทึกที่มาและสิทธิ์การใช้','/sources'),
('นำเข้าและจัดการ','ชื่อไม่เขียนทับงานเดิม','พร้อมใช้งาน','ชื่อ Job / Source ซ้ำเติมลำดับ รุ่น Model ใช้เวลาและรหัสเฉพาะ แยกประวัติแต่ละรอบ','/jobs'),
('นำเข้าและจัดการ','ค้นหา เรียงลำดับ และแท็บ','พร้อมใช้งาน','Sources / Jobs / Models ค้นหาและเรียงชื่อ เวลา ขนาด; Models แยก PLY / SPZ และสลับรายการ/การ์ดได้','/models'),
('นำเข้าและจัดการ','Source → Views → Model','พร้อมใช้งาน','ย้อนกลับหาต้นฉบับ ชุด Views และรุ่น Model ได้ เปลี่ยนชื่อ/ลบพร้อมตัวเลือกข้อมูลที่เชื่อมกัน','/sources'),
('เตรียมภาพ','เลือกช่วงวิดีโอก่อนเริ่ม','พร้อมใช้งาน','Play/seek, ระบุเวลาเริ่ม–จบ, เก็บทุก N เฟรม และดูตัวอย่างทิศทาง/FOV โดยไม่แก้ไฟล์ต้นฉบับ','/sources'),
('เตรียมภาพ','Perspective Views จากภาพ 360','พร้อมใช้งาน','แตกหลายทิศจาก panorama หรือเก็บภาพ walkthrough พร้อม transforms.json และเลขเฟรมต้นทาง','/jobs'),
('เตรียมภาพ','Preview / High / Ultra','พร้อมใช้งาน','เลือก preset และคำแนะนำจาก metadata/ประวัติ ปรับเองได้; Ultra ไม่ใช่การกู้รายละเอียดที่ต้นฉบับไม่มี','/tutorial#profile'),
('เตรียมภาพ','PNG 2048 จากต้นฉบับ','มีเงื่อนไข','ตัวเลือกหน้าเตรียม Model ใช้ต้นฉบับ panorama และ poses ที่เข้ากัน เพิ่มพื้นที่/เวลา ต้องวัดผลจริง','/jobs'),
('เตรียมภาพ','ตรวจเบลอและภาพซ้ำ','พร้อมใช้งาน','ตรวจรายละเอียด/ความคล้ายภาพ เลือกข้ามตำแหน่งที่คุณภาพต่ำโดยรักษาภาพซ้อนและจุดประเมินไว้','/jobs'),
('เตรียมภาพ','Preview / ปรับพื้นที่ Mask','พร้อมใช้งาน','เลือกซ่อนบริเวณล่าง เก็บพื้นเพิ่ม หรือใช้เฉพาะ Mask คน; พรีวิวแนวพื้นแยกจาก Mask คนที่คำนวณจริง','/jobs'),
('ตำแหน่งกล้อง','Spherical COLMAP','มีเงื่อนไข','หา pose หนึ่งตำแหน่งต่อเฟรม 360 แล้วใช้ศูนย์กลางร่วมกันทุกทิศ ต้องมีภาพซ้อนและการเคลื่อนกล้องจริง','/jobs'),
('ตำแหน่งกล้อง','Camera Preview 3D','พร้อมใช้งาน','ดูเส้นทาง จุดฉาก sparse กรอบภาพจริง มองจากกล้อง และตรวจช่วงตำแหน่งกระโดดก่อนเทรน','/jobs'),
('ตำแหน่งกล้อง','เลือกกล้องบางส่วนไป Train','พร้อมใช้งาน','เลือกช่วง 10 จุดต่อเนื่อง เพิ่ม/ตัดจุด หรือวาดวงเลือกตำแหน่ง แล้วสร้าง dataset ของรุ่นใหม่','/jobs'),
('ตำแหน่งกล้อง','หลายห้องและหลายชั้น','มีเงื่อนไข','ถ่ายต่อเนื่องผ่านประตู/บันไดพร้อมภาพเชื่อมได้ตามคุณภาพ pose; ยังไม่มีตัวเลือกชั้นและระบบเดินตามระดับพื้นอัตโนมัติ','/tutorial#record'),
('GPU และงานเบื้องหลัง','GPU สำหรับ Views','มีเงื่อนไข','WGPU ผ่าน Vulkan/D3D12 รองรับ AMD/NVIDIA/Intel เมื่อมี backend; auto ลองทางเลือกก่อน CPU และบอกเหตุผล fallback','/benchmarks'),
('GPU และงานเบื้องหลัง','GPU สำหรับ COLMAP','มีเงื่อนไข','CUDA เมื่อ executable และ NVIDIA driver รองรับ; auto กลับ CPU เมื่อ GPU ใช้ไม่ได้ การระบุ GPU ตรงจะแสดง error','/benchmarks'),
('GPU และงานเบื้องหลัง','GPU สำหรับ Mask คน','มีเงื่อนไข','Torch CUDA → DirectML บน Windows → CPU ในโหมด auto ตามความพร้อมของเครื่อง','/benchmarks'),
('GPU และงานเบื้องหลัง','Brush Gaussian Splat Training','มีเงื่อนไข','ใช้ WGPU/Vulkan บน GPU ที่รองรับ รวม RTX; ไม่จำเป็นต้องมี Torch CUDA เพื่อให้ Brush ใช้ GPU','/jobs'),
('GPU และงานเบื้องหลัง','คิวกลางและป้องกันงานชน','พร้อมใช้งาน','จัดคิวงานใช้ GPU ข้าม Job และเซิร์ฟเวอร์ มีสถานะรอคิว/ยกเลิกรอ ส่วน CPU export มีคิวแยก ไม่ได้แบ่ง VRAM แบบรันพร้อมกัน','/jobs'),
('GPU และงานเบื้องหลัง','Progress / ETA / เวลาแต่ละรอบ','พร้อมใช้งาน','Views/Model มีความคืบหน้า เวลาใช้จริง และ ETA จากข้อมูลที่มี ไม่รับประกันเวลาจบและงด ETA เมื่อข้อมูลไม่พอ','/jobs'),
('GPU และงานเบื้องหลัง','Windows / Ubuntu','มีเงื่อนไข','มี setup/run scripts สำหรับ Windows native และ Ubuntu/CUDA พร้อม check runtime; ฮาร์ดแวร์/driver ต้องรองรับจริง','/tutorial'),
('คุณภาพและหลักฐาน','Quality gate ก่อนเผยแพร่','พร้อมใช้งาน','รุ่นที่สร้างใหม่ทั้งกดเองและอัตโนมัติต้องมีภาพประเมินอย่างน้อย 3 ภาพ คะแนนอย่างน้อย 18 dB และไม่มีภาพ/Mask อ่านไม่ครบ ไม่แก้ผลรุ่นเก่าย้อนหลัง','/experiments'),
('คุณภาพและหลักฐาน','แยกภาพทดสอบตามตำแหน่ง','พร้อมใช้งาน','รุ่นใหม่กันทุกมุมจากตำแหน่งทดสอบออกจาก Train; ประวัติรุ่นเก่ายังใช้ split เดิมและต้องสร้างรุ่นใหม่เพื่อประเมินแบบนี้','/experiments'),
('คุณภาพและหลักฐาน','หน้าเทียบภาพมุมเดียวกัน','พร้อมใช้งาน','ภาพต้นทาง/ภาพเตรียม/Brush render และเว็บ PLY เต็ม/รุ่นโหลดเร็ว พร้อมภาพคู่ ลากเทียบ รุ่นก่อน/Mask และซูม','/experiments'),
('คุณภาพและหลักฐาน','PSNR / SSIM และข้อจำกัด','มีเงื่อนไข','วัดภาพที่รองรับและพิกเซลนอก Mask; ไม่ใช้คะแนนแทนความถูกต้องของ geometry หรือมุมที่ไม่เคยถ่าย','/experiments'),
('คุณภาพและหลักฐาน','ผลทดลอง GPU / Cloud','พร้อมใช้งาน','ตารางผลเครื่องจริงและค่าประมาณมีที่มาแยกกัน พร้อมเครื่องคำนวณ ไม่อ้างว่าเทียบ Cloud ฉากเดียวกันแล้ว','/benchmarks'),
('ดูโมเดล','Viewer และ Scenes / Maps','พร้อมใช้งาน','เปิด Gaussian PLY/SPZ หรือเลือกหลายโมเดลมาจัดตำแหน่งใน Scene บันทึก/เปิดต่อ พร้อมพื้นและกำแพงตรวจชน และส่งออกกล่องเป็น OBJ','/scenes'),
('ดูโมเดล','Mouse lock / คีย์บอร์ด / Xbox','มีเงื่อนไข','WASD ความเร็ว ความสูง ล็อก/ปล่อยเมาส์ และจอย Xbox ตาม browser/device; ไม่ใช่ระบบ collision กับผนัง','/models'),
('ดูโมเดล','Capture timeline / Mini-map','มีเงื่อนไข','เลื่อนไปจุดถ่าย เปิดภาพจริง และคลิกตำแหน่งจากแผนที่ได้เมื่อโมเดลมี metadata กล้อง','/models'),
('ดูโมเดล','Inspector / Focus / Config','พร้อมใช้งาน','ซ่อนเครื่องมือขยายพื้นที่ ดู config ของรุ่นนั้น และกลับไปภาพเทรน/ต้นทางได้เมื่อข้อมูลยังอยู่','/models'),
('ดูโมเดล','Fine tune เป็นรุ่นใหม่','มีเงื่อนไข','ใช้ checkpoint และ dataset ที่ตรงกับรุ่นต้นทาง; รุ่นที่ตัด/ลบจุดแล้วไม่มี checkpoint ตรงกันจึงทำต่อจากรุ่นนั้นไม่ได้','/models'),
('Editor','กรอบห้องหมุนได้','พร้อมใช้งาน','ปรับหกด้านและ yaw เลือกจากศูนย์กลางหรือ Gaussian ทั้งก้อน พร้อมเทียบต้นฉบับ/ดูส่วนที่จะลบ','/models'),
('Editor','แปรงลบ / ลากวงเลือก','พร้อมใช้งาน','วาดบนภาพให้จุดเป็นสีแดงก่อนกดลบ เลือกชั้นหน้าโดยประมาณหรือเลือกทะลุได้ ไม่ใช่การแบ่งผิวตาม semantic','/models'),
('Editor','Undo / Redo และร่าง','พร้อมใช้งาน','ย้อนหลังสูงสุด 40 สถานะใน session ทั้งกรอบ/แต่ละ stroke/การลบ ร่างคืนได้หลังโหลดหน้าใหม่แต่ history ไม่ข้าม session','/models'),
('Editor','รักษาต้นฉบับและค่าจุด','พร้อมใช้งาน','บันทึก PLY ชื่อใหม่ เก็บทุก property/SH ของจุดที่เหลือ และสร้าง SPZ ต่อ โดยตรวจ mask/hash กับพรีวิว','/models'),
('ส่งออก','Gaussian PLY / SPZ','พร้อมใช้งาน','ดาวน์โหลด PLY เต็มหรือ SPZ บีบอัด แต่ละรุ่นมี record/lineage และไม่เปลี่ยนคุณภาพต้นทางให้ดีขึ้นเอง','/models#compressed-models'),
('ส่งออก','GLB สำหรับ Three.js','พร้อมใช้งาน','KHR_gaussian_splatting เก็บทุก SH ที่ SPZ มี ต้องมี loader/renderer ที่รองรับ ไม่ใช่ GLB แบบ mesh','/models#compressed-models'),
('ส่งออก','ZIP เปิดต่อแบบออฟไลน์','พร้อมใช้งาน','รวม GLB, ตัวอย่าง Three.js, dependencies/ใบอนุญาต, README และตัวเปิด Windows/Ubuntu; ต้องมี Python 3.10+ และ GPU/browser ที่รองรับ','/models#compressed-models'),
('ส่งออก','Mesh อัตโนมัติ / CAD / วัดเมตร','ยังไม่รองรับ','ยังไม่มีสร้าง mesh ทั้งฉาก พื้นแปลน หรือระยะเมตรจาก Gaussian โดยตรง; Scenes ส่งออก OBJ ของกล่องพื้น/กำแพงที่ผู้ใช้วางไว้ได้','/project-overview#limits'),
('คู่มือและระบบ','Tutorial พร้อมเส้นทางจำลอง','พร้อมใช้งาน','สอน Record → Upload → Profile → Views → Camera Preview → Model พร้อมห้องเดียว ทางเดิน หลายห้อง และการวนซ้ำ','/tutorial'),
('คู่มือและระบบ','ไอคอน สี และกลุ่มคำสั่ง','พร้อมใช้งาน','SVG คู่ข้อความ: สั่งงาน/บันทึกสีเขียว เปิดหน้าสีฟ้า ดาวน์โหลดสีเหลือง ลบสีแดง พร้อมเมนูเครื่องมือรอง','/sources'),
('คู่มือและระบบ','ค้นหาและเรียง Gallery / ประวัติ','พร้อมใช้งาน','ค้นหาภาพทั้ง dataset ก่อนแบ่ง 48 ภาพต่อหน้า; ประวัติ/ผลงาน/ผลทดลองค้นหาได้โดยไม่แตะ Source หรือเริ่มงาน GPU','/jobs'),
('คู่มือและระบบ','Tests และรายงานตรวจจริง','พร้อมใช้งาน','มี Python/JavaScript regression และ Chromium review แยกจาก training; ผลทดสอบฮาร์ดแวร์จริงไม่ได้ยืนยันทุก GPU/OS','/project-overview#verification'),
]

def build():
    groups=list(dict.fromkeys(item[0] for item in FEATURES))
    cards=[]
    for category,title,status,description,path in FEATURES:
        kind={'พร้อมใช้งาน':'ready','มีเงื่อนไข':'conditional','ยังไม่รองรับ':'missing'}[status]
        cards.append(f'<article class="feature" data-category="{category}" data-state="{kind}"><div class="feature-top"><span class="category">{category}</span><span class="state {kind}">{status}</span></div><h3>{title}</h3><p>{description}</p><a data-app-path="{path}" href="{path}">เปิดส่วนนี้ <span aria-hidden="true">→</span></a></article>')
    template=(ROOT/'app/web/templates/project_overview_template.html').read_text(encoding='utf8')
    template=template.replace('__CARDS__',''.join(cards)).replace('__COUNT__',str(len(FEATURES)))
    template=template.replace('__CATEGORIES__',''.join(f'<option>{name}</option>' for name in groups))
    (ROOT/'docs/PROJECT_OVERVIEW.html').write_text(template,encoding='utf8')
    print(f'Built PROJECT_OVERVIEW.html: {len(FEATURES)} capabilities')

if __name__=='__main__':build()
