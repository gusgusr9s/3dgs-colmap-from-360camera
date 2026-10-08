// Form behavior with a simulated DOM; rendering is checked separately.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(value='') {this.value=String(value);this.listeners={};this.children=[];this.dataset={};this.disabled=true;}
  addEventListener(type,callback){this.listeners[type]=callback;}
  append(item){this.children.push(item);}
  replaceChildren(){this.children=[];}
}
const keys=['quality','width','height','fov','views','sample_every','projection'];
function form(mode='new') {
  const inputs=Object.fromEntries(keys.map((key,index)=>[key,new Element(['high',768,768,90,8,8,'auto'][index])]));
  const nodes={};
  for(const selector of ['data-apply-suggestions','data-suggestion-note','data-view-estimate','data-geometry-note','data-source-profile','data-suggestion-warnings'])nodes[`[${selector}]`]=new Element();
  const buttons=keys.map(key=>{const button=new Element();button.dataset.useSetting=key;nodes[`[data-suggestion-for="${key}"]`]=new Element();return button;});
  return {dataset:{settingsMode:mode,settingsSource:'data/raw:clip.mp4'},inputs,nodes,buttons,
    addEventListener(){},
    elements:{namedItem(key){return inputs[key];}},querySelector(selector){return nodes[selector];},
    querySelectorAll(selector){return selector==='[data-use-setting]' ? buttons : [...buttons,nodes['[data-apply-suggestions]']];}};
}
let result={settings:{quality:'high',width:768,height:768,fov:90,views:8,sample_every:1,projection:'panorama'},
  profile:{width:4096,height:2048,fps:5,frame_count:77,kind:'video',duration_seconds:15.4,detected_projection:'panorama'},
  reasons:Object.fromEntries(keys.map(key=>[key,'from profile'])),history:{selected:{job:'old',level:2}},warnings:[]};
const context=vm.createContext({WeakMap,Set,AbortController,URLSearchParams,console,
  document:{addEventListener(){},createElement(){return new Element();}},
  fetch:async()=>({ok:true,json:async()=>result})});
vm.runInContext(fs.readFileSync(new URL('../view_settings.mjs',import.meta.url),'utf8'),context);
context.form=form();await vm.runInContext('loadSuggestions(form)',context);
assert.equal(context.form.inputs.sample_every.value,1);
assert.ok(context.form.nodes['[data-view-estimate]'].textContent.includes('1,232'));
context.form.inputs.width.value='900';context.form.inputs.width.listeners.input();
context.form.inputs.quality.value='ultra';context.form.inputs.quality.listeners.input();
result={...result,settings:{...result.settings,quality:'ultra',width:1024,height:1024}};
await vm.runInContext('loadSuggestions(form)',context);
assert.equal(context.form.inputs.width.value,'900');
assert.equal(context.form.inputs.height.value,1024);
context.form.nodes['[data-apply-suggestions]'].listeners.click();
assert.equal(context.form.inputs.width.value,1024);
context.form.inputs.sample_every.value='10';context.form.inputs.sample_every.listeners.input();
assert.ok(context.form.nodes['[data-view-estimate]'].textContent.includes('0.3'));
context.edit=form('edit');context.edit.inputs.width.value='640';
await vm.runInContext('loadSuggestions(edit)',context);
assert.equal(context.edit.inputs.width.value,'640');
context.edit.buttons.find(button=>button.dataset.useSetting==='width').listeners.click();
assert.equal(context.edit.inputs.width.value,1024);
console.log('Suggestion UI passed: autofill, manual edits, quality recalculation, per-field apply, saved edits and effective sampling.');
