// Exercise the editor's real event handlers and Three transforms. DOM, GPU,
// network, and pointer lock are simulated; this does not claim browser GPU QA.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const core = fs.readFileSync(new URL('../assets/vendor/three-0.180.0/three.core.js', import.meta.url), 'utf8');
const three = await import('data:text/javascript;base64,' + Buffer.from(core).toString('base64'));
const template = fs.readFileSync(new URL('../app/web/templates/scene_editor.html', import.meta.url), 'utf8');
const collision = fs.readFileSync(new URL('../assets/app/scene_collision.mjs', import.meta.url), 'utf8').replace(/^export /gm, '');
const autoProxy = fs.readFileSync(new URL('../assets/app/scene_auto_proxy.mjs', import.meta.url), 'utf8').replace(/^export /gm, '');
const source = fs.readFileSync(new URL('../assets/app/scene_editor.mjs', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '')
  .replace("await import('@sparkjsdev/spark')", 'testSpark');
const copy = value => JSON.parse(JSON.stringify(value));
const near = (actual, expected, message = '') => assert.ok(Math.abs(actual - expected) < 1e-6, `${message} ${actual} != ${expected}`);

function baseScene() {
  return { id: 'test-map', name: 'Test map', revision: 1, version: 1, units: 'relative', objects: [], colliders: [],
    view: { position: [6, 4, 6], target: [0, 0, 0] }, spawn: { position: [0, 1.65, 0], yaw: 0 }, player: { height: 1.65, radius: .25, speed: 1.4 } };
}
const assets = [{ kind: 'ply', ref: 'room-a.ply', label: 'Room A', url: '/model-a.ply', bytes: 2000, world_up: [0, 1, 0] },
  { kind: 'spz', ref: 'room-b.spz', label: 'Room B', url: '/model-b.spz', bytes: 1000, world_up: [0, 0, 1] }];

async function createEditor(initial = baseScene()) {
  const nodes = new Map(); const nudge = []; let document;
  class Element {
    constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.attributes = {}; this.className = ''; this.value = ''; this.textContent = ''; this.disabled = false; this.hidden = false; this.checked = false; this.dataset = {}; this.clientWidth = 1000; this.clientHeight = 700;
      this.classList = { contains: value => this.className.split(/\s+/).includes(value), toggle: (value, force) => { const values = new Set(this.className.split(/\s+/).filter(Boolean)); const add = force === undefined ? !values.has(value) : force; if (add) values.add(value); else values.delete(value); this.className = [...values].join(' '); return add; } };
    }
    set id(value) { this._id = value; nodes.set(`#${value}`, this); } get id() { return this._id; }
    append(...children) { this.children.push(...children); } replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this.attributes[key] = value; } getAttribute(key) { return this.attributes[key]; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    async fire(type, event = {}) { event.target ||= this; event.preventDefault ||= () => { event.defaultPrevented = true; }; await Promise.all((this.listeners[type] || []).map(callback => callback(event))); return event; }
    click() { if (!this.disabled) return this.fire('click'); }
    focus() { const previous = document.activeElement; document.activeElement = this; if (previous !== this) previous?.fire('blur'); }
    setPointerCapture() {} getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 700 }; }
  }
  for (const match of template.matchAll(/<([a-z][a-z\d-]*)\b([^>]*?)\bid="([^"]+)"([^>]*?)>/gi)) {
    const node = new Element(match[1]); node.id = match[3]; const attrs = match[2] + match[4];
    node.value = /\bvalue="([^"]*)"/.exec(attrs)?.[1] || ''; node.checked = /\bchecked\b/.test(attrs); node.disabled = /\bdisabled\b/.test(attrs); node.hidden = /\bhidden\b/.test(attrs);
  }
  // Prevent automatic startup; instantiate the class below to retain control.
  nodes.delete('#scene-config');
  for (const match of template.matchAll(/data-nudge="([^"]+)"/g)) { const node = new Element('button'); node.dataset.nudge = match[1]; nudge.push(node); }
  document = new Element(); document.body = new Element('body'); document.activeElement = null; document.hidden = false; document.hasFocus = () => true;
  document.querySelector = selector => nodes.get(selector) || null; document.querySelectorAll = selector => selector === '[data-nudge]' ? nudge : [];
  document.createElement = tag => new Element(tag); document.exitPointerLock = () => { document.pointerLockElement = null; document.fire('pointerlockchange'); };
  const window = new Element(); const meshInstances = []; let nextLoad = null;
  class Controls { constructor(camera) { this.camera = camera; this.target = new three.Vector3(); this.updates = 0; } update() { this.updates++; this.camera.lookAt(this.target); } dispose() { this.disposed = true; } }
  class TransformControls extends three.EventDispatcher {
    constructor() { super(); this.helper = new three.Object3D(); this._dragging = false; this.axis = null; this.size = 1; }
    get dragging() { return this._dragging; }
    set dragging(value) { if (value === this._dragging) return; this._dragging = value; this.dispatchEvent({ type: 'dragging-changed', value }); }
    getHelper() { return this.helper; } setSize(value) { this.size = value; }
    attach(object) { this.object = object; return this; } detach() { this.object = undefined; this.axis = null; return this; }
    setMode(mode) { this.mode = mode; } setSpace(space) { this.space = space; }
  }
  class Renderer { setPixelRatio() {} setSize() {} render() {} setAnimationLoop(callback) { this.frame = callback; } }
  class SparkRenderer extends three.Object3D { constructor(options) { super(); this.options = options; } }
  class SplatMesh extends three.Object3D {
    constructor(options) { super(); this.options = options; this.numSplats = 100; this.initialized = nextLoad || Promise.resolve(); nextLoad = null; meshInstances.push(this); }
    getBoundingBox() { return new three.Box3(new three.Vector3(-1, 0, -1), new three.Vector3(1, 2, 1)); }
    dispose() { this.disposed = true; }
  }
  let stored = copy(initial), postOverride = null; const requests = [];
  const fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    if (url === '/api/scene-assets') return { ok: true, json: async () => ({ assets: copy(assets) }) };
    if (options.method === 'POST') {
      if (postOverride) return postOverride(url, options);
      const body = JSON.parse(options.body); assert.equal(body.revision, stored.revision); stored = { ...body, revision: stored.revision + 1 };
    }
    return { ok: true, json: async () => copy(stored) };
  };
  let sequence = 0;
  const context = vm.createContext({ THREE: { ...three, WebGLRenderer: Renderer }, OrbitControls: Controls, TransformControls, testSpark: { SparkRenderer, SplatMesh },
    document, window, console, devicePixelRatio: 1, crypto: { randomUUID: () => `object-${++sequence}` }, fetch,
    addEventListener: window.addEventListener.bind(window), ResizeObserver: class { observe() {} }, Blob, URL, setTimeout });
  vm.runInContext(collision, context); vm.runInContext(autoProxy, context); vm.runInContext(source + '\nglobalThis.Editor = SceneEditor; globalThis.boundsFromMesh = sceneModelBounds;', context);
  const editor = new context.Editor({ id: 'test-map' }); await editor.start();
  return { editor, nodes, nudge, document, window, meshInstances, requests, boundsFromMesh: context.boundsFromMesh, getStored: () => copy(stored), setPost: callback => { postOverride = callback; }, deferLoad: promise => { nextLoad = promise; } };
}

