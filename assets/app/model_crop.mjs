import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {SparkRenderer, SplatMesh, dyno} from '@sparkjsdev/spark';
import {validBounds, newCropHistory, previewGate} from './crop_state.mjs';
import {PointPainter} from './point_painter.mjs';

const config = JSON.parse(document.querySelector('#crop-config').textContent);
const $ = id => document.querySelector('#'+id);
const canvas = $('crop-canvas'), host = canvas.parentElement;
const scene = new THREE.Scene(); scene.background = new THREE.Color('#0b1018');
const camera = new THREE.PerspectiveCamera(60, 1, .001, 10000);
const renderer = new THREE.WebGLRenderer({canvas, antialias:false});
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5)); renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NoToneMapping;
const controls = new OrbitControls(camera, canvas); controls.enableDamping = true;
scene.add(new SparkRenderer({renderer, preBlurAmount:.3, blurAmount:0, enableLod:false,
  sortRadial:false, maxStdDev:Math.sqrt(12), minAlpha:1/255, focalAdjustment:1}));
const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1,1,1)),
  new THREE.LineBasicMaterial({color:'#9be7ce',depthTest:false}));
box.renderOrder = 1000; box.visible = false; scene.add(box);
const gate = previewGate();
let info, model, state, initial, history, baseRotation, selection, maskTexture, modeUniform;
let maskPixels, maskWidth, pending, debounce, loading=true, saving=false, saved=false, requestId=null, editId=null;
let inFlight=null, currentMode=1, draftKey, renderFailed=false, painter, paintingBusy=false;
const faces = [['min',0,'ซ้าย'],['max',0,'ขวา'],['min',2,'หน้า'],['max',2,'หลัง'],['min',1,'พื้น'],['max',1,'เพดาน']];
const faceInputs = [];

function error(message, retry=false) {
  $('crop-error').hidden = !message; $('crop-error').textContent = message || '';
  $('crop-retry').hidden = !retry;
}
function resize() {
  const {width,height} = host.getBoundingClientRect();
  renderer.setSize(width, height, false); camera.aspect=width/Math.max(height,1); camera.updateProjectionMatrix();
  painter?.resize(width,height);
}
new ResizeObserver(resize).observe(host);
renderer.setAnimationLoop(() => {
  if (document.hidden || renderFailed) return;
  try { if(!painter || painter.tool==='navigate')controls.update(); renderer.render(scene,camera); }
  catch (exc) { renderFailed=true; error('แสดงผลไม่ได้: '+exc.message); $('crop-save').disabled=true; }
});

