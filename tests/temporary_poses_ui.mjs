import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const button={disabled:true,addEventListener(type,callback){this.click=callback;}};
const status={textContent:'',children:[],replaceChildren(){this.children=[];},append(value){this.children.push(value);}};
const fields={sample_every:{value:'8',dispatchEvent(){}},sampling_guard:{value:'auto',dispatchEvent(){}},preview_duration:{value:'5'}};
const form={elements:{namedItem(key){return fields[key];}},reportValidity(){return true;},querySelector(selector){return selector==='[data-temporary-pose]'?button:status;}};
let requested,closed=false,destination;
const popup={opener:{},document:{body:{}},location:{replace(value){destination=value;}},close(){closed=true;}};
const context=vm.createContext({
  document:{querySelectorAll(){return [form];},createElement(){return {addEventListener(type,callback){this.click=callback;}};}},
  window:{open(){return popup;}},
  Event:class {},FormData:class extends Map{constructor(){super([['action','generate'],...Object.entries(fields).map(([key,input])=>[key,input.value])]);}},
  fetch:async(url,options)=>{
    if(options.method!=='POST')return {ok:true,json:async()=>({state:'ready',preview:{spacing:{mean:.04,ratio_to_reference:.3,reference:{job:'cbf586354f'},recommendation:{direction:'increase',sample_every:30,current_sample_every:8,minimum_preview_seconds:6}}}})};
    requested={url,options};return {ok:true,json:async()=>({ok:true,redirect_to:'/pose-preview/'+'a'.repeat(32)})};
  },
});
vm.runInContext(fs.readFileSync(new URL('../assets/app/temporary_poses.mjs',import.meta.url),'utf8'),context);
assert.equal(button.disabled,false);
assert.equal(requested,undefined,'Opening a form must never start a preview');
await button.click();
await new Promise(resolve=>setImmediate(resolve));
assert.equal(requested.options.body.get('action'),'preview_pose');
assert.equal(requested.options.body.get('sample_every'),'8');
assert.equal(popup.opener,null);
assert.equal(destination,'/pose-preview/'+'a'.repeat(32));
assert.equal(status.children[0].href,destination);
const apply=status.children.at(-1);
assert.equal(apply.textContent,'ใช้ทุก 30 เฟรม');
apply.click();assert.equal(fields.sample_every.value,'30');assert.equal(fields.sampling_guard.value,'manual');
await button.click();await new Promise(resolve=>setImmediate(resolve));
fields.sample_every.value='16';status.children.at(-1).click();
assert.equal(fields.sample_every.value,'16','Changing settings after a probe prevents stale advice from being applied');
context.fetch=async()=>({ok:false,json:async()=>({error:'Invalid clip'})});
await button.click();assert.equal(status.textContent,'Invalid clip');assert.equal(closed,true);assert.equal(button.disabled,false);
console.log('Temporary preview UI passed: explicit action, separate map, errors and no automatic processing.');