const ui = await createEditor(); const { editor, nodes, document } = ui; const node = name => nodes.get(`#${name}`);
const recoveredBounds = ui.boundsFromMesh({ getBoundingBox: () => new three.Box3(), splats: {lodSplats: {
  forEachSplat: callback => { callback(0,new three.Vector3(-2,0,-3)); callback(1,new three.Vector3(4,5,6)); },
}} });
assert.deepEqual(recoveredBounds.min.toArray(),[-2,0,-3]);
assert.deepEqual(recoveredBounds.max.toArray(),[4,5,6]);
assert.equal(ui.boundsFromMesh({getBoundingBox:()=>new three.Box3()}).isEmpty(),true,'empty sources remain empty');
const settle = async () => { await new Promise(resolve => setImmediate(resolve)); };
const change = async (name, value) => { node(name).value = String(value); await node(name).fire('change'); await settle(); };
assert.equal(editor.ready, true); assert.equal(editor.doc.objects.length, 0); assert.equal(node('scene-save').disabled, false);
assert.equal(editor.spark.options.enableLod, true);

// The actual checkbox and Add handlers must add both sources in one undo step.
const assetRows = node('asset-list').children;
for (const row of assetRows) { const checkbox = row.children[0]; checkbox.checked = true; await checkbox.fire('change'); }
assert.equal(node('assets-add').disabled, false); const previousCamera = editor.camera.position.clone();
await node('assets-add').click(); await settle();
assert.equal(editor.doc.objects.length, 2); assert.equal(editor.history.past.length, 1);
assert.equal(editor.runtime.size, 2); assert.equal(ui.meshInstances.length, 2); assert.equal(ui.meshInstances[0].options.lod, true);
assert.ok(editor.camera.position.distanceTo(previousCamera) > .1, 'first add auto-fits despite backend default view');
const [a, b] = editor.doc.objects;
const normalizedUp = new three.Vector3(0, 0, 1).applyEuler(new three.Euler(...b.rotation)); near(normalizedUp.y, 1, 'source world-up becomes Y');
assert.equal(editor.chosenAssets.size, 0);

