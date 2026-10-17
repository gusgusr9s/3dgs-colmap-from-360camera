import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const code = fs.readFileSync(new URL('../glb_export.mjs', import.meta.url), 'utf8');
function screen(initial) {
  const ids = ['glb-export','glb-status','glb-progress','glb-start','glb-ready','glb-details','glb-initial'];
  const nodes = Object.fromEntries(ids.map(id => [id, {textContent:'', hidden:false, disabled:false, dataset:{id:'a'.repeat(32)},
    removeAttribute(name) { delete this[name]; }}]));
  nodes['glb-initial'].textContent = JSON.stringify(initial);
  const document = {hidden:false, querySelector: selector => nodes[selector.slice(1)]};
  const timers = [], replies = []; let requests = 0;
  const context = vm.createContext({document, AbortSignal, setTimeout: callback => timers.push(callback),
    fetch: async () => { ++requests; const reply = replies.shift(); if (reply instanceof Error) throw reply; return {ok:true,json:async()=>reply}; }});
  vm.runInContext(code, context);
  return {nodes, document, timers, replies, requests:()=>requests, tick:async()=>{const next=timers.shift();assert.ok(next);await next();}};
}
const ui = screen({state:'queued'}), original = ui.nodes['glb-start'];
assert.equal(original.disabled, true);
assert.equal(ui.nodes['glb-ready'].hidden, true);
ui.replies.push({state:'running',progress:63}); await ui.tick();
assert.equal(ui.nodes['glb-progress'].value,63);
ui.replies.push(new Error('offline')); await ui.tick();
assert.equal(original.disabled,true);
assert.match(ui.nodes['glb-status'].textContent,/เชื่อมต่อ/);
ui.replies.push({state:'running',progress:0}); await ui.tick();
assert.equal(ui.nodes['glb-progress'].value,63,'transient partial progress file must not move backwards');
ui.document.hidden=true; const requests=ui.requests(); await ui.tick();
assert.equal(ui.requests(),requests,'hidden tabs pause polling'); ui.document.hidden=false;
ui.replies.push({state:'ready',bytes:236000000,splats:1000000,sh_degree:3}); await ui.tick();
assert.equal(ui.nodes['glb-start'],original,'polling preserves existing button and focus/hover');
assert.equal(ui.nodes['glb-ready'].hidden,false); assert.equal(ui.timers.length,0);
assert.match(ui.nodes['glb-details'].textContent,/SH 3/);
const failed=screen({state:'error',error:'conversion failed'});
assert.equal(failed.nodes['glb-start'].disabled,false);
assert.match(failed.nodes['glb-start'].textContent,/ลอง/);
assert.equal(failed.nodes['glb-status'].textContent,'conversion failed');
console.log('GLB export UI passed: progress, stable nodes, network retry, hidden tabs, ready download and conversion retry.');
