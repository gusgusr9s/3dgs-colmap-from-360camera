// Browser event contracts: external submit buttons, upload progress, cancellation and retry.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(tag='div') {
    this.tagName=tag.toUpperCase();this.attributes={};this.children=[];this.dataset={};
    this.disabled=false;this.hidden=false;this.textContent='';this.type=tag==='button'?'submit':'';
  }
  append(...nodes) {for(const node of nodes){node.parent=this;this.children.push(node);}}
  setAttribute(key,value) {this.attributes[key]=value;}
  removeAttribute(key) {delete this.attributes[key];}
  hasAttribute(key) {return key in this.attributes;}
  matches(selector) {
    if(selector.startsWith('#'))return this.id===selector.slice(1);
    const match=selector.match(/^\[([^\]]+)\]$/);return !!match && this.hasAttribute(match[1]);
  }
  querySelector(selector) {return this.children.map(node=>node.matches(selector)?node:node.querySelector(selector)).find(Boolean)||null;}
  querySelectorAll(selector) {return this.children.flatMap(node=>[...(selector.split(',').some(s=>node.matches(s))?[node]:[]),...node.querySelectorAll(selector)]);}
  closest(selector) {return selector==='dialog'?(this.tagName==='DIALOG'?this:this.parent?.closest(selector)||null):selector.split(',').some(s=>this.matches(s))?this:this.parent?.closest(selector)||null;}
}
class Events {
  constructor(){this.handlers={};}
  addEventListener(type,handler){(this.handlers[type]||=[]).push(handler);}
  fire(type,data={}){for(const handler of this.handlers[type]||[])handler(data);}
}
class Request extends Events {
  constructor(){super();this.upload=new Events();this.headers={};requests.push(this);}
  open(method,url){this.method=method;this.url=url;}
  setRequestHeader(key,value){this.headers[key]=value;}
  send(payload){this.payload=payload;}
  abort(){this.aborted=true;this.fire('abort');}
}
class Payload {
  constructor(form){this.values=new Map(form.fields);this.disabledAtCapture=form.elements.some(element=>element.name==='file'&&element.disabled);}
  get(name){return this.values.get(name);}
  set(name,value){this.values.set(name,value);}
}
const requests=[], capture=[], bubble=[], documentEvents={}, globalEvents={}, navigations=[];
const document={createElement:tag=>new Element(tag),addEventListener(type,handler,capturing=false){
  if(type==='submit')(capturing?capture:bubble).push(handler);
  else (documentEvents[type]||=[]).push(handler);
}};
const location={href:'http://127.0.0.1:8000/sources',origin:'http://127.0.0.1:8000',assign(url){navigations.push(url);}};
vm.runInNewContext(fs.readFileSync(new URL('../action_feedback.mjs',import.meta.url),'utf8'),{
  document,location,URL,XMLHttpRequest:Request,FormData:Payload,
  addEventListener(type,handler){(globalEvents[type]||=[]).push(handler);},
});
function form(action='upload_source') {
  const dialog=new Element('dialog'), form=new Element('form'), input=new Element('input'), button=new Element('button'), close=new Element('button');
  form.method='post';form.action='/actions';form.fields=[['action',action],['file',{name:'DJI.mp4'}]];
  input.name='file';input.type='file';button.textContent='อัปโหลด';close.type='button';close.setAttribute('data-close','');
  form.elements=[input,button];dialog.append(form,button,close);form.append(input);
  return {dialog,form,input,button,close};
}
function submit(item,targetHandler=null) {
  const event={target:item.form,submitter:item.button,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};
  for(const handler of capture)handler(event);
  targetHandler?.(event);
  for(const handler of bubble)handler(event);
  return event;
}
function display(item){return item.form.querySelector('[data-action-feedback]');}
function text(item,field){return display(item).querySelector(`[data-action-${field}]`).textContent;}
function event(target){return {target,defaultPrevented:false,stopped:false,preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;}};}