// Numerical transforms affect the actual Three object and persist radians.
await change('position-0', 6); near(editor.runtime.get(b.id).group.position.x, 6);
await change('rotation-1', 90); near(editor.doc.objects[1].rotation[1], Math.PI / 2);
await change('item-scale', 2); near(editor.runtime.get(b.id).group.scale.x, 2);
await change('item-scale', 0); near(editor.doc.objects[1].scale, 2, 'invalid zero scale is rejected');
assert.equal(node('item-scale').getAttribute('aria-invalid'), 'true');
await node('scene-undo').click(); near(editor.doc.objects[1].scale, 1);
await node('scene-redo').click(); near(editor.doc.objects[1].scale, 2);
await change('item-scale', 2);
const xBeforeNudge = editor.item().position[0]; await ui.nudge[1].click(); near(editor.item().position[0], xBeforeNudge + .1);
await node('item-duplicate').click(); await settle(); assert.equal(editor.doc.objects.length, 3); assert.notEqual(editor.selected, b.id);
await node('item-remove').click(); await settle(); assert.equal(editor.doc.objects.length, 2);

// Hide releases the decoded mesh and show reloads it; local source stays intact.
editor.fit(a.id); editor.camera.updateMatrixWorld(true); editor.select(null);
await editor.canvas.fire('pointerdown', { button: 0, clientX: 500, clientY: 350 }); await editor.canvas.fire('pointerup', { button: 0, clientX: 500, clientY: 350 });
assert.equal(editor.selected, a.id, 'viewport click chooses the nearest visible bounding box');
await editor.canvas.fire('pointerdown', { button: 0, clientX: 500, clientY: 350 }); await editor.canvas.fire('pointerup', { button: 0, clientX: 600, clientY: 350 });
assert.equal(editor.selected, a.id, 'orbit drag does not change selection');
const originalMesh = editor.runtime.get(a.id).mesh; node('item-visible').checked = false; await node('item-visible').fire('change');
assert.equal(originalMesh.disposed, true); assert.equal(editor.runtime.has(a.id), false); assert.equal(editor.doc.objects.length, 2);
node('item-visible').checked = true; await node('item-visible').fire('change'); await settle(); assert.ok(editor.runtime.get(a.id).mesh);

// Generating an approximate room uses transformed bounds and one undo step.
editor.select(a.id); const proxyStart = editor.doc.colliders.length;
await node('auto-collision').click();
assert.equal(editor.doc.colliders.length, proxyStart + 5);
assert.equal(editor.doc.colliders.filter(item => item.kind === 'floor').length, 1);
near(editor.doc.spawn.position[1], editor.doc.player.height);
await node('scene-undo').click(); assert.equal(editor.doc.colliders.length, proxyStart);
await node('scene-redo').click(); assert.equal(editor.doc.colliders.length, proxyStart + 5);
await node('scene-undo').click();

// Floor remains at Y0 after fitting; first floor sets a usable spawn above it.
editor.controls.target.set(0, 12, 0); await node('add-floor').click();
assert.equal(editor.doc.colliders.length, 1); near(editor.doc.colliders[0].position[1], -.1); near(editor.doc.spawn.position[1], editor.doc.player.height);
await node('add-wall').click(); await change('position-2', -1); await change('rotation-1', 35); near(editor.doc.colliders[1].yaw, 35 * Math.PI / 180);
await change('size-0', 6); near(editor.doc.colliders[1].size[0], 6); assert.equal(node('rotation-0').disabled, true);
await change('player-radius', 51); near(editor.doc.player.radius, .25, 'backend maximum also enforced by handler');