function rotation() {
  const yaw = new THREE.Matrix4().makeRotationY(THREE.MathUtils.degToRad(state.angle));
  return baseRotation.clone().multiply(yaw);
}
function crop() {
  const e=rotation().elements;
  return {origin:info.suggested.origin, rotation:[[e[0],e[4],e[8]],[e[1],e[5],e[9]],[e[2],e[6],e[10]]],
    min:state.min, max:state.max, strict:state.strict};
}
function payload() {
  return {model:config.model, source_signature:info.source_signature, source_sha256:info.source_sha256, crop:crop(),
    manual_selection_id:state.manual_selection_id || null,highlight_selection_id:state.highlight_selection_id || null};
}
function boxCenter() {
  return new THREE.Vector3().fromArray(state.min).add(new THREE.Vector3().fromArray(state.max)).multiplyScalar(.5)
    .applyMatrix4(rotation()).add(new THREE.Vector3().fromArray(info.suggested.origin));
}
function fit(view='fit') {
  if (!state || !validBounds(state)) return;
  if (view==='capture' && config.initial_view?.transform_matrix) {
    const transform = new THREE.Matrix4().set(...config.initial_view.transform_matrix.flat());
    camera.position.setFromMatrixPosition(transform); camera.quaternion.setFromRotationMatrix(transform);
    camera.up.set(0,1,0).applyQuaternion(camera.quaternion).normalize();
    camera.fov=config.initial_view.fov_y || 65;
    controls.target.copy(camera.position).add(new THREE.Vector3(0,0,-1).applyQuaternion(camera.quaternion));
  } else {
    const size=new THREE.Vector3().fromArray(state.max).sub(new THREE.Vector3().fromArray(state.min));
    const radius=Math.max(size.length()/2,.01), center=boxCenter(), frame=rotation();
    const offset=(view==='top' ? new THREE.Vector3(0,1,.0001) : view==='front' ? new THREE.Vector3(0,0,1) : new THREE.Vector3(1,.7,1)).normalize();
    camera.position.copy(center).add(offset.applyMatrix4(frame).multiplyScalar(radius*2.3));
    camera.up.copy((view==='top' ? new THREE.Vector3(0,0,-1) : new THREE.Vector3(0,1,0)).transformDirection(frame));
    controls.target.copy(center); camera.fov=60;
  }
  camera.updateProjectionMatrix(); controls.update();
}
function setMode(value) {
  if(value!==1)painter?.setTool('navigate');
  currentMode=value; if(modeUniform) modeUniform.value=value;
  $('crop-original').setAttribute('aria-pressed',String(value===0));
  $('crop-removed').setAttribute('aria-pressed',String(value===2));
  model?.updateGenerator();
}
function syncControls() {
  for(const {key,axis,range,number} of faceInputs) {
    const span=initial.max[axis]-initial.min[axis], value=state[key][axis];
    if(!Number.isFinite(value)) continue;
    range.min=Math.min(initial.min[axis]-span*.6,value-span*.05);
    range.max=Math.max(initial.max[axis]+span*.6,value+span*.05);
    range.step=Math.max(span/1000,.0001); number.step=range.step;
    range.value=value;
    if(document.activeElement!==number) number.value=Number(value.toFixed(4));
  }
  $('crop-strict').checked=state.strict; $('crop-angle').value=state.angle;
  $('crop-angle-value').textContent=state.angle+'°';
  $('crop-undo').disabled=saving || paintingBusy || !history.canUndo(); $('crop-redo').disabled=saving || paintingBusy || !history.canRedo();
}
function syncPaint() {
  const busy=saving || paintingBusy || loading;
  for(const id of ['paint-navigate','paint-brush','paint-lasso'])$(id).disabled=busy;
  $('paint-settings').disabled=busy;
  $('paint-apply').disabled=busy || !gate.ready() || !selection?.highlight_count;
  $('paint-cancel').disabled=busy || !state?.highlight_selection_id;
  painter?.setBusy(busy);
}
function updateBox() {
  if(!validBounds(state)) return;
  box.position.copy(boxCenter()); box.quaternion.setFromRotationMatrix(rotation());
  box.scale.fromArray(state.max).sub(new THREE.Vector3().fromArray(state.min));
  box.visible=$('crop-box').checked;
}
function editChanged(commit=false) {
  if(saving || loading) return;
  saved=false; selection=null; requestId=null; gate.change();
  document.body.dataset.preview='pending';
  try { localStorage.removeItem(draftKey+':saving'); } catch {}
  $('crop-save').disabled=true; $('crop-result').hidden=true; clearTimeout(debounce); inFlight?.abort();
  if(commit && validBounds(state)) history.push(state);
  syncControls(); updateBox();
  syncPaint();
  if(!validBounds(state)) { error('ขอบซ้าย/พื้น/หน้าต้องน้อยกว่าขอบขวา/เพดาน/หลัง'); return; }
  error(''); $('crop-preview-status').textContent='กำลังอัปเดตพรีวิว…';
  try { localStorage.setItem(draftKey,JSON.stringify(state)); } catch {}
  debounce=setTimeout(updatePreview,300);
}
async function updatePreview() {
  clearTimeout(debounce); if(!validBounds(state) || saving) return;
  const revision=gate.current(); inFlight?.abort(); const controller=new AbortController(); inFlight=controller;
  try {
    const response=await fetch('/api/model-crop/preview',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload()),signal:AbortSignal.any([controller.signal,AbortSignal.timeout(45000)])});
    if(!response.ok) { const result=await response.json(); throw new Error(result.error || 'คำนวณพรีวิวไม่ได้'); }
    const bytes=await response.arrayBuffer(), view=new DataView(bytes), length=view.getUint32(0,true);
    const result=JSON.parse(new TextDecoder().decode(new Uint8Array(bytes,4,length)));
    const mask=new Uint8Array(bytes,4+length,info.count);
    if(result.total!==info.count || bytes.byteLength!==4+length+info.count+(result.highlight_bytes || 0)) throw new Error('จำนวนจุดไม่ตรงกับ Model กรุณาเปิดใหม่');
    if(!gate.accept(revision)) return;
    maskPixels.fill(0); maskPixels.set(mask);
    if(result.highlight_bytes) {
      const highlighted=new Uint8Array(bytes,4+length+info.count,result.highlight_bytes);
      for(let i=0;i<info.count;++i)if(highlighted[i]&&mask[i])maskPixels[i]=2;
    }
    maskTexture.needsUpdate=true; model.updateGenerator();
    selection=result;
    $('crop-count').textContent=`เก็บ ${result.kept.toLocaleString()} / ${result.total.toLocaleString()} จุด · ตัดออก ${(result.total-result.kept).toLocaleString()} จุด (${(100*(result.total-result.kept)/result.total).toFixed(1)}%)`;
    $('crop-preview-status').textContent=result.kept ? 'พรีวิวพร้อม · ตรวจผนังและพื้นก่อนบันทึก' : 'กรอบนี้ไม่เหลือจุด ให้ขยายกรอบหรือปิดเก็บทั้งก้อน';
    $('crop-save').disabled=!result.kept || renderFailed || !!state.highlight_selection_id || paintingBusy;
    $('paint-count').textContent=result.highlight_count ? `เลือก ${result.highlight_count.toLocaleString()} จุด · สีแดงยังไม่ถูกลบ` : '';
    syncPaint();
    document.body.dataset.preview='ready'; document.body.dataset.kept=String(result.kept);
    error('');
  } catch(exc) {
    if(controller.signal.aborted || revision!==gate.current()) return;
    $('crop-preview-status').textContent='พรีวิวยังไม่พร้อม'; $('crop-save').disabled=true;
    error(exc.message,true);
  }
}
function makeMaskModifier() {
  const texture=new dyno.DynoUniform({key:'selection',type:'usampler2D',value:maskTexture});
  modeUniform=new dyno.DynoInt({value:1});
  const width=new dyno.DynoInt({value:maskWidth});
  return dyno.dynoBlock({gsplat:dyno.Gsplat},{gsplat:dyno.Gsplat},({gsplat}) => {
    const operation=new dyno.Dyno({inTypes:{gsplat:dyno.Gsplat,mask:'usampler2D',mode:'int',width:'int'},outTypes:{gsplat:dyno.Gsplat},
      inputs:{gsplat,mask:texture,mode:modeUniform,width},globals:()=>[dyno.defineGsplat],
      statements:({inputs:i,outputs:o})=>[
        `${o.gsplat} = ${i.gsplat};`,
        `bool keep = texelFetch(${i.mask}, ivec2(${i.gsplat}.index % ${i.width}, ${i.gsplat}.index / ${i.width}), 0).r != 0u;`,
        `if ((${i.mode} == 1 && !keep) || (${i.mode} == 2 && keep)) { ${o.gsplat}.rgba.a = 0.0; ${o.gsplat}.flags = 0u; }`,
        `if (${i.mode} == 2 && !keep) { ${o.gsplat}.rgba.rgb = mix(${o.gsplat}.rgba.rgb, vec3(1.0,0.15,0.08),0.7); }`,
        `if (${i.mode} == 1 && texelFetch(${i.mask}, ivec2(${i.gsplat}.index % ${i.width}, ${i.gsplat}.index / ${i.width}), 0).r == 2u) { ${o.gsplat}.rgba.rgb = mix(${o.gsplat}.rgba.rgb, vec3(1.0,0.08,0.02),0.85); }`,
      ]});
    return operation.outputs;
  });
}
function buildFields() {
  for(const [key,axis,label] of faces) {
    const div=document.createElement('div'); div.className='crop-face';
    const caption=document.createElement('label'), number=document.createElement('input'), range=document.createElement('input');
    const id=`crop-${key}-${axis}`; number.id=id; number.type='number'; caption.htmlFor=id; caption.textContent=label;
    range.type='range'; range.setAttribute('aria-label',label); range.dataset.face=key+'-'+axis;
    number.dataset.face=key+'-'+axis;
    const change=(input,commit) => { state[key][axis]=input.value==='' ? NaN : Number(input.value); editChanged(commit); };
    range.addEventListener('input',()=>change(range,false)); range.addEventListener('change',()=>change(range,true));
    number.addEventListener('input',()=>change(number,false)); number.addEventListener('change',()=>change(number,true));
    div.append(caption,number,range); $('crop-faces').append(div); faceInputs.push({key,axis,range,number});
  }
}
function freeze(value) {
  saving=value; $('crop-fields').disabled=value; $('crop-save').disabled=value || !selection?.kept || !gate.ready() || renderFailed || !!state?.highlight_selection_id;
  for(const id of ['crop-reset','crop-undo','crop-redo']) $(id).disabled=value;
  if(!value) syncControls();
  syncPaint();
}
async function paintStroke(screen) {
  if(saving || paintingBusy || !gate.ready()) {error('กรุณารอพรีวิวล่าสุดก่อนเลือกจุด');return;}
  const revision=gate.current();paintingBusy=true;syncPaint();syncControls();$('crop-fields').disabled=true;
  $('crop-save').disabled=true;$('paint-count').textContent='กำลังคำนวณจุดที่เลือก…';
  try {
    const response=await fetch('/api/model-crop/select',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...payload(),base_selection_id:state.highlight_selection_id || state.manual_selection_id || null,screen}),signal:AbortSignal.timeout(60000)});
    const result=await response.json();if(!response.ok)throw new Error(result.error || 'เลือกจุดไม่ได้');
    if(revision!==gate.current())return;
    if(result.added) {state.highlight_selection_id=result.selection_id;editChanged(true);}
    else $('paint-count').textContent='ไม่พบจุดเพิ่มในบริเวณนี้ ลองซูม หมุนมุม หรือปรับเลือกชั้นหน้า';
  } catch(exc) {error(exc.message);$('paint-count').textContent='เลือกจุดไม่สำเร็จ ลากอีกครั้งเพื่อลองใหม่';}
  finally {
    paintingBusy=false;$('crop-fields').disabled=saving;syncPaint();syncControls();
    $('crop-save').disabled=saving || !gate.ready() || !selection?.kept || !!state.highlight_selection_id || renderFailed;
  }
}
async function watchCompression(identifier) {
  if(!identifier) { $('crop-compress-status').textContent='เปิด Model แล้วกดบีบอัดเพื่อส่งออก GLB ต่อได้'; return; }
  $('crop-compress').href='/compression/'+identifier;
  $('crop-compress-status').textContent='กำลังเตรียม SPZ ของรุ่นหลังตัด เมื่อเสร็จจะส่งออก GLB ต่อได้';
  try {
    const response=await fetch('/api/compression/'+identifier,{signal:AbortSignal.timeout(15000),cache:'no-store'});
    if(!response.ok) throw new Error(); const result=await response.json();
    if(result.state==='ready') {
      $('crop-compress').href='/exports/glb/'+identifier;
      $('crop-compress-status').textContent='SPZ พร้อม · กด SPZ / ส่งออก GLB เพื่อดาวน์โหลดต่อ'; return;
    }
    if(['error','deleted'].includes(result.state)) { $('crop-compress-status').textContent='SPZ ยังไม่พร้อม เปิด Model เพื่อลองบีบอัดใหม่ได้'; return; }
  } catch { $('crop-compress-status').textContent='กำลังเชื่อมต่อสถานะ SPZ ใหม่…'; }
  if(saved) setTimeout(()=>watchCompression(identifier),2500);
}
async function watchSave() {
  try {
    const response=await fetch('/api/model-crop/status/'+editId,{cache:'no-store',signal:AbortSignal.timeout(15000)});
    if(!response.ok) throw new Error('อ่านสถานะบันทึกไม่ได้');
    const result=await response.json();
    if(result.state==='error') { freeze(false); error(result.error); $('crop-save-progress').hidden=true; return; }
    $('crop-save-progress').value=result.progress || 0;
    $('crop-preview-status').textContent=result.state==='queued' ? 'รอคิวบันทึก… ปิดหน้านี้แล้วกลับมาได้' : `กำลังบันทึก ${result.progress || 0}%`;
    if(result.state==='ready') {
      saved=true; freeze(false); $('crop-save').disabled=true; $('crop-save-progress').hidden=true; $('crop-result').hidden=false;
      $('crop-result-name').textContent=result.model;
      $('crop-open').href='/model/'+encodeURIComponent(result.model); $('crop-ply').href='/models/'+encodeURIComponent(result.model);
      $('crop-compress').href=$('crop-open').href;
      $('crop-preview-status').textContent='บันทึกเป็นรุ่นใหม่แล้ว ต้นฉบับยังอยู่';
      document.body.dataset.saved=result.model; watchCompression(result.compression_id);
      try { localStorage.removeItem(draftKey+':saving'); } catch {} return;
    }
  } catch(exc) { $('crop-preview-status').textContent='กำลังเชื่อมต่อสถานะบันทึกใหม่…'; }
  setTimeout(watchSave,1800);
}
async function save() {
  if(saving || paintingBusy || state.highlight_selection_id || !gate.ready() || !selection?.kept || !validBounds(state)) return;
  requestId ||= crypto.randomUUID().replaceAll('-','');
  freeze(true); error(''); $('crop-save-progress').hidden=false; $('crop-save-progress').removeAttribute('value');
  $('crop-preview-status').textContent='กำลังเริ่มบันทึกรุ่นใหม่…';
  // Persist the id before sending, so a connection loss cannot duplicate the output.
  try { localStorage.setItem(draftKey+':saving',requestId); } catch {}
  try {
    const response=await fetch('/api/model-crop/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...payload(),selection_sha256:selection.selection_sha256,request_id:requestId}),signal:AbortSignal.timeout(45000)});
    const result=await response.json(); if(!response.ok) throw new Error(result.error || 'บันทึกไม่ได้');
    editId=result.id; watchSave();
  } catch(exc) { freeze(false); $('crop-save-progress').hidden=true; error(exc.message+' · กดบันทึกอีกครั้งเพื่อตรวจงานเดิมได้'); }
}

$('crop-top').onclick=()=>fit('top'); $('crop-front').onclick=()=>fit('front');
$('crop-fit').onclick=()=>fit(); $('crop-capture').onclick=()=>fit('capture');
$('crop-original').onclick=()=>setMode(currentMode===0 ? 1 : 0);
$('crop-removed').onclick=()=>setMode(currentMode===2 ? 1 : 2);
$('crop-box').onchange=()=>{box.visible=$('crop-box').checked && !loading;};
$('crop-angle').addEventListener('input',()=>{state.angle=Number($('crop-angle').value);editChanged(false);});
$('crop-angle').addEventListener('change',()=>editChanged(true));
$('crop-strict').onchange=()=>{state.strict=$('crop-strict').checked;editChanged(true);};
$('crop-reset').onclick=()=>{state=structuredClone(initial);editChanged(true);fit();};
$('crop-undo').onclick=()=>{state=history.undo();editChanged(false);};
$('crop-redo').onclick=()=>{state=history.redo();editChanged(false);};
$('crop-retry').onclick=()=>{error('');updatePreview();}; $('crop-save').onclick=save;
for(const tool of ['navigate','brush','lasso'])$('paint-'+tool).onclick=()=>painter?.setTool(tool);
$('paint-radius').oninput=()=>{if(painter)painter.radius=Number($('paint-radius').value);$('paint-radius-value').textContent=$('paint-radius').value+' px';};
$('paint-front').onchange=()=>{if(painter)painter.frontOnly=$('paint-front').checked;};
$('paint-apply').onclick=()=>{if(!paintingBusy && state.highlight_selection_id && gate.ready()){state.manual_selection_id=state.highlight_selection_id;state.highlight_selection_id=null;editChanged(true);}};
$('paint-cancel').onclick=()=>{if(!paintingBusy && state.highlight_selection_id){state.highlight_selection_id=null;editChanged(true);}};
document.addEventListener('keydown',event=>{
  if(loading || saving || paintingBusy || event.target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target?.tagName))return;
  if(event.key==='Escape'){painter?.cancel();if(state.highlight_selection_id)$('paint-cancel').click();}
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();(event.shiftKey?$('crop-redo'):$('crop-undo')).click();}
  else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='y'){event.preventDefault();$('crop-redo').click();}
  else if(event.key==='Delete'){event.preventDefault();$('paint-apply').click();}
});

