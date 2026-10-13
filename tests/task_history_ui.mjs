// Exercise the inline UI script with a simulated DOM; no browser rendering.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const script = JSON.parse(execFileSync(path.join(root, '.venv-win', 'Scripts', 'python.exe'),
  ['-c', 'import json, web_app; print(json.dumps(web_app.APP_SCRIPT))'],
  {cwd: root, encoding:'utf8', env:{...process.env, PYTHONUTF8:'1'}}));

class Element {
  constructor() {this.children=[]; this.style={}; this.dataset={}; this.nodes={}; this._text='';}
  set textContent(value) {this._text=String(value); this.children=[];}
  get textContent() {return this._text + this.children.map(child => child.textContent).join('');}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this._text=''; this.children=[...children];}
  querySelector(selector) {return this.nodes[selector] || null;}
  querySelectorAll(selector) {return this.nodes[selector] || [];}
}
const card = new Element(), history = new Element();
card.dataset.job='job'; history.dataset={job:'job', modelRun:'20261007_213000_ab12cd34'};
for (const node of [card,history]) node.nodes['.status-slot']=new Element();
card.nodes['.views-timing-slot']=new Element();
const button=new Element(); card.nodes['[data-create-model]']=[button];
history.nodes['[data-create-model]']=[];
history.nodes['.run-artifacts']=new Element();
const captured=[];
const started='2026-10-07T14:30:00+00:00';
let jobStatus={state:'running', stage:'model', step:'Training', started_at:started, elapsed_seconds:40,
  progress:40, busy:true, views:{state:'ready', started_at:started, finished_at:'2026-10-07T14:30:20Z',elapsed_seconds:20}};
let runStatus={state:'done', stage:'model', step:'complete', started_at:started, finished_at:'2026-10-07T14:31:00Z',
  elapsed_seconds:60, model:'job new.ply'};
