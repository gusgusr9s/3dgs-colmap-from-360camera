import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(value='') {this.value=value;this.listeners={};this.children=[];this.hidden=true;this.textContent='';}
  addEventListener(type,callback) {(this.listeners[type]??=[]).push(callback);}
  dispatchEvent(event) {for(const callback of this.listeners[event.type]??[])callback(event);}
  append(...items) {this.children.push(...items);}
  replaceChildren() {this.children=[];}
  removeAttribute(name) {delete this[name];}
  closest() {return null;}
  async click() {for(const callback of this.listeners.click??[])await callback();}
}
const button=new Element(), status=new Element();
const fields=Object.fromEntries(Object.entries({sample_every:'8',sampling_guard:'manual',preview_duration:'5',
  projection:'panorama',start_seconds:'0',end_seconds:'20'}).map(([key,value])=>[key,new Element(value)]));
const nodes={'[data-temporary-pose]':button,'[data-temporary-pose-status]':status,
  '[data-temporary-map-frame]':new Element(),'[data-temporary-map-empty]':new Element(),
  '[data-temporary-pose-stale]':new Element(),'[data-preview-duration-note]':new Element()};
const form=new Element();
Object.assign(form,{dataset:{settingsSource:'uploads:clip.mp4',sourceFps:'30',sourceDuration:'20'},isConnected:true,
  elements:{namedItem:key=>fields[key]},reportValidity:()=>true,querySelector:selector=>nodes[selector]});
let requested,adviceBasis='sampling_target';
const context=vm.createContext({console,
  document:{querySelectorAll:()=>[form],createElement:()=>new Element()},
  Event:class {constructor(type){this.type=type;}},
  FormData:class extends Map {constructor(){super(Object.entries(fields).map(([key,input])=>[key,input.value]));}},
  fetch:async(url,options)=>{
    if(options?.method==='POST'){
      requested={url,options};return {ok:true,json:async()=>({ok:true,redirect_to:'/pose-preview/'+'a'.repeat(32)})};
    }
    return {ok:true,json:async()=>({state:'ready',preview:{spacing:{mean:.04,ratio_to_reference:.3,
      reference:{job:'cbf586354f'},recommendation:{direction:'increase',sample_every:10,current_sample_every:8,
      basis:adviceBasis,minimum_preview_seconds:2}}}})};
  },
});
vm.runInContext(fs.readFileSync(new URL('../assets/app/temporary_poses.mjs',import.meta.url),'utf8'),context);
assert.equal(requested,undefined,'Opening a Source form must not start a map');
form.dispatchEvent({type:'source-profile',detail:{profile:{kind:'video',detected_projection:'panorama'}}});
assert.equal(button.disabled,false);
await button.click();await new Promise(resolve=>setImmediate(resolve));
assert.equal(requested.options.body.get('action'),'preview_pose');
assert.equal(requested.options.body.get('sample_every'),'8');
assert.equal(status.children[0].href,'/pose-preview/'+'a'.repeat(32));
const advice=status.children.at(-2), apply=status.children.at(-1);
assert.match(advice.textContent,/3 ตำแหน่งต่อวินาที/,'Sampling target explains the advice even when a reference exists');
assert.doesNotMatch(advice.textContent,/ระยะเฉลี่ยชิด/);
assert.equal(apply.textContent,'ใช้ทุก 10 เฟรม');
await apply.click();
assert.equal(fields.sample_every.value,'10');assert.equal(fields.sampling_guard.value,'manual');
await button.click();await new Promise(resolve=>setImmediate(resolve));
fields.sample_every.value='15';await status.children.at(-1).click();
assert.equal(fields.sample_every.value,'15','Changing the form prevents stale advice from being applied');
adviceBasis='depth_reference';
await button.click();await new Promise(resolve=>setImmediate(resolve));
assert.match(status.children.at(-2).textContent,/ระยะเฉลี่ยชิดกว่างานอ้างอิง/);
context.fetch=async()=>({ok:false,json:async()=>({error:'Invalid clip'})});
await button.click();assert.equal(status.textContent,'Invalid clip');assert.equal(button.disabled,false);
console.log('Temporary map UI passed: sampling target, reference advice, apply, stale edits and errors.');