async function start() {
  try {
    const metadata=fetch('/api/model-crop/info?model='+encodeURIComponent(config.model),{signal:AbortSignal.timeout(60000)}).then(async response=>{
      const value=await response.json(); if(!response.ok) throw new Error(value.error); return value;
    });
    model=new SplatMesh({url:config.url,fileType:config.file_type,extSplats:true,lod:false,enableLod:false,
      onProgress:event=>{$('crop-load').textContent=`กำลังโหลด Model ${Math.min(100,Math.round(100*(event.loaded || 0)/(event.total || config.bytes)))}%`;}});
    scene.add(model);
    [info]=await Promise.all([metadata,model.initialized]);
    if(model.numSplats!==info.count || JSON.stringify(info.source_signature)!==JSON.stringify(config.source_signature))
      throw new Error('Model เปลี่ยนระหว่างโหลด กรุณาเปิด Editor ใหม่');
    draftKey='crop:'+config.model+':'+info.source_sha256;
    baseRotation=new THREE.Matrix4().set(
      ...info.suggested.rotation[0],0,...info.suggested.rotation[1],0,...info.suggested.rotation[2],0,0,0,0,1);
    initial={min:info.suggested.min,max:info.suggested.max,strict:true,angle:0,manual_selection_id:null,highlight_selection_id:null}; state=structuredClone(initial);
    try {
      const draft=JSON.parse(localStorage.getItem(draftKey));
      if(draft && validBounds(draft) && Number.isFinite(draft.angle) && typeof draft.strict==='boolean' &&
          [draft.manual_selection_id,draft.highlight_selection_id].every(id=>!id||/^[a-f0-9]{32}$/.test(id))) state={...initial,...draft};
    } catch {}
    history=newCropHistory(state); buildFields();
    maskWidth=Math.min(2048,renderer.capabilities.maxTextureSize);
    const height=Math.ceil(info.count/maskWidth);
    if(height>renderer.capabilities.maxTextureSize) throw new Error('Model ใหญ่เกินความจุพรีวิวของ GPU นี้');
    maskPixels=new Uint8Array(maskWidth*height).fill(1);
    maskTexture=new THREE.DataTexture(maskPixels,maskWidth,height,THREE.RedIntegerFormat,THREE.UnsignedByteType);
    maskTexture.internalFormat='R8UI'; maskTexture.minFilter=maskTexture.magFilter=THREE.NearestFilter;
    maskTexture.unpackAlignment=1; maskTexture.needsUpdate=true;
    model.objectModifier=makeMaskModifier(); model.updateGenerator();
    painter=new PointPainter({overlay:$('paint-overlay'),camera,controls,onStroke:paintStroke,onMode:tool=>{
      for(const name of ['navigate','brush','lasso'])$('paint-'+name).setAttribute('aria-pressed',String(name===tool));
      if(tool!=='navigate')setMode(1);
    }});
    const viewport=host.getBoundingClientRect();painter.resize(viewport.width,viewport.height);
    loading=false; $('crop-fields').disabled=false;
    for(const id of ['crop-top','crop-front','crop-fit','crop-reset','crop-original','crop-removed']) $(id).disabled=false;
    $('crop-capture').disabled=!config.initial_view?.transform_matrix;
    $('crop-load').hidden=true; syncControls(); updateBox(); fit();
    document.body.dataset.loaded='true'; gate.change(); await updatePreview();
    syncPaint();
    try {
      const pendingId=localStorage.getItem(draftKey+':saving');
      if(pendingId && /^[a-f0-9]{32}$/.test(pendingId)) {
        const response=await fetch('/api/model-crop/status/'+pendingId);
        if(response.ok) { editId=pendingId; freeze(true); $('crop-save-progress').hidden=false; watchSave(); }
      }
    } catch {}
  } catch(exc) { $('crop-load').textContent='เปิด Editor ไม่สำเร็จ'; error(exc.message); document.body.dataset.error=exc.message; }
}
start();
