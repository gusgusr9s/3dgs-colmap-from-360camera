import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() { this.children = []; this.listeners = {}; this.style = {}; }
  append(node) { this.children.push(node); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  fire(name, value) { return this.listeners[name]({ target: { value } }); }
  setAttribute(name, value) { (this.attributes ||= {})[name] = value; }
  focus() { this.focused = true; }
}
const nodes = Object.fromEntries(['config','view','source','compare-source','render','overlay','previous-card','mask-card','previous','mask','metrics','wipe','quality-counter','quality-previous','quality-next','quality-details-empty','quality-split','quality-wipe','quality-details'].map(id => [id,new Element()]));
for (const id of ['quality-reference','quality-full','quality-fast','quality-zoom','quality-source-label','quality-image-status']) nodes[id] = new Element();
const tabs = ['quality-split','quality-wipe','quality-details'].map(id => {
  const element = new Element(); element.dataset = {qualityTab:id}; return element;
});
let keydown;
nodes.config.textContent = JSON.stringify([
  { name: 'a', source: '/source-a', raw_source: '/raw-a', viewer_url: '/model/a?review_frame=a', fast_available: true, render: '/render-a', mask: '/mask-a', previous: '/previous-a', metrics_url: '/metrics-a' },
  { name: 'b', source: '/source-b', render: '/render-b', metrics_url: '/metrics-b' },
  { name: 'broken', source: '/source-c', render: '/render-c', metrics_url: '/metrics-c' },
]);
const pending = [];
const context = vm.createContext({ AbortController, Map,
  document: { getElementById: id => nodes[id], createElement: () => new Element(), querySelectorAll: () => tabs, addEventListener: (name, handler) => { keydown = handler; } },
  fetch: (url, options) => new Promise(resolve => pending.push({url,options,resolve})),
});
vm.runInContext(fs.readFileSync(new URL('../quality_viewer.mjs',import.meta.url),'utf8'),context);
assert.equal(pending.length, 1, 'only the initial selected view computes a metric');
assert.equal(nodes.source.src, '/source-a'); assert.equal(nodes.mask.src, undefined, 'inactive panels do not request images');
assert.equal(nodes['quality-full'].href, '/model/a?review_frame=a&variant=full');
assert.equal(nodes['quality-fast'].href, '/model/a?review_frame=a&variant=fast');
nodes['quality-reference'].value = 'raw'; nodes['quality-reference'].fire('change');
assert.equal(nodes.source.src, '/raw-a'); assert.equal(pending.length,1,'changing reference never changes the metric target');
nodes.source.fire('error'); assert(nodes['quality-image-status'].textContent.includes('อ่านภาพอ้างอิงไม่ได้'));
nodes['quality-reference'].value = 'training'; nodes['quality-reference'].fire('change');
assert.equal(nodes['quality-counter'].textContent, '1 / 3');
assert(nodes['quality-previous'].disabled && !nodes['quality-next'].disabled);
const b = nodes.view.fire('change','1');
assert(pending[0].options.signal.aborted);
assert(nodes['previous-card'].hidden && nodes['mask-card'].hidden);
assert(nodes['quality-full'].hidden && nodes['quality-fast'].hidden,'unavailable models do not expose broken links');
pending[1].resolve({ ok: true, json: async () => ({ psnr: 28.123, previous_psnr: null }) }); await b;
assert(nodes.metrics.textContent.includes('28.12'));
pending[0].resolve({ ok: true, json: async () => ({ psnr: 18.5, previous_psnr: 17 }) });
await new Promise(resolve => setImmediate(resolve));
assert(nodes.metrics.textContent.includes('28.12'), 'a late response cannot overwrite the current view');
assert.equal(nodes.metrics.attributes['aria-busy'],'false');
await nodes.view.fire('change','0');
assert(nodes.metrics.textContent.includes('18.50') && nodes.metrics.textContent.includes('17.00'));
assert.equal(pending.length,2,'returning to a view reuses its metric');
tabs[2].fire('click'); assert.equal(nodes.mask.src,'/mask-a'); assert.equal(nodes.previous.src,'/previous-a');
assert(nodes['quality-split'].hidden && !nodes['quality-details'].hidden);
await nodes['quality-next'].fire('click');
assert(!nodes['quality-details-empty'].hidden,'missing baseline and mask have an explicit empty state');
assert.equal(nodes['quality-counter'].textContent,'2 / 3');
let prevented = false;
tabs[2].listeners.keydown({key:'ArrowLeft',preventDefault(){prevented=true;}});
assert(prevented && tabs[1].focused);
assert.equal(nodes['compare-source'].src,'/source-b'); assert.equal(nodes.overlay.src,'/render-b');
assert.equal(nodes['quality-counter'].textContent,'2 / 3','tab arrow keys do not change images');
keydown({key:'ArrowLeft', target:{closest:()=>true}, preventDefault(){throw new Error('Hijacked form key');}});
assert.equal(nodes.view.value,'1');
keydown({key:'ArrowLeft', target:{closest:()=>null}, preventDefault(){}});
assert.equal(nodes.view.value,'0');
keydown({key:'ArrowLeft', target:{closest:()=>null}, preventDefault(){}});
assert.equal(nodes.view.value,'0','navigation stops at the first image');
const bad = nodes.view.fire('change','2'); pending[2].resolve({ ok: false }); await bad;
assert(nodes.metrics.textContent.includes('ไม่ได้'));
assert(nodes['quality-next'].disabled);
const retry = nodes.view.fire('change','2'); pending[3].resolve({ ok: true, json: async () => ({ psnr: 22, previous_psnr: null }) }); await retry;
assert(nodes.metrics.textContent.includes('22.00'));
nodes.wipe.fire('input','75'); assert.equal(nodes.overlay.style.clipPath,'inset(0 25% 0 0)');
console.log('Quality viewer passed: tabs, deferred images, previous/next boundaries, keyboard guards, selected-view metrics, stale responses, cache, retry and wipe.');