// Walk starts with spawn yaw even on a zero-dt frame, lands, and stops at a wall.
await change('rotation-1', 0); editor.doc.spawn.yaw = .7; await node('walk-toggle').click();
const beforeZeroFrame = editor.camera.quaternion.clone(); editor.frame(100); near(editor.camera.quaternion.angleTo(beforeZeroFrame), 0, 'zero-dt frame preserves spawn view');
assert.equal(document.activeElement, editor.canvas); assert.equal(node('assets-add').disabled, true);
editor.walkYaw = 0; editor.applyLook(); await ui.window.fire('keydown', { code: 'KeyW', target: editor.canvas });
for (let time = 150; time < 2150; time += 50) editor.frame(time);
near(editor.camera.position.y, 1.65, 'gravity keeps eye on floor'); assert.ok(editor.camera.position.z > -.66, 'wall blocks the eye cylinder');
await ui.window.fire('keyup', { code: 'KeyW' });
// Inputs, other focus, hidden tabs, and window blur cannot keep walking.
editor.canvas.focus(); await ui.window.fire('keydown', { code: 'KeyW', target: editor.canvas }); node('nudge-step').focus(); assert.equal(editor.keys.size, 0);
await ui.window.fire('keydown', { code: 'KeyW', target: node('nudge-step') }); assert.equal(editor.keys.size, 0);
await ui.window.fire('keydown', { code: 'KeyW', target: document.body }); assert.equal(editor.keys.size, 0);
editor.canvas.focus(); await ui.window.fire('keydown', { code: 'KeyW', target: editor.canvas }); const paused = editor.camera.position.clone();
await ui.window.fire('blur'); editor.frame(2200); editor.frame(2250); near(editor.camera.position.distanceTo(paused), 0, 'window blur pauses gravity and movement');
await ui.window.fire('focus'); document.hidden = true; await document.fire('visibilitychange'); editor.frame(2300); editor.frame(2350); near(editor.camera.position.distanceTo(paused), 0, 'hidden tab pauses walk');
document.hidden = false; await ui.window.fire('keydown', { code: 'Escape', target: editor.canvas }); assert.equal(editor.walking, false);

// Saved scene round-trips without baking or losing either asset transform.
await node('scene-save').click(); assert.equal(editor.dirty, false); assert.equal(editor.revision, 2);
const saved = ui.getStored(); assert.equal(saved.objects.length, 2); assert.equal(saved.colliders.length, 2); near(saved.objects[1].position[0], xBeforeNudge + .1);
const reopened = await createEditor(saved); await settle(); assert.deepEqual(copy(reopened.editor.doc.objects), saved.objects); assert.equal(reopened.editor.runtime.size, 2);

// A pointer drag is one document transaction, even with many objectChange events.
const gizmoUI = await createEditor(saved); await settle(); const gizmo = gizmoUI.editor;
const gizmoNode = name => gizmoUI.nodes.get(`#${name}`);
const press = (code, extra = {}) => gizmoUI.window.fire('keydown', { code, target: gizmo.canvas, ...extra });
gizmo.select(b.id); assert.equal(gizmo.transform.object, gizmo.runtime.get(b.id).group);
const dragBefore = copy(gizmo.item()), dragHistory = gizmo.history.past.length, oldOrbit = gizmo.controls;
gizmo.transform.axis = 'X'; gizmo.transform.dragging = true;
assert.equal(oldOrbit.disposed, true, 'drag discards pending Orbit damping'); assert.equal(gizmo.controls.enabled, false);
const group = gizmo.transform.object; group.position.x += 1; gizmo.transform.dispatchEvent({ type: 'objectChange' });
group.position.x += 2; gizmo.transform.dispatchEvent({ type: 'objectChange' });
assert.equal(gizmo.history.past.length, dragHistory, 'intermediate pointer moves do not create undo entries');
assert.equal(gizmoNode('scene-save').disabled, true); assert.equal(await gizmo.save(), false, 'partial drags cannot be saved');
gizmo.transform.dragging = false; assert.equal(gizmo.controls.enabled, true); assert.equal(gizmo.history.past.length, dragHistory + 1);
near(gizmo.item().position[0], dragBefore.position[0] + 3); gizmo.undo(); assert.deepEqual(copy(gizmo.item()), dragBefore);
gizmo.redo(); near(gizmo.item().position[0], dragBefore.position[0] + 3);
await gizmo.save(); near(gizmoUI.getStored().objects[1].position[0], dragBefore.position[0] + 3);

