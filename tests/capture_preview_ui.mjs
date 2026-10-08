// Simulated DOM interaction checks; this does not rasterize or screenshot a browser.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element {
  constructor(value=''){this.value=String(value);this.listeners={};this.children=[];this.src='';this.hidden=false;this.disabled=false;this.checked=false;this.currentTime=0;this.paused=true;this.classList={add(){},remove(){}};}
  addEventListener(type,fn){(this.listeners[type] ||= []).push(fn);}
  fire(type,event={}){for(const fn of this.listeners[type] || [])fn(event);}
  append(...items){this.children.push(...items);}
  replaceChildren(){this.children=[];}
  pause(){this.paused=true;}
}
const selectors=['clip-video','clip-controls','clip-actions','clip-start','clip-end','clip-start-number','clip-end-number','clip-label','capture-position','capture-yaw','capture-frame','capture-direction','capture-raw','capture-view','capture-strip','capture-directions','direction-control','capture-status','auto-model','capture-warning','set-start','set-end','reset-clip'];
const nodes=Object.fromEntries(selectors.map(key=>[`[data-${key}]`,new Element()]));
const inputs=Object.fromEntries(Object.entries({width:640,height:640,fov:90,views:8,sample_every:10,projection:'panorama',quality:'high',start_seconds:.5,end_seconds:2}).map(([key,value])=>[key,new Element(value)]));
nodes['[data-clip-start-number]']=inputs.start_seconds;nodes['[data-clip-end-number]']=inputs.end_seconds;
nodes['[data-auto-model]'].checked=true;nodes['[data-capture-position]'].value='0';nodes['[data-capture-yaw]'].value='0';
const dialog=new Element();
const form=new Element();form.dataset={settingsSource:'data/raw:360.mp4'};
form.elements={namedItem(key){return inputs[key];}};form.querySelector=selector=>nodes[selector];form.closest=()=>dialog;
let timers=new Map(),nextTimer=0;
const profile={width:2048,height:1024,fps:20,frame_count:100,duration_seconds:5,kind:'video',modified_ns:1};
function resultFor(url){
  const query=new URL(url,'http://localhost').searchParams;
  const step=Math.min(Number(query.get('sample_every')),6),first=Math.ceil(Number(query.get('start_seconds'))*20),last=Number(query.get('end_seconds'))*20 || 100;
  if(last<=first || last>100) return {error:'invalid range'};
  const directions=Array.from({length:16},(_,i)=>({yaw:i%8*45,pitch:i<8 ? 0 : i<12 ? -55 : 55}));
  return {profile,settings:{width:Number(query.get('width')),height:Number(query.get('height'))},source_url:'/source/raw/360.mp4',
    plan:{start_frame:first,end_frame:last,effective_sample_every:step,requested_sample_every:Number(query.get('sample_every')),
      start_seconds:first/20,end_seconds:last/20,sample_gap_seconds:step/20,positions:Math.ceil((last-first)/step),
      views_per_frame:16,total_views:Math.ceil((last-first)/step)*16,panorama:true,directions}};
}
const context=vm.createContext({WeakMap,Set,URLSearchParams,AbortController,console,form,
  setTimeout(fn){timers.set(++nextTimer,fn);return nextTimer;},clearTimeout(id){timers.delete(id);},
  document:{addEventListener(){},createElement(){return new Element();}},
  fetch:async url=>{const result=resultFor(url);return {ok:!result.error,json:async()=>result};}});
vm.runInContext(fs.readFileSync(new URL('../capture_preview.mjs',import.meta.url),'utf8'),context);
vm.runInContext('initCapture(form)',context);
await vm.runInContext('loadCapture(form)',context);
assert.equal(nodes['[data-capture-position]'].max,4);
assert.ok(nodes['[data-capture-status]'].textContent.includes('80'));
assert.ok(nodes['[data-capture-status]'].textContent.includes('ลดจาก 10'));
assert.equal(nodes['[data-clip-video]'].src,'/source/raw/360.mp4');
assert.ok(nodes['[data-capture-strip]'].children[1].children[1].textContent.includes('#16'));
inputs.sample_every.value='2';await vm.runInContext('loadCapture(form)',context);
nodes['[data-capture-position]'].value='2';nodes['[data-capture-yaw]'].value='5';vm.runInContext('showCapture(form)',context);
assert.ok(nodes['[data-capture-frame]'].textContent.includes('frame 14'));
assert.ok(nodes['[data-capture-direction]'].textContent.includes('225°'));
assert.ok(nodes['[data-capture-view]'].src.includes('ordinal=2'));
assert.ok(nodes['[data-capture-view]'].src.includes('direction=5'));
// Trim slider and current-video-time controls update submitted values.
nodes['[data-clip-start]'].value='3';nodes['[data-clip-start]'].fire('input');assert.equal(Number(inputs.start_seconds.value),1.95);
nodes['[data-clip-start]'].value='1';nodes['[data-clip-start]'].fire('input');assert.equal(Number(inputs.start_seconds.value),1);
nodes['[data-clip-video]'].currentTime=1.5;nodes['[data-set-end]'].fire('click');assert.equal(inputs.end_seconds.value,1.5);
await vm.runInContext('loadCapture(form)',context);
assert.equal(nodes['[data-capture-position]'].max,4);
assert.equal(nodes['[data-auto-model]'].disabled,true);
// Invalid range blocks submission, then restores the user's auto-training preference.
inputs.end_seconds.value=.5;await vm.runInContext('loadCapture(form)',context);
let prevented=false;form.fire('submit',{preventDefault(){prevented=true;}});assert.equal(prevented,true);
nodes['[data-reset-clip]'].fire('click');await vm.runInContext('loadCapture(form)',context);
assert.equal(nodes['[data-capture-position]'].max,49);assert.equal(nodes['[data-auto-model]'].checked,true);
nodes['[data-clip-video]'].currentTime=1.4;nodes['[data-clip-video]'].fire('seeked');
assert.equal(nodes['[data-capture-position]'].value,14);
// Old responses cannot overwrite the latest settings.
let pending=[];context.fetch=url=>new Promise(resolve=>pending.push({resolve,result:resultFor(url)}));
inputs.width.value=640;const old=vm.runInContext('loadCapture(form)',context);
inputs.width.value=768;const latest=vm.runInContext('loadCapture(form)',context);
pending[1].resolve({ok:true,json:async()=>pending[1].result});await latest;
pending[0].resolve({ok:true,json:async()=>pending[0].result});await old;
assert.ok(nodes['[data-capture-direction]'].textContent.includes('768'));
// A response arriving during the debounce interval must not validate edited values.
pending=[];const inFlight=vm.runInContext('loadCapture(form)',context);
inputs.end_seconds.value=.1;inputs.end_seconds.fire('input');
pending[0].resolve({ok:true,json:async()=>pending[0].result});await inFlight;
prevented=false;form.fire('submit',{preventDefault(){prevented=true;}});assert.equal(prevented,true);
nodes['[data-clip-video]'].paused=false;dialog.fire('close');assert.equal(nodes['[data-clip-video]'].paused,true);
console.log('Capture UI passed: trim, exact adjacent samples, interval cap, direction slider, payload, invalid range, stale responses and automatic model preference.');