const upload=form();submit(upload);
const first=requests.at(-1);
assert.equal(first.method,'POST');assert.equal(first.headers['X-Requested-With'],'XMLHttpRequest');
assert.equal(first.payload.disabledAtCapture,false,'File is captured before controls are disabled');
assert(upload.button.disabled && upload.input.disabled && upload.close.disabled,'Buttons outside the form also lock');
submit(upload);assert.equal(requests.length,1,'Repeated submit must not create a second upload');
first.upload.fire('progress',{lengthComputable:true,loaded:1024**3,total:2*1024**3});
assert(text(upload,'detail').includes('50.0%'));assert(text(upload,'detail').includes('1.00 GB / 2.00 GB'));
const dialogCancel=event(upload.dialog);for(const handler of documentEvents.cancel)handler(dialogCancel);
assert(dialogCancel.defaultPrevented,'Escape cannot dismiss an active upload');
const backdrop=event(upload.dialog);for(const handler of documentEvents.click)handler(backdrop);
assert(backdrop.defaultPrevented && backdrop.stopped,'Backdrop cannot hide the progress');
const unload=event(null);for(const handler of globalEvents.beforeunload)handler(unload);
assert(unload.defaultPrevented,'Navigation interruption is guarded');
first.upload.fire('progress',{lengthComputable:true,loaded:2*1024**3,total:2*1024**3});
assert(display(upload).querySelector('[data-request-cancel]').disabled,'Cannot cancel after all bytes were sent');
first.upload.fire('load');assert(text(upload,'title').includes('กำลังตรวจและบันทึก'));
assert(display(upload).querySelector('[data-request-cancel]').disabled,'Cancellation ends when server processing begins');
first.response={ok:true,message:'อัปโหลด DJI.mp4 แล้ว',redirect_to:'/sources?message=success'};first.status=200;first.fire('load');
assert.equal(navigations.at(-1),'http://127.0.0.1:8000/sources?message=success');
assert(text(upload,'title').includes('สำเร็จ'));assert(!upload.form.hasAttribute('data-action-busy'));
const afterSuccess=event(null);for(const handler of globalEvents.beforeunload)handler(afterSuccess);
assert(!afterSuccess.defaultPrevented,'Successful navigation does not show an interruption prompt');

const cancelled=form();submit(cancelled);const interrupted=requests.at(-1);
display(cancelled).querySelector('[data-request-cancel]').onclick();
assert(interrupted.aborted);assert(text(cancelled,'title').includes('ยกเลิก'));
assert(!cancelled.input.disabled && !cancelled.button.disabled);assert(cancelled.form.fields[1][1].name==='DJI.mp4');
interrupted.upload.fire('progress',{lengthComputable:true,loaded:1,total:2});
assert(text(cancelled,'title').includes('ยกเลิก'),'Late events do not replace the cancellation notice');
submit(cancelled);assert.notEqual(requests.at(-1),interrupted,'User can retry with the existing selected file');
requests.at(-1).fire('error');assert(text(cancelled,'title').includes('การเชื่อมต่อขาด'));

const invalid=form('rename_source');submit(invalid);const failed=requests.at(-1);
failed.response={ok:false,message:'มี source ชื่อนี้แล้ว'};failed.status=400;failed.fire('load');
assert.equal(text(invalid,'detail'),'มี source ชื่อนี้แล้ว');assert(!invalid.button.disabled);
assert.equal(display(invalid).attributes.role,'alert');

const named=form('ignored');named.button.name='action';named.button.value='reprocess_job';
submit(named);assert.equal(requests.at(-1).payload.get('action'),'reprocess_job','Preserve the clicked external action');
requests.at(-1).fire('error');
const blocked=form('generate');const before=requests.length;
submit(blocked,e=>{blocked.button.disabled=true;e.preventDefault();});
assert.equal(requests.length,before,'Capture validation can block submission');assert(!blocked.button.disabled);

const pose=form('generate3d');submit(pose,()=>{pose.button.disabled=true;});
requests.at(-1).status=400;requests.at(-1).response={ok:false,message:'job นี้กำลังทำงาน'};requests.at(-1).fire('load');
assert(!pose.button.disabled,'A button disabled by a target listener is restored after an error');
console.log('Action feedback passed: external buttons, duplicate submit, progress, save stage, modal/navigation guards, cancel, retry, errors and capture validation.');