// Gizmo pointer events never select another object behind the handle.
gizmo.transform.axis = 'X'; let picks = 0; const originalPick = gizmo.pick; gizmo.pick = () => { picks++; };
await gizmo.canvas.fire('pointerdown', { button: 0, clientX: 500, clientY: 350 });
await gizmo.canvas.fire('pointerup', { button: 0, clientX: 500, clientY: 350 });
assert.equal(picks, 0); gizmo.pick = originalPick;

// Escape restores the exact start transform and clean state without adding history.
const cancelBefore = copy(gizmo.item()), cancelHistory = gizmo.history.past.length, cancelCamera = gizmo.camera.position.clone();
gizmo.transform.dragging = true; gizmo.transform.object.position.y += 7; gizmo.transform.dispatchEvent({ type: 'objectChange' });
assert.equal(gizmo.dirty, true); await press('Escape');
assert.deepEqual(copy(gizmo.item()), cancelBefore); assert.equal(gizmo.history.past.length, cancelHistory); assert.equal(gizmo.dirty, false);
assert.equal(gizmo.transform.dragging, false); assert.equal(gizmo.controls.enabled, true);
gizmo.frame(2500); gizmo.frame(2550); near(gizmo.camera.position.distanceTo(cancelCamera), 0, 'cancelled drag cannot leave camera momentum');

// Keyboard axes constrain the visible handles; scale stays uniform on splats.
await press('KeyE'); assert.equal(gizmo.transform.mode, 'rotate');
await press('KeyX'); assert.equal(gizmo.transform.showX, true); assert.equal(gizmo.transform.showY, false); assert.equal(gizmo.transform.showZ, false);
await press('KeyX'); assert.equal(gizmo.transformAxis, null); assert.equal(gizmo.transform.showZ, true);
await press('KeyY'); await press('KeyR'); assert.equal(gizmo.transform.mode, 'scale'); assert.equal(gizmo.transform.space, 'local');
gizmo.transform.axis = 'Y'; gizmo.transform.dragging = true; const oldScale = gizmo.item().scale;
gizmo.transform.object.scale.y = oldScale * 1.5; gizmo.transform.dispatchEvent({ type: 'objectChange' }); gizmo.transform.dragging = false;
near(gizmo.item().scale, oldScale * 1.5); for (const value of gizmo.transform.object.scale.toArray()) near(value, oldScale * 1.5, 'all splat axes scale together');
await press('KeyS', { altKey: true }); near(gizmo.item().scale, 1);
await gizmoNode('transform-space').click(); assert.equal(gizmo.transformSpace, 'world', 'space switch is disabled for scale');
await press('KeyW'); await gizmoNode('transform-space').click(); assert.equal(gizmo.transform.space, 'local');
await press('Escape'); assert.equal(gizmo.transformAxis, null);

// Reset individual axes and whole vectors; typing and native input shortcuts stay safe.
gizmo.change(() => { gizmo.item().position = [3, 4, 5]; gizmo.item().rotation = [.2, .4, .6]; });
await gizmoNode('position-reset-0').click(); assert.deepEqual(copy(gizmo.item().position), [0, 4, 5]);
await gizmoNode('rotation-reset-2').click(); assert.deepEqual(copy(gizmo.item().rotation), [.2, .4, 0]);
await press('KeyG', { altKey: true, target: gizmoNode('item-name') }); assert.deepEqual(copy(gizmo.item().position), [0, 4, 5]);
await press('KeyE', { target: gizmoNode('position-0') }); assert.equal(gizmo.transformMode, 'translate');
await press('KeyG', { altKey: true }); assert.deepEqual(copy(gizmo.item().position), [0, 0, 0]);
await press('KeyR', { altKey: true }); assert.deepEqual(copy(gizmo.item().rotation), [0, 0, 0]);
gizmo.undo(); assert.deepEqual(copy(gizmo.item().rotation), [.2, .4, 0]);
await gizmoNode('item-reset-all').click(); assert.deepEqual(copy(gizmo.item().position), [0, 0, 0]); assert.deepEqual(copy(gizmo.item().rotation), [0, 0, 0]); near(gizmo.item().scale, 1);

