// Camera math and controls with real Three classes and a simulated DOM.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const code=await (await fetch('https://cdn.jsdelivr.net/npm/three@0.186.1/build/three.core.js')).text();
const THREE=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
class Element {
  constructor(){this.children=[];this.listeners={};this.dataset={};this.style={};this.value='0';this.classList={toggle(){}};this.attributes={};}
  addEventListener(type,fn){(this.listeners[type] ||= []).push(fn);}
  fire(type,event={}){for(const fn of this.listeners[type] || []) fn(event);}
  append(...items){this.children.push(...items);}
  replaceChildren(){this.children=[];}
  setAttribute(key,value){this.attributes[key]=value;}
  setPointerCapture(){}
  getBoundingClientRect(){return {left:0,top:0,width:640,height:480};}
}
const nodes={};
for(const id of ['config','canvas','loading','position','position-slider','direction','direction-slider','direction-label','image','image-link','selection','thumbnails','previous','next','hint','stats','warnings','missing-section','missing','find-form','find','train','train-form','overview','focus','camera-view','toggle-points','select-area','selected-frames','selection-signature','subset-summary','subset-warning','range-start','range-end','range-start-slider','range-end-slider','select-range','select-ten','select-all','select-current','lasso-path']) nodes['#'+id]=new Element();
nodes['#config'].textContent='{"job":"check"}'; nodes['.stage']={clientWidth:640,clientHeight:480};
const views=[],cameras=[];
for(let position=0;position<12;position++) {
  const indices=[];
  for(let direction=0;direction<8;direction++) {
    const matrix=new THREE.Matrix4().makeRotationY(direction*Math.PI/4);
    matrix.setPosition(position*.25,0,0);
    indices.push(views.length); views.push({name:`${position}_${direction}.jpg`,image_url:`/${position}/${direction}.jpg`,
      frame_index:position*8,yaw_deg:direction*45,pitch_deg:0,transform_matrix:[0,1,2,3].map(row=>[0,1,2,3].map(column=>matrix.elements[column*4+row])),w:64,h:64,fl_x:32,fl_y:32,cx:32,cy:32});
  }
  cameras.push({frame_index:position*8,position:[position*.25,0,0],views:indices});
}
const preview={input_signature:'signature',cameras,views,points:{positions:[0,0,-2,1,0,-2]},segments:[],warnings:[],unregistered_frames:[],summary:{registered_positions:12,input_positions:12,registered_views:96,sparse_points:2,suspicious_segments:0}};
class Renderer{setPixelRatio(){} setSize(){} setAnimationLoop(){} render(){}}
class Textures{load(url,callback){callback(new THREE.Texture());}}
class Controls{constructor(camera){this.camera=camera;this.target=new THREE.Vector3();}update(){this.camera.lookAt(this.target);this.camera.updateMatrixWorld();}}
const context=vm.createContext({THREE:{...THREE,WebGLRenderer:Renderer,TextureLoader:Textures},OrbitControls:Controls,
  document:{querySelector(selector){return nodes[selector];},querySelectorAll(selector){return selector==='[data-view]'?nodes['#thumbnails'].children:[];},createElement(){return new Element();},createTextNode(text){return {textContent:text};}},
  console,devicePixelRatio:1,addEventListener(){},setTimeout(){},
  fetch:async()=>({ok:true,json:async()=>({preview,training_ready:true,busy:false})})});
