// Shared visual cues. SVGs are decorative; the original text and listeners remain intact.
import {setupCollections,setupTables,setupChapterSearch} from './collections.mjs';
const paths={
  trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  edit:'m14 4 6 6M4 20l5-1L21 7l-6-6L3 13z',
  brush:'m14 4 6 6M8 15 20 3l2 2-12 12M8 15c-5-2-2 5-6 6 5 1 9-1 8-4',
  lasso:'M8 19C1 17 2 5 12 4s14 12 4 14H9c-5 0-5 5 0 4',
  cube:'m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5m-9 5v8',
  download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  save:'M4 3h13l4 4v14H3V3zM7 3v6h9V3M7 21v-8h10v8',
  upload:'M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5',
  arrow:'M4 12h16m-6-6 6 6-6 6', back:'M20 12H4m6-6-6 6 6 6',
  play:'m8 4 12 8-12 8z', camera:'M3 7h4l2-3h6l2 3h4v14H3zM12 10a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
  image:'M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M8 7h.01',
  settings:'M4 7h16M4 17h16M9 4v6m6 4v6', info:'M12 11v6m0-10h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  reset:'M3 4v6h6M4 10a8 8 0 1 1 1 8', undo:'M9 4 3 10l6 6M3 10h10a7 7 0 0 1 7 7',
  redo:'m15 4 6 6-6 6M21 10H11a7 7 0 0 0-7 7',
  eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12m13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  move:'M12 3v18M3 12h18m-12-6 3-3 3 3m-6 12 3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3',
  lock:'M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4',
  map:'m3 5 6-2 6 3 6-3v16l-6 3-6-3-6 2zM9 3v16m6-13v16',
  gamepad:'M7 7h10c5 0 7 12 3 13-2 0-4-4-5-4H9c-1 0-3 4-5 4C0 19 2 7 7 7zM6 10v5m-2-2h5m7-2h.01m3 3h.01',
  close:'m6 6 12 12M6 18 18 6', search:'M10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12m5 11 6 6',
  expand:'M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6', list:'M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1',
};
const ids={'paint-brush':'brush','paint-lasso':'lasso','paint-navigate':'move','paint-apply':'trash','paint-cancel':'close',
 'crop-undo':'undo','crop-redo':'redo','crop-reset':'reset','crop-top':'cube','crop-front':'cube','crop-capture':'camera','crop-fit':'expand',
 'crop-original':'eye','crop-removed':'eye','crop-save':'save','select-area':'lasso','pointer-lock':'lock',
 'focus-toggle':'expand','inspector-toggle':'settings','pose-inspector-toggle':'settings','navigation-mode':'move'};
Object.assign(ids,{'walk-start':'move','free-view':'expand','mini-map-toggle':'map','gamepad-toggle':'gamepad',
  'overview':'expand','focus':'eye','camera-view':'camera','toggle-points':'eye','previous':'back','next':'arrow',
  'select-range':'lasso','select-ten':'list','select-current':'edit','select-all':'list'});
function iconName(node){
  if(node.dataset.icon)return node.dataset.icon;
  if(ids[node.id])return ids[node.id];
  const label=node.textContent.trim(),href=node.getAttribute('href')||'';
  if(node.hasAttribute('download'))return 'download';
  if(node.matches('.danger')||/ลบ/.test(label))return 'trash';
  if(/ย้อนกลับ/.test(label))return 'undo';if(/ทำซ้ำ/.test(label))return 'redo';
  if(/เปลี่ยนชื่อ|แก้ไข|ตัดขอบ/.test(label))return 'edit';
  if(/อัปโหลด|เพิ่ม Source/.test(label))return 'upload';
  if(/Check config|config|ปรับ|ตั้งค่า|Inspector/.test(label))return 'settings';
  if(/ยกเลิก|^ปิด$/.test(label))return 'close';
  if(/บันทึก/.test(label))return 'save';
  if(/กลับ|ก่อนหน้า/.test(label))return 'back';
  if(/เริ่ม|สร้าง|เตรียม|ปรับละเอียด/.test(label))return 'play';
  if(/ดาวน์โหลด/.test(label))return 'download';
  if(/คืนค่า|ลองใหม่|ลอง.*อีกครั้ง|รีเซ็ต|โหลด.*ล่าสุด/.test(label))return 'reset';
  if(/กล้อง|Camera|Pose/.test(label)||href.startsWith('/poses/'))return 'camera';
  if(/ภาพ|Gallery|Mask/.test(label)||href.startsWith('/quality/'))return 'image';
  if(/Model|Viewer|PLY|SPZ|GLB|3D/.test(label)||href.startsWith('/model/'))return 'cube';
  if(/คู่มือ|Log|JSON|ข้อมูล/.test(label))return 'info';
  if(node.tagName==='A')return 'arrow';
  return 'settings';
}
function decorate(node){
  if(!node.matches('button,a.button,a.download-link,.inspector-actions>a,.inspector-actions form>button')||
      node.querySelector('img,svg:not(.action-icon)')||!node.textContent.trim()||/^[←→×⋯]$/.test(node.textContent.trim()))return;
  const name=iconName(node);
  let icon=node.querySelector(':scope > .action-icon');
  if(icon?.dataset.name===name)return;
  icon?.remove();icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
  icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('class','action-icon');icon.setAttribute('aria-hidden','true');
  icon.setAttribute('focusable','false');icon.dataset.name=name;
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',paths[name]||paths.arrow);icon.append(path);
  node.prepend(icon);node.classList.add('has-action-icon');
}
function scan(root){if(root.nodeType!==1)return;decorate(root);root.querySelectorAll('button,a.button,a.download-link,.inspector-actions>a').forEach(decorate);}
setupCollections();setupTables();setupChapterSearch();
scan(document.body);
new MutationObserver(records=>{
  for(const record of records){
    if(record.type==='characterData')decorate(record.target.parentElement);
    else {if(record.target.nodeType===1)decorate(record.target);for(const node of record.addedNodes)scan(node);}
  }
}).observe(document.body,{subtree:true,childList:true,characterData:true});
