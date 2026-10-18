import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const config = JSON.parse(document.querySelector('#config').textContent);
const $ = selector => document.querySelector(selector);
const stage = $('.stage');
const loading = $('#loading');
let data, renderer, scene, camera, controls, pointCloud, selectedPlane;
let selectedCamera = 0, selectedView = 0, textureRequest = 0, radius = 1;
let originalMode = false, pointsVisible = true;
let selectionMode = false, trainReady = false, jobBusy = false;
const trainingCameras = new Set();
let lassoPoints = null;
const worldUp = new THREE.Vector3(0, 1, 0);
const center = new THREE.Vector3();
const markers = [], contextual = new THREE.Group(), selectedGroup = new THREE.Group();
const raycaster = new THREE.Raycaster();
const textureLoader = new THREE.TextureLoader();

function option(select, value, label) {
  const node = document.createElement('option'); node.value = value; node.textContent = label; select.append(node);
}
function line(points, color, group = contextual) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const object = new THREE.Line(geometry, new THREE.LineBasicMaterial({color})); group.add(object); return object;
}
function pose(view) { return new THREE.Matrix4().set(...view.transform_matrix.flat()); }
function resize() {
  if (!renderer) return;
  camera.aspect = Math.max(stage.clientWidth, 1) / Math.max(stage.clientHeight, 1);
  renderer.setSize(stage.clientWidth, stage.clientHeight, false); camera.updateProjectionMatrix();
  if (originalMode) viewFromCamera();
}
function disposeSelection() {
  selectedGroup.traverse(object => {
    object.geometry?.dispose();
    if (object.material) { object.material.map?.dispose(); object.material.dispose(); }
  });
  selectedGroup.clear(); selectedPlane = null;
}
function restoreMap() {
  originalMode = false; contextual.visible = true; pointCloud.visible = pointsVisible;
  markers.forEach(marker => {marker.visible = true;});
  camera.fov = 55; camera.updateProjectionMatrix();
  $('#hint').textContent = 'ลาก: หมุน · ลากขวา: เลื่อน · ล้อเมาส์: ซูม · กดจุดกล้องเพื่อเลือกภาพ';
}
function overview() {
  restoreMap(); camera.up.copy(worldUp);
  camera.position.copy(center).add(new THREE.Vector3(radius * 1.5, radius * .8, radius * 1.5));
  controls.target.copy(center); controls.update();
}
function focusSelection() {
  restoreMap(); const p = new THREE.Vector3(...data.cameras[selectedCamera].position);
  const view = data.views[selectedView], matrix = pose(view);
  const forward = new THREE.Vector3(0, 0, -1).transformDirection(matrix);
  camera.position.copy(p).addScaledVector(forward, -radius * .25).addScaledVector(worldUp, radius * .12);
  camera.up.copy(worldUp); controls.target.copy(p).addScaledVector(forward, radius * .08); controls.update();
}
function viewFromCamera() {
  const view = data.views[selectedView], matrix = pose(view);
  matrix.decompose(camera.position, camera.quaternion, camera.scale);
  camera.up.set(0, 1, 0).transformDirection(matrix);
  // Keep the full captured image visible even when the canvas has a different aspect ratio.
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.max(view.h / (2 * view.fl_y), view.w / (2 * view.fl_x * camera.aspect))));
  camera.updateProjectionMatrix();
  controls.target.copy(camera.position).add(new THREE.Vector3(0, 0, -1).transformDirection(matrix));
  controls.update(); originalMode = true; contextual.visible = false; pointCloud.visible = false;
  markers.forEach(marker => {marker.visible = false;});
  $('#hint').textContent = 'ภาพต้นฉบับที่ pose นี้ · กดภาพรวมเพื่อกลับไปดูเส้นทางกล้อง';
}
function selectView(index) {
  selectedView = Number(index); const view = data.views[selectedView];
  $('#direction').value = String(selectedView);
  $('#direction-slider').value = data.cameras[selectedCamera].views.indexOf(selectedView);
  $('#direction-label').textContent = `ทิศ ${view.yaw_deg ?? 0}° · pitch ${view.pitch_deg ?? 0}° · มุม ${Number($('#direction-slider').value)+1}/${data.cameras[selectedCamera].views.length}`;
  $('#image').src = view.image_url; $('#image-link').href = view.image_url;
  $('#selection').textContent = `เฟรม ${view.frame_index} · ${view.name}\nตำแหน่ง (${data.cameras[selectedCamera].position.map(v => v.toFixed(3)).join(', ')}) · หน่วยสัมพัทธ์`;
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('selected', Number(button.dataset.view) === selectedView));
  disposeSelection();
  const matrix = pose(view), origin = new THREE.Vector3().setFromMatrixPosition(matrix);
  const depth = radius * .09;
  const corners = [[0,0],[view.w,0],[view.w,view.h],[0,view.h]].map(([x,y]) =>
    new THREE.Vector3((x - view.cx) / view.fl_x * depth, -(y - view.cy) / view.fl_y * depth, -depth).applyMatrix4(matrix));
  line([...corners, corners[0]], 0x8bbbff, selectedGroup);
  corners.forEach(corner => line([origin, corner], 0x8bbbff, selectedGroup));
  // Construct image corners using the same intrinsics as the training camera.
  const geometry = new THREE.BufferGeometry();
  const positions = [corners[0],corners[3],corners[1],corners[3],corners[2],corners[1]].flatMap(p => [p.x,p.y,p.z]);
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute([0,1,0,0,1,1,0,0,1,0,1,1], 2));
  const material = new THREE.MeshBasicMaterial({side:THREE.DoubleSide, toneMapped:false, color:0xffffff});
  selectedPlane = new THREE.Mesh(geometry, material); selectedPlane.visible = false; selectedGroup.add(selectedPlane);
  const request = ++textureRequest;
  textureLoader.load(view.image_url, texture => {
    if (request !== textureRequest) { texture.dispose(); return; }
    texture.colorSpace = THREE.SRGBColorSpace; material.map = texture; material.needsUpdate = true; selectedPlane.visible = true;
  }, undefined, error => { if (request === textureRequest) console.warn('Could not load camera image', error); });
  if (originalMode) viewFromCamera();
}
function selectCamera(index) {
  const previousSlot = data.cameras[selectedCamera]?.views.indexOf(selectedView) || 0;
  selectedCamera = Math.max(0, Math.min(data.cameras.length - 1, Number(index)));
  $('#position').value = String(selectedCamera); $('#position-slider').value = selectedCamera;
  updateMarkers();
  const group = data.cameras[selectedCamera]; $('#direction').replaceChildren(); $('#thumbnails').replaceChildren();
  $('#direction-slider').max = group.views.length - 1;
  for (const index of group.views) {
    const view = data.views[index];
    const label = view.yaw_deg == null ? view.name : `ทิศ ${view.yaw_deg}° · pitch ${view.pitch_deg ?? 0}°`;
    option($('#direction'), index, label);
    const button = document.createElement('button'); button.dataset.view = index;
    const img = document.createElement('img'); img.src = view.image_url; img.loading = 'lazy'; img.alt = label;
    button.append(img, document.createTextNode(label)); button.addEventListener('click', () => selectView(index));
    $('#thumbnails').append(button);
  }
  $('#previous').disabled = selectedCamera === 0; $('#next').disabled = selectedCamera === data.cameras.length - 1;
  selectView(group.views[Math.max(0, Math.min(previousSlot, group.views.length - 1))]);
  if (!originalMode) $('#hint').textContent = 'จุดสีส้ม: กล้องที่จะ train · สีเทา: ตัดออก · กรอบสีฟ้า: ภาพที่เลือก · เส้นแดง: ช่วงที่ควรตรวจ';
}