// Collider rotation is yaw-only, including yaw beyond 90 degrees; scale edits box dimensions.
gizmo.select(saved.colliders[1].id); await press('KeyE');
assert.equal(gizmo.transform.showX, false); assert.equal(gizmo.transform.showY, true); assert.equal(gizmo.transform.showZ, false);
await press('KeyX'); assert.equal(gizmo.transformAxis, null); assert.equal(gizmoNode('transform-axis-x').disabled, true);
gizmo.transform.axis = 'Y'; gizmo.transform.dragging = true; gizmo.transform.object.rotation.set(0, 2.4, 0); gizmo.transform.dispatchEvent({ type: 'objectChange' }); gizmo.transform.dragging = false;
near(gizmo.item().yaw, 2.4); near(gizmo.transform.object.rotation.x, 0); near(gizmo.transform.object.rotation.z, 0);
await press('KeyR'); gizmo.transform.axis = 'X'; gizmo.transform.dragging = true;
gizmo.transform.object.scale.x = 12; gizmo.transform.dispatchEvent({ type: 'objectChange' }); gizmo.transform.dragging = false;
near(gizmo.item().size[0], 12); assert.equal(gizmo.item().scale, undefined);
await press('KeyS', { altKey: true }); assert.deepEqual(copy(gizmo.item().size), [5, 3, .2]);

// Home and the button restore saved view through a fresh Orbit controller.
gizmo.camera.position.set(100, 50, -30); gizmo.controls.target.set(5, 6, 7); const beforeHome = gizmo.controls;
await press('Home', { target: gizmoNode('position-0') }); near(gizmo.camera.position.x, 100, 'Home while typing retains native field behavior');
await press('Home'); assert.equal(beforeHome.disposed, true); assert.deepEqual(gizmo.camera.position.toArray(), saved.view.position); assert.deepEqual(gizmo.controls.target.toArray(), saved.view.target);
gizmo.camera.position.x += 30; await gizmoNode('reset-view').click(); assert.deepEqual(gizmo.camera.position.toArray(), saved.view.position);
gizmo.setWalking(true); assert.equal(gizmo.transform.object, undefined, 'walk detaches the gizmo');
await press('Escape'); assert.equal(gizmo.transform.object, gizmo.colliderRuntime.get(gizmo.selected));

// Undo after save must retain the latest server revision for the next save.
await node('scene-undo').click(); assert.equal(editor.revision, 2); await node('scene-save').click(); assert.equal(editor.revision, 3);

// Edits arriving while a save is pending stay dirty and are not overwritten.
let resolveSave; ui.setPost(async (url, options) => new Promise(resolve => { resolveSave = () => resolve({ ok: true, json: async () => ({ ...JSON.parse(options.body), revision: 4 }) }); }));
const saving = node('scene-save').click(); await settle(); await change('scene-name', 'Edited during save'); resolveSave(); await saving;
assert.equal(editor.revision, 4); assert.equal(editor.doc.name, 'Edited during save'); assert.equal(editor.dirty, true);
// A conflict cannot destroy this tab's scene or silently clear its dirty state.
ui.setPost(async () => ({ ok: false, status: 409 })); await node('scene-save').click();
assert.equal(editor.doc.name, 'Edited during save'); assert.equal(editor.dirty, true); assert.equal(editor.revision, 4); assert.match(node('scene-error').textContent, /JSON/);

// Cancel a pending decode by deleting it; the late result must be disposed.
const race = await createEditor(); let finishDecode; race.deferLoad(new Promise(resolve => { finishDecode = resolve; }));
const raceRows = race.nodes.get('#asset-list').children; raceRows[0].children[0].checked = true; await raceRows[0].children[0].fire('change'); await race.nodes.get('#assets-add').click();
await race.nodes.get('#item-remove').click(); finishDecode(); await settle(); assert.equal(race.editor.doc.objects.length, 0); assert.equal(race.editor.runtime.size, 0); assert.equal(race.meshInstances[0].disposed, true);

// Source or scene names are rendered as text, never HTML.
assert.equal(/innerHTML/.test(source), false);
console.log('scene_editor_ui: multi-add, transforms, gizmo drag/cancel/history, uniform scale, yaw-only boxes, focus-safe shortcuts, axis resets, camera reset, walk, save/reopen and races passed');