const source=fs.readFileSync(new URL('../assets/app/pose_viewer.mjs',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
vm.runInContext(source,context);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(nodes['#train'].disabled,false);
nodes['#direction-slider'].fire('input',{target:{value:'3'}});
assert.equal(vm.runInContext('selectedView',context),3);
assert.equal(nodes['#image'].src,'/0/3.jpg');
vm.runInContext('selectCamera(4)',context);
assert.equal(vm.runInContext('selectedView',context),35);
assert.equal(Number(nodes['#direction-slider'].value),3);
nodes['#select-ten'].fire('click');
assert.equal(JSON.parse(nodes['#selected-frames'].value).length,8);
vm.runInContext('setTrainingRange(0,9)',context);
assert.equal(JSON.parse(nodes['#selected-frames'].value).length,10);
assert.equal(nodes['#selection-signature'].value,'signature');
vm.runInContext('setTrainingRange(0,1)',context);
assert.equal(nodes['#train'].disabled,true);
nodes['#select-all'].fire('click');
assert.equal(nodes['#selected-frames'].value,'');
assert.equal(nodes['#train'].disabled,false);
assert.equal(vm.runInContext('pointInPolygon([2,2],[[0,0],[5,0],[5,5],[0,5]])',context),true);
assert.equal(vm.runInContext('pointInPolygon([8,2],[[0,0],[5,0],[5,5],[0,5]])',context),false);
nodes['#select-area'].fire('click');
assert.equal(vm.runInContext('controls.enabled',context),false);
nodes['#canvas'].fire('pointerdown',{button:0,clientX:0,clientY:0,pointerId:1});
for(const [x,y] of [[640,0],[640,480],[0,480],[0,0]]) nodes['#canvas'].fire('pointermove',{clientX:x,clientY:y});
nodes['#canvas'].fire('pointerup',{button:0,clientX:0,clientY:0});
assert.equal(vm.runInContext('trainingCameras.size',context),12);
nodes['#select-area'].fire('click');
assert.equal(vm.runInContext('controls.enabled',context),true);
for(const id of ['spacing-panel','spacing-min','spacing-max','spacing-summary','spacing-selection'])nodes['#'+id]=new Element();
nodes['#spacing-min'].value='.15';nodes['#spacing-max'].value='1';
preview.spacing={minimum:.25,median:.25,maximum:.25,minimum_percent:.1,median_percent:.1,maximum_percent:.1,
  segments:cameras.slice(1).map((_,i)=>({start:i,end:i+1,distance:.25,percent:.1}))};
vm.runInContext('renderSpacing()',context);
assert.ok(nodes['#spacing-summary'].textContent.includes('ถี่ 11'));
nodes['#spacing-min'].value='.05';vm.runInContext('renderSpacing()',context);
assert.ok(nodes['#spacing-summary'].textContent.includes('อยู่ในช่วง 11'));
nodes['#spacing-max'].value='.01';vm.runInContext('renderSpacing()',context);
assert.ok(nodes['#spacing-summary'].textContent.includes('ขอบบนต้องมากกว่า'));
preview.spacing.reference={job:'cbf586354f',raw_mean:.1122};preview.spacing.mean=.03;preview.spacing.ratio_to_reference=.3;
preview.spacing.segments.forEach((s,i)=>s.reference_ratio=[.25,.75,1.25,3][i%4]);
nodes['#spacing-min'].value='1';nodes['#spacing-max'].value='2';
vm.runInContext('selectCamera(0);updateSubset()',context);
assert.ok(nodes['#spacing-summary'].textContent.includes('30.0%'));
assert.equal(vm.runInContext('markers[1].material.color.getHex()',context),0xef5350,'Very dense camera points stay red after training selection refresh');
assert.equal(vm.runInContext('markers[2].material.color.getHex()',context),0xf5d442,'Dense camera points are yellow');
assert.equal(vm.runInContext('markers[0].material.color.getHex()',context),0x75aaff,'Selected camera stays highlighted');
vm.runInContext("config.temporary=true;config.preview_api='/api/pose-preview/temp'",context);
let temporaryUrl;
context.fetch=async url=>{temporaryUrl=url;return {ok:true,json:async()=>({state:'queued',status:{step:'รอคิว Train'}})};};
await vm.runInContext('load()',context);
assert.equal(temporaryUrl,'/api/pose-preview/temp');
assert.ok(nodes['#loading'].textContent.includes('รอคิว Train'));
context.fetch=async()=>({ok:true,json:async()=>({state:'ready',preview,training_ready:false,busy:false})});
await vm.runInContext('load()',context);
assert.equal(nodes['#find-form'].hidden,true,'Temporary previews do not advertise Job actions');
assert.equal(nodes['#train'].disabled,true);
console.log('Pose UI passed: controls, spacing and queued temporary previews without Train.');