function updateMarkers() {
  markers.forEach((marker, i) => marker.material.color.setHex(i===selectedCamera ? 0x75aaff : trainingCameras.has(i) ? 0xffbc65 : 0x425064));
}
function updateSubset() {
  updateMarkers();
  const selected=[...trainingCameras].sort((a,b)=>a-b);
  const views=selected.reduce((sum,index)=>sum+data.cameras[index].views.length,0);
  $('#selected-frames').value = selected.length===data.cameras.length ? '' : JSON.stringify(selected.map(index=>data.cameras[index].frame_index));
  $('#selection-signature').value = data.input_signature;
  $('#subset-summary').textContent=`เลือก ${selected.length}/${data.cameras.length} ตำแหน่ง · ${views} ภาพไป train`;
  const warnings=[];
  const viewsPerPosition = selected.length ? Math.max(1, Math.min(...selected.map(index=>data.cameras[index].views.length))) : 1;
  const minimum = 3 + Math.ceil(3 / viewsPerPosition);
  if (selected.length<minimum) warnings.push(`เลือกอย่างน้อย ${minimum} ตำแหน่ง เพื่อแยกภาพทดสอบ · แนะนำเริ่ม 10 จุดต่อเนื่อง`);
  if (selected.some((index,i)=>i && index-selected[i-1]>1)) warnings.push('ชุดนี้มีช่วงที่ข้ามตำแหน่ง ควรตรวจว่าภาพยังซ้อนกัน');
  const jumps=data.segments.filter(segment=>segment.suspicious && trainingCameras.has(segment.from) && trainingCameras.has(segment.to)).length;
  if (jumps) warnings.push(`มี ${jumps} ช่วงที่ pose กระโดด ควรตัดช่วงที่ตำแหน่งผิดออก`);
  $('#subset-warning').hidden=!warnings.length; $('#subset-warning').textContent=warnings.join('\n');
  $('#train').disabled=!trainReady || jobBusy || selected.length<minimum;
  $('#train').textContent=selected.length===data.cameras.length ? 'Train ทุกตำแหน่งเป็น Model ใหม่' : `Train ${selected.length} ตำแหน่งเป็น Model ใหม่`;
}
function setTrainingRange(start,end) {
  start=Math.max(0,Math.min(data.cameras.length-1,Number(start)));
  end=Math.max(start,Math.min(data.cameras.length-1,Number(end)));
  trainingCameras.clear(); for(let i=start;i<=end;i++) trainingCameras.add(i);
  for(const selector of ['#range-start','#range-start-slider']) $(selector).value=start+1;
  for(const selector of ['#range-end','#range-end-slider']) $(selector).value=end+1;
  updateSubset();
}
function pointInPolygon(point,polygon) {
  let inside=false;
  for(let i=0,j=polygon.length-1;i<polygon.length;j=i++) {
    const a=polygon[i],b=polygon[j];
    if((a[1]>point[1])!==(b[1]>point[1]) && point[0]<(b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0]) inside=!inside;
  }
  return inside;
}
function toggleSelectionMode() {
  selectionMode=!selectionMode;
  if(selectionMode && originalMode) overview();
  controls.enabled=!selectionMode;
  $('#select-area').textContent=selectionMode ? 'จบการวงเลือก / กลับไปหมุน' : 'ลากวงเลือกตำแหน่ง';
  $('#hint').textContent=selectionMode ? 'ลากวงรอบกลุ่มจุดกล้อง · จุดสีส้มจะถูกเลือกไป train · กดจบการวงเลือกเพื่อหมุนฉาก' : 'ลาก: หมุน · ลากขวา: เลื่อน · ล้อเมาส์: ซูม';
}
function setupScene() {
  renderer = new THREE.WebGLRenderer({canvas:$('#canvas'), antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  scene = new THREE.Scene(); scene.background = new THREE.Color(0x070d17);
  const allCenters = data.cameras.map(c => new THREE.Vector3(...c.position));
  const bounds = new THREE.Box3().setFromPoints(allCenters); bounds.getCenter(center);
  radius = Math.max(bounds.getSize(new THREE.Vector3()).length() * .5, .1);
  const distances = [];
  for (let i=0; i<data.points.positions.length; i+=3) distances.push(Math.hypot(data.points.positions[i]-center.x, data.points.positions[i+1]-center.y, data.points.positions[i+2]-center.z));
  distances.sort((a,b)=>a-b);
  if (distances.length) radius = Math.max(radius, distances[Math.floor((distances.length-1)*.9)]);
  const horizontal = data.views.find(v => Math.abs(v.pitch_deg ?? 0) < 1) || data.views[0];
  worldUp.set(0,1,0).transformDirection(pose(horizontal));
  camera = new THREE.PerspectiveCamera(55, 1, radius / 2000, radius * 1000); camera.up.copy(worldUp);
  controls = new OrbitControls(camera, $('#canvas')); controls.enableDamping = true; controls.maxDistance = radius * 25;
  scene.add(contextual, selectedGroup);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.points.positions, 3));
  if (data.points.colors) {
    const rgb = new Float32Array(data.points.colors);
    for (let i = 0; i < rgb.length; i += 3) {
      const linear = new THREE.Color().setRGB(rgb[i],rgb[i+1],rgb[i+2],THREE.SRGBColorSpace);
      rgb[i]=linear.r; rgb[i+1]=linear.g; rgb[i+2]=linear.b;
    }
    geometry.setAttribute('color', new THREE.BufferAttribute(rgb, 3));
  }
  pointCloud = new THREE.Points(geometry, new THREE.PointsMaterial({size:radius*.003, vertexColors:!!data.points.colors, color:data.points.colors ? 0xffffff : 0xa7c4ee}));
  scene.add(pointCloud);
  const markerGeometry = new THREE.SphereGeometry(radius * .008, 10, 8);
  for (let i=0; i<data.cameras.length; i++) {
    const marker = new THREE.Mesh(markerGeometry, new THREE.MeshBasicMaterial({color:0x52d5b5}));
    marker.position.copy(allCenters[i]); marker.userData.cameraIndex = i; scene.add(marker); markers.push(marker);
    const matrix = pose(data.views[data.cameras[i].views[0]]);
    line([marker.position, marker.position.clone().addScaledVector(new THREE.Vector3(0,0,-1).transformDirection(matrix), radius*.04)], 0x41716e);
  }
  for (const segment of data.segments) line([allCenters[segment.from],allCenters[segment.to]], segment.suspicious ? 0xf47b7b : 0x52b5a2);
  const grid = new THREE.GridHelper(radius*3,20,0x3a4c66,0x1b2c44);
  grid.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),worldUp);
  grid.position.copy(center).addScaledVector(worldUp,-radius*.08); contextual.add(grid);
  let down;
  $('#canvas').addEventListener('pointerdown', event => {down=[event.clientX,event.clientY];});
  $('#canvas').addEventListener('pointerdown', event => {
    if(!selectionMode || event.button!==0) return;
    const rect=$('#canvas').getBoundingClientRect();
    lassoPoints=[[event.clientX-rect.left,event.clientY-rect.top]];
    $('#canvas').setPointerCapture(event.pointerId); $('#lasso-path').setAttribute('d','');
  });
  $('#canvas').addEventListener('pointermove',event=>{
    if(!lassoPoints) return;
    const rect=$('#canvas').getBoundingClientRect(), point=[event.clientX-rect.left,event.clientY-rect.top],last=lassoPoints.at(-1);
    if(Math.hypot(point[0]-last[0],point[1]-last[1])<4) return;
    lassoPoints.push(point);
    $('#lasso-path').setAttribute('d',lassoPoints.map((p,i)=>(i?'L':'M')+p.join(',')).join(' ')+' Z');
  });
  $('#canvas').addEventListener('pointerup',event=>{
    if(!lassoPoints) return;
    const polygon=lassoPoints; lassoPoints=null; $('#lasso-path').setAttribute('d','');
    if(polygon.length<3) return;
    camera.updateMatrixWorld();
    const rect=$('#canvas').getBoundingClientRect(); trainingCameras.clear();
    markers.forEach((marker,index)=>{
      const p=marker.position.clone().project(camera);
      if(p.z>=-1 && p.z<=1 && pointInPolygon([(p.x+1)*rect.width/2,(1-p.y)*rect.height/2],polygon)) trainingCameras.add(index);
    });
    updateSubset();
  });
  $('#canvas').addEventListener('pointercancel',()=>{lassoPoints=null;$('#lasso-path').setAttribute('d','');});
  $('#canvas').addEventListener('pointerup', event => {
    if (selectionMode || !down || Math.hypot(event.clientX-down[0],event.clientY-down[1])>5 || event.button!==0 || originalMode) return;
    const rect=$('#canvas').getBoundingClientRect();
    raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),camera);
    const hit=raycaster.intersectObjects(markers,false)[0]; if(hit) selectCamera(hit.object.userData.cameraIndex);
  });
  resize(); overview(); selectCamera(0);
  renderer.setAnimationLoop(() => {controls.update(); renderer.render(scene,camera);});
}
function renderSummary() {
  const summary = data.summary;
  for (const [value,label] of [[`${summary.registered_positions}/${summary.input_positions}`,'ตำแหน่งกล้อง'],[summary.registered_views,'ภาพที่มี pose'],[summary.sparse_points,'จุดฉากจาก COLMAP'],[summary.suspicious_segments,'ช่วงที่ควรตรวจ']]) {
    const stat=document.createElement('div'); stat.className='stat'; const b=document.createElement('b'); b.textContent=value;
    const span=document.createElement('span'); span.textContent=label; stat.append(b,span); $('#stats').append(stat);
  }
  for(const message of data.warnings) {const warning=document.createElement('div');warning.className='warning';warning.textContent=message;$('#warnings').append(warning);}
  data.cameras.forEach((c,index) => option($('#position'),index,`จุด ${index+1} · เฟรม ${c.frame_index} · ${c.views.length} มุม`));
  $('#position-slider').max=data.cameras.length-1;
  data.cameras.forEach((camera,index)=>trainingCameras.add(index));
  for(const selector of ['#range-start','#range-end','#range-start-slider','#range-end-slider']) $(selector).max=data.cameras.length;
  for(const selector of ['#range-end','#range-end-slider']) $(selector).value=Math.min(10,data.cameras.length);
  if(data.unregistered_frames.length) {
    $('#missing-section').hidden=false;
    for(const frame of data.unregistered_frames) {const a=document.createElement('a');a.href=`/jobs/${encodeURIComponent(config.job)}/frame_${String(frame).padStart(6,'0')}_view_00.jpg`;a.target='_blank';a.rel='noopener';a.textContent=`เฟรม ${frame}`;$('#missing').append(a);}
  }
  $('#find-form').hidden=true;
  for(const selector of ['#position','#position-slider','#direction','#direction-slider','#overview','#focus','#camera-view','#toggle-points','#select-area','#select-range','#select-ten','#select-all','#select-current']) $(selector).disabled=false;
}
async function load() {
  try {
    const response=await fetch(`/api/poses/${encodeURIComponent(config.job)}`,{cache:'no-store'});
    const result=await response.json();
    if(!response.ok) throw new Error(result.error || 'ไม่พบ job');
    if(!result.preview) {
      const status=result.status || {};
      loading.textContent=status.error || (result.state==='running' ? `กำลังหา camera pose…\n${status.step || ''}` : 'ยังไม่มีตำแหน่งกล้อง\nกด “หา Camera Pose เพื่อ preview” ด้านขวา');
      $('#find').disabled=result.state==='running';
      if(result.state==='running') setTimeout(load,2000);
      return;
    }
    data=result.preview; renderSummary(); setupScene(); loading.hidden=true;
    trainReady=result.training_ready; jobBusy=result.busy; updateSubset();
    $('#find').disabled = !!result.busy;
    if (!result.training_ready) {
      $('#find-form').hidden = false;
      const warning = document.createElement('div'); warning.className='warning'; warning.textContent='ชุด pose นี้ยังไม่ผ่านการตรวจสำหรับ train · กดหา Camera Pose เพื่อตรวจใหม่ก่อน'; $('#warnings').append(warning);
    }
    if (result.status?.error) {
      const warning=document.createElement('div'); warning.className='warning'; warning.textContent=result.status.error; $('#warnings').append(warning);
    }
  } catch(error) {console.error(error);loading.textContent='เปิด Camera Preview ไม่สำเร็จ\n'+error.message;}
}
$('#position').addEventListener('change',event=>selectCamera(event.target.value));
$('#position-slider').addEventListener('input',event=>selectCamera(event.target.value));
$('#direction').addEventListener('change',event=>selectView(event.target.value));
$('#direction-slider').addEventListener('input',event=>selectView(data.cameras[selectedCamera].views[Number(event.target.value)]));
$('#select-area').addEventListener('click',toggleSelectionMode);
$('#select-range').addEventListener('click',()=>setTrainingRange(Number($('#range-start').value)-1,Number($('#range-end').value)-1));
$('#select-ten').addEventListener('click',()=>setTrainingRange(selectedCamera,selectedCamera+9));
$('#select-all').addEventListener('click',()=>setTrainingRange(0,data.cameras.length-1));
$('#select-current').addEventListener('click',()=>{if(trainingCameras.has(selectedCamera)) trainingCameras.delete(selectedCamera); else trainingCameras.add(selectedCamera);updateSubset();});
for(const side of ['start','end']) {
  $(`#range-${side}-slider`).addEventListener('input',event=>{$(`#range-${side}`).value=event.target.value;setTrainingRange(Number($('#range-start').value)-1,Number($('#range-end').value)-1);});
}
$('#train-form').addEventListener('submit',event=>{if($('#train').disabled) event.preventDefault(); else $('#train').disabled=true;});
$('#previous').addEventListener('click',()=>selectCamera(selectedCamera-1));
$('#next').addEventListener('click',()=>selectCamera(selectedCamera+1));
$('#overview').addEventListener('click',overview);
$('#focus').addEventListener('click',focusSelection);
$('#camera-view').addEventListener('click',viewFromCamera);
$('#toggle-points').addEventListener('click',()=>{pointsVisible=!pointsVisible;pointCloud.visible=pointsVisible&&!originalMode;$('#toggle-points').textContent=pointsVisible?'ซ่อนจุดฉาก':'แสดงจุดฉาก';});
addEventListener('resize',resize);
load();
