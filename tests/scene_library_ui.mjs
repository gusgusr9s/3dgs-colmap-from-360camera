// Exercise the library's real event handlers; no browser/GPU is implied.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../assets/app/scene_library.mjs', import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function library(href = 'http://localhost/scenes', responses = []) {
  const elements = new Map(); const calls = []; let reloaded = false;
  const element = name => {
    if (!elements.has(name)) elements.set(name, {value: 'ห้องสองแห่ง', textContent: '', disabled: false, listeners: {}, dataset: {},
      addEventListener(type, callback) { this.listeners[type] = callback; },
      querySelector() { return element('submit'); }});
    return elements.get(name);
  };
  element('delete').dataset = {sceneDelete:'a'.repeat(32),revision:'3'};
  const location = {get href(){return href;},set href(value){href=new URL(value,href).href;},reload:()=>{reloaded=true;}};
  const context = vm.createContext({document:{querySelector:element,querySelectorAll:()=>[element('delete')]},
    location,URL,confirm:()=>true,
    fetch:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)}); const next=responses.shift() || {ok:true,body:{id:'b'.repeat(32)}}; return {ok:next.ok,json:async()=>next.body};}});
  vm.runInContext(source,context);
  const fire = async(name,type,event={})=>element(name).listeners[type]({preventDefault(){},...event});
  return {context,element,calls,fire,get reloaded(){return reloaded;}};
}
const create = library('http://localhost/scenes?model=room_a.ply');
await create.fire('#scene-create','submit');
await flush();
assert.equal(create.calls[0].path,'/api/scenes');
assert.deepEqual(create.calls[0].body.objects,[{asset:{kind:'ply',ref:'room_a.ply'},name:'room_a.ply'}]);
assert.equal(create.context.location.href,'http://localhost/scene/'+'b'.repeat(32));
await create.fire('#scene-create','submit');
assert.equal(create.calls.length,1,'Repeated submits cannot create duplicate scenes');

const failed = library('http://localhost/scenes',[{ok:false,body:{error:'ไม่พบโมเดล'}}]);
await failed.fire('#scene-create','submit'); await flush();
assert.equal(failed.element('#scene-library-status').textContent,'ไม่พบโมเดล');
assert.equal(failed.element('submit').disabled,false,'Failure re-enables retry');
await failed.fire('#scene-create','submit'); await flush();
assert.equal(failed.calls.length,2);

const imported = library();
const file = {size:100,text:async()=>JSON.stringify({name:'สำเนา',objects:[{asset:{kind:'spz',ref:'a'.repeat(32)}}]})};
const input = {files:[file],value:'fixture.json'};
await imported.fire('#scene-import','change',{target:input});
assert.equal(imported.calls[0].body.name,'สำเนา');
assert.equal(input.value,'');
const oversized = library();
await oversized.fire('#scene-import','change',{target:{files:[{size:3*1024*1024}],value:'large.json'}});
assert.equal(oversized.calls.length,0);
assert.match(oversized.element('#scene-library-status').textContent,/2 MB/);

const deletion = library('http://localhost/scenes',[{ok:false,body:{error:'Scene changed'}}]);
await deletion.fire('delete','click');
assert.deepEqual(deletion.calls[0],{path:'/api/scenes/'+'a'.repeat(32)+'/delete',body:{revision:3}});
assert.equal(deletion.reloaded,false);
assert.equal(deletion.element('delete').disabled,false);
assert.equal(deletion.element('#scene-library-status').textContent,'Scene changed');
await deletion.fire('delete','click');
assert.equal(deletion.reloaded,true);
console.log('Scene library passed: create, duplicate-submit guard, retry, model shortcut, JSON import, size limit and revision-aware delete.');