const document={
  querySelectorAll(selector) {return selector==='[data-job]' ? [card,history] : [];},
  querySelector(selector) {return selector==='form[data-action-busy]' ? document.busyForm || null : null;},
  createElement() {return new Element();},
  createTextNode(text) {const node=new Element(); node.textContent=text; return node;},
};
class FixedDate extends Date {static now() {return Date.parse('2026-10-07T14:32:00Z');}}
let reloads=0;
const context=vm.createContext({document, Intl, URL, URLSearchParams, Date:FixedDate, console, setInterval(){},window:{location:{reload(){reloads++;}}},
  async fetch(url) {captured.push(url); return {ok:true, async json(){return url.startsWith('/api/run/') ? runStatus : jobStatus;}}},
});
vm.runInContext(script,context);
assert.equal(vm.runInContext('formatDuration(3661)',context),'1 ชม. 1 นาที 1 วินาที');
await vm.runInContext('refreshJobs()',context);
assert.equal(button.disabled,true);
assert.ok(card.nodes['.status-slot'].textContent.includes('2 นาที 0 วินาที'));
assert.ok(card.nodes['.status-slot'].textContent.includes('21:30:00'));
assert.ok(card.nodes['.views-timing-slot'].textContent.includes('20 วินาที'));
assert.equal(captured[1],'/api/run/job/20261007_213000_ab12cd34');
assert.equal(history.nodes['.run-artifacts'].children[0].href,'/model/job%20new.ply');
assert.equal(history.nodes['.run-artifacts'].children[1].download,'');
const etaSlot = new Element(); context.etaSlot = etaSlot;
vm.runInContext("renderTiming(etaSlot,{state:'running',started_at:'2026-10-07T14:30:00Z',estimate_state:'available',eta_seconds:65,estimated_finish_at:'2026-10-07T14:33:05Z'},'สร้าง Model')",context);
assert.ok(etaSlot.textContent.includes('เหลือประมาณ 1 นาที 5 วินาที'));
assert.ok(etaSlot.textContent.includes('21:33:05'));
vm.runInContext("renderTiming(etaSlot,{state:'done',started_at:'2026-10-07T14:30:00Z',finished_at:'2026-10-07T14:31:00Z',eta_seconds:65,estimated_finish_at:'2026-10-07T14:33:05Z'},'สร้าง Model')",context);
assert.ok(!etaSlot.textContent.includes('เหลือประมาณ'),'Completed tasks hide previous estimates');
vm.runInContext("renderTiming(etaSlot,{state:'running',started_at:'2026-10-07T14:30:00Z',estimate_state:'stale'},'สร้าง Model')",context);
assert.ok(etaSlot.textContent.includes('รอความคืบหน้าใหม่'));
jobStatus={...jobStatus, state:'done', finished_at:'2026-10-07T14:31:00Z', elapsed_seconds:60,busy:false};
runStatus={...runStatus,state:'deleted'};
await vm.runInContext('refreshJobs()',context);
assert.equal(button.disabled,false);
assert.ok(card.nodes['.status-slot'].textContent.includes('1 นาที 0 วินาที'));
assert.equal(history.nodes['.run-artifacts'].children.length,0);
const legacy=new Element(); context.legacy=legacy;
vm.runInContext("renderTiming(legacy,{recorded_at:'2026-10-07T14:30:00Z'},'สร้าง Model')",context);
assert.ok(legacy.textContent.includes('ไม่มีระยะเวลาในงานเดิม'));
const errorSlot=new Element();context.errorSlot=errorSlot;
context.errorStatus={state:'error',step:'failed',error:'Command failed\nreader error <literal>'};
vm.runInContext('renderStatus(errorSlot,errorStatus)',context);
assert.ok(errorSlot.textContent.includes('เปิดรายละเอียดหรือ Log'));
const expanded=errorSlot.children.find(child=>child.className==='job-error').children[1];
expanded.open=true;
vm.runInContext('renderStatus(errorSlot,errorStatus)',context);
assert.equal(errorSlot.children.find(child=>child.className==='job-error').children[1],expanded);
assert.equal(expanded.open,true);
card.dataset.jobDetail='1';card.dataset.viewCount='1';card.dataset.viewRun='views-a';
jobStatus={...jobStatus,views:{...jobStatus.views,frames:4,run_id:'views-a'}};
await vm.runInContext('refreshJobs()',context);
assert.equal(reloads,1,'Reload a partially generated gallery when all views are ready');
document.busyForm={};
await vm.runInContext('refreshJobs()',context);
assert.equal(reloads,1,'Automatic gallery reload cannot interrupt an upload or form action');
document.busyForm=null;
card.dataset.viewCount='4';card.dataset.knownModelRun='model-a';jobStatus={...jobStatus,run_id:'model-b'};
await vm.runInContext('refreshJobs()',context);
assert.equal(reloads,2,'Reload model history when a new automatic run appears');
delete card.dataset.jobDetail;card.dataset.sourceHub='1';card.dataset.knownModel='old.ply';jobStatus={...jobStatus,model:'new.ply'};
await vm.runInContext('refreshJobs()',context);
assert.equal(reloads,3,'Refresh source-related models after training completes');
const previewCard = new Element(), viewImage = new Element(), runPreview = new Element(), modelImage = new Element();
viewImage.src = 'http://localhost/api/resource-preview?kind=views&key=job'; viewImage.dataset.viewRun = 'old-views';
previewCard.nodes['[data-preview-kind="views"] img'] = viewImage;
previewCard.nodes['[data-run-preview]'] = runPreview;
runPreview.href = '/job/job'; runPreview.dataset.previewKind = 'views';
runPreview.getAttribute = name => runPreview[name];
runPreview.nodes.img = modelImage; runPreview.nodes.span = new Element();
context.previewCard = previewCard;
vm.runInContext("updateResourcePreview(previewCard, {state:'done', model:'scene & new.ply', views:{state:'ready', run_id:'new-views'}})", context);
assert.equal(new URL(viewImage.src).searchParams.get('v'), 'new-views');
assert.equal(runPreview.href, '/model/scene%20%26%20new.ply');
assert.equal(runPreview.dataset.previewKind, 'model');
assert.equal(new URL(modelImage.src, 'http://localhost').searchParams.get('key'), 'scene & new.ply');
assert.equal(modelImage.hidden, false);
console.log('UI checks passed: timing, status, busy buttons, version links, deletion and automatic preview updates.');
context.computeSlot=new Element();
context.computeStatus={state:'running',stage:'views',progress:25,view_compute:{backend:'WGPU/D3D12',device:'RX 6700 XT',fallback_reasons:['Vulkan: device lost']}};
vm.runInContext('renderStatus(computeSlot,computeStatus)',context);
assert.ok(context.computeSlot.textContent.includes('WGPU/D3D12: RX 6700 XT'));
assert.ok(context.computeSlot.textContent.includes('Vulkan: device lost'));
