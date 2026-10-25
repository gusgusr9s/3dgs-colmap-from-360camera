import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { SparkRenderer,SplatMesh } from '@sparkjsdev/spark';
import { sceneObjectMatrix,sceneWorldColliders } from './scene_geometry.mjs';
import { moveScenePlayer } from './scene_collision.mjs';

const $=selector=>document.querySelector(selector),canvas=$('#canvas'),keys=new Set();
let doc,renderer,controls,walking=false,yaw=0,pitch=0,fall=0,last=null,pointer=null,focused=true;
const camera=new THREE.PerspectiveCamera(55,1,.001,100000),scene=new THREE.Scene();
const boxes=[];let loaded=0;
function reset(){walking=false;keys.clear();fall=0;document.exitPointerLock?.();controls?.dispose();camera.position.fromArray(doc.view.position);camera.up.set(0,1,0);controls=new OrbitControls(camera,canvas);controls.target.fromArray(doc.view.target);controls.enableDamping=true;controls.update();$('#walk').textContent='ทดลองเดิน';}
function look(){camera.quaternion.setFromEuler(new THREE.Euler(pitch,yaw,0,'YXZ'));}
function walk(){if(walking){reset();return;}walking=true;keys.clear();last=null;fall=0;controls.enabled=false;camera.position.fromArray(doc.spawn.position);yaw=doc.spawn.yaw;pitch=0;look();canvas.focus();$('#walk').textContent='หยุดเดิน (Esc)';}
function resize(){const rect=canvas.parentElement.getBoundingClientRect();renderer.setSize(Math.max(1,rect.width),Math.max(1,rect.height),false);camera.aspect=rect.width/Math.max(1,rect.height);camera.updateProjectionMatrix();}
$('#reset').addEventListener('click',()=>doc&&reset());$('#walk').addEventListener('click',()=>doc&&walk());
$('#colliders').addEventListener('change',event=>boxes.forEach(mesh=>{mesh.visible=event.target.checked;}));
addEventListener('keydown',event=>{if(event.code==='Escape'&&walking){reset();return;}if(!walking||(!document.pointerLockElement&&document.activeElement!==canvas))return;if(['KeyW','KeyA','KeyS','KeyD','ShiftLeft','ShiftRight'].includes(event.code)){event.preventDefault();keys.add(event.code);}});
addEventListener('keyup',event=>keys.delete(event.code));addEventListener('blur',()=>{focused=false;keys.clear();last=null;});addEventListener('focus',()=>{focused=true;last=null;});
canvas.addEventListener('blur',()=>keys.clear());document.addEventListener('pointerlockchange',()=>keys.clear());document.addEventListener('visibilitychange',()=>{keys.clear();last=null;});
canvas.addEventListener('pointerdown',event=>{canvas.focus();if(walking){pointer=[event.clientX,event.clientY];canvas.setPointerCapture(event.pointerId);}});
canvas.addEventListener('pointermove',event=>{if(!walking||(!pointer&&document.pointerLockElement!==canvas))return;const locked=document.pointerLockElement===canvas;const dx=locked?event.movementX:event.clientX-pointer[0],dy=locked?event.movementY:event.clientY-pointer[1];if(pointer)pointer=[event.clientX,event.clientY];yaw-=dx*.003;pitch=Math.max(-1.48,Math.min(1.48,pitch-dy*.003));look();});
canvas.addEventListener('pointerup',()=>{pointer=null;});canvas.addEventListener('dblclick',()=>{if(walking)canvas.requestPointerLock?.()?.catch?.(()=>{});});
try{
  const response=await fetch('./scene.json');if(!response.ok)throw new Error('ไม่พบข้อมูลฉาก');const data=await response.json();doc=data.scene;
  $('#title').textContent=doc.name;THREE.ColorManagement.workingColorSpace=THREE.SRGBColorSpace;
  renderer=new THREE.WebGLRenderer({canvas,antialias:false});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.toneMapping=THREE.NoToneMapping;
  scene.background=new THREE.Color(0x10171e);scene.add(new SparkRenderer({renderer,preBlurAmount:.3,blurAmount:0,sortRadial:false,enableLod:true}));
  scene.add(new THREE.HemisphereLight(0xffffff,0x6b7785,2));const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(5,10,4);scene.add(light);
  reset();new ResizeObserver(resize).observe(canvas.parentElement);resize();
  const worldBoxes=sceneWorldColliders(doc);
  for(const box of worldBoxes.filter(box=>box.enabled)){const mesh=new THREE.Mesh(new THREE.BoxGeometry(...box.size),new THREE.MeshBasicMaterial({color:box.kind==='floor'?0x83c65b:0xf2be74,transparent:true,opacity:.2,depthWrite:false}));mesh.position.fromArray(box.position);mesh.rotation.set(...box.rotation);mesh.visible=false;scene.add(mesh);boxes.push(mesh);}
  renderer.setAnimationLoop(time=>{const dt=last===null?0:Math.min(.05,Math.max(0,(time-last)/1000));last=time;
    if(walking&&focused&&!document.hidden&&(document.activeElement===canvas||document.pointerLockElement===canvas)&&dt){
      const f=Number(keys.has('KeyW'))-Number(keys.has('KeyS')),r=Number(keys.has('KeyD'))-Number(keys.has('KeyA'));
      const delta=new THREE.Vector3(-Math.sin(yaw)*f+Math.cos(yaw)*r,0,-Math.cos(yaw)*f-Math.sin(yaw)*r);if(delta.lengthSq()>1)delta.normalize();delta.multiplyScalar(doc.player.speed*dt*(keys.has('ShiftLeft')||keys.has('ShiftRight')?2:1));fall=Math.max(-50,fall-9.81*dt);delta.y=fall*dt;
      const result=moveScenePlayer(camera.position.toArray(),delta.toArray(),worldBoxes,doc.player);camera.position.fromArray(result.position);if(result.grounded)fall=0;
      if(camera.position.y< -1000)reset();
    }else if(!walking)controls.update();renderer.render(scene,camera);
  });
  for(const object of doc.objects.filter(object=>object.visible)){
    const asset=data.assets.find(asset=>asset.kind===object.asset.kind&&asset.ref===object.asset.ref);if(!asset)throw new Error('ไม่พบไฟล์ '+object.name);
    $('#status').textContent='กำลังโหลด '+object.name;
    const group=new THREE.Group();group.applyMatrix4(sceneObjectMatrix(object));scene.add(group);
    if(asset.kind==='glb'){const gltf=await new GLTFLoader().loadAsync(asset.url);group.add(gltf.scene);}else{const mesh=new SplatMesh({url:asset.url,extSplats:true,lod:true,enableLod:true});await mesh.initialized;group.add(mesh);}loaded++;
  }
  $('#status').textContent=`พร้อม · ${loaded} โมเดล · ลากหมุนดู หรือทดลองเดินด้วย WASD · Shift เร็วขึ้น · Esc หยุดเดิน`;
  window.scenePlayer=Object.freeze({get loaded(){return loaded;},get walking(){return walking;},get position(){return camera.position.toArray();}});
}catch(error){$('#error').textContent=error.message;$('#status').textContent='เปิดฉากไม่สำเร็จ';}
