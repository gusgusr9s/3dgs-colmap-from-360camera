// Exercise the viewer's actual event handlers with real Three camera math.
// Rendering and the browser's pointer-lock API are simulated; no GPU claim.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const core = fs.readFileSync(new URL('../assets/vendor/three-0.180.0/three.core.js', import.meta.url), 'utf8');
const THREE = await import('data:text/javascript;base64,' + Buffer.from(core).toString('base64'));
const navigation = fs.readFileSync(new URL('../model_navigation.mjs', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const gamepad = fs.readFileSync(new URL('../model_gamepad.mjs', import.meta.url), 'utf8').replace(/^export /gm, '');
const minimap = fs.readFileSync(new URL('../model_minimap.mjs', import.meta.url), 'utf8').replace(/^export /gm, '');
const viewer = fs.readFileSync(new URL('../model_viewer.mjs', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/\nstart\(\);\s*$/, '\n');

async function createViewer(poses, options = {}) {
  let pads = [];
  let document;
  class Element {
    constructor(tagName = 'DIV') {
      this.tagName = tagName; this.listeners = {}; this.attributes = {}; this.style = {};
      this.value = ''; this.hidden = false; this.disabled = false; this.textContent = '';
    }
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
    fire(type, event = {}) {
      event.target ||= this;
      event.preventDefault ||= () => { event.defaultPrevented = true; };
      for (const handler of this.listeners[type] || []) handler(event);
      return event;
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    getBoundingClientRect() { return { left: 0, top: 0, width: 260, height: 170 }; }
    setPointerCapture() {}
    focus() {
      const previous = document.activeElement;
      document.activeElement = this;
      if (previous !== this) previous?.fire('blur');
    }
    querySelector() { return new Element('OPTION'); }
    showModal() { this.open = true; }
    close() { this.open = false; }
  }
  const nodes = {};
  for (const name of ['model-config', 'c', 'viewer', 'status', 'error', 'navigation-mode', 'capture-position', 'capture-label', 'capture-image', 'help', 'walk-start', 'free-view', 'movement-controls', 'pointer-lock', 'walk-height-control', 'walk-height', 'walk-height-value', 'movement-speed', 'renderer-mode', 'capture-prev', 'capture-next', 'gamepad-toggle', 'gamepad-status']) nodes['#' + name] = new Element();
  for (const name of ['model-config-open','model-config-close','model-config-dialog']) nodes['#'+name]=new Element();
  for (const name of ['mini-map', 'mini-map-toggle', 'mini-map-svg', 'mini-map-viewer', 'mini-map-selected', 'mini-map-label', 'mini-map-hint', 'mini-map-route', 'mini-map-points', 'mini-map-scale', 'mini-map-scale-label']) nodes['#'+name]=new Element();
  for (const name of ['navigation-mode', 'movement-speed', 'renderer-mode']) nodes['#' + name].tagName = 'SELECT';
  nodes['#walk-height'].tagName = 'INPUT';
  nodes['#c'].tagName = 'CANVAS';
  nodes['#navigation-mode'].value = 'capture';
  nodes['#walk-height'].value = '0';
  nodes['#movement-speed'].value = '1';
  nodes['#model-config'].textContent = JSON.stringify({ renderer: 'spark', url: '/test.ply', cameras_url: '/cameras', initial_view: {} });
  nodes['#viewer'].clientWidth = 960; nodes['#viewer'].clientHeight = 540;
  document = new Element();
  document.querySelector = selector => nodes[selector] || null;
  document.pointerLockElement = null;
  document.exitPointerLock = () => { document.pointerLockElement = null; document.fire('pointerlockchange'); };
  nodes['#c'].requestPointerLock = () => { document.pointerLockElement = nodes['#c']; document.fire('pointerlockchange'); };
  class Renderer {
    setPixelRatio() {} setSize() {} render() {}
    setAnimationLoop(callback) { this.frame = callback; }
  }
  class Controls {
    constructor(camera) { this.camera = camera; this.target = new THREE.Vector3(); }
    dispose() { this.disposed = true; }
    update() { this.camera.lookAt(this.target); }
  }
  const window = new Element();
  const context = vm.createContext({ THREE, OrbitControls: Controls, document, console, devicePixelRatio: 1,
    navigator: options.unsupported ? {} : { getGamepads: () => { if (options.blocked) throw new Error('SecurityError'); return pads; } },
    location: { href: 'http://localhost/model/test.ply' }, URL, addEventListener: window.addEventListener.bind(window),
    fetch: async () => ({ ok: true, json: async () => poses }),
    result: { count: 10, label: 'Test', bounds: new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1)) },
    testRenderer: new Renderer() });
  vm.runInContext(navigation, context);
  vm.runInContext(gamepad, context);
  vm.runInContext(minimap, context);
  vm.runInContext(viewer, context);
  vm.runInContext('loadModel = async () => { renderer = testRenderer; return result; }', context);
  await vm.runInContext('start()', context);
  const state = () => vm.runInContext('({ camera, controls, movement, keys, ready, miniMap, index })', context);
  const key = (code, type = 'keydown', target = nodes['#c']) => document.fire(type, { code, target });
  return { nodes, document, window, state, key, frame: time => context.testRenderer.frame(time), gamepads: values => { pads = values; } };
}

const rotation = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI);
const captures = [0, 1].map(index => {
  const matrix = new THREE.Matrix4().compose(new THREE.Vector3(index, 2, 3), rotation, new THREE.Vector3(1, 1, 1));
  return { transform_matrix: [0, 1, 2, 3].map(row => [0, 1, 2, 3].map(column => matrix.elements[column * 4 + row])), frame_index: index, fov_x: 90, w: 640, h: 640, image_url: `/image${index}.jpg` };
});
const ui = await createViewer({ cameras: captures, path_span: 2, scene_radius: 10 });
const { nodes, document, state, key, frame } = ui;
assert.equal(state().ready, true);
assert.equal(nodes['#walk-start'].disabled, false);
assert.equal(nodes['#movement-controls'].hidden, true);
const orbitBefore = state().controls;
nodes['#walk-start'].fire('click');
assert.equal(orbitBefore.disposed, true, 'Mode changes discard cached Orbit inertia');
assert.equal(nodes['#navigation-mode'].value, 'walk');
assert.equal(state().controls.enabled, false);
assert.equal(nodes['#walk-height-control'].hidden, false);
assert.equal(document.pointerLockElement, nodes['#c']);
assert.match(nodes['#help'].textContent, /Esc/);
const walkingStart = state().camera.position.clone();
assert.equal(key('KeyW').defaultPrevented, true);
frame(1000); frame(1050);
assert.ok(Math.abs(state().camera.position.distanceTo(walkingStart) - .025) < 1e-10);
const beforeLook = state().camera.position.clone();
const beforeRotation = state().camera.quaternion.clone();
document.fire('mousemove', { movementX: 80, movementY: -80 });
assert.ok(state().camera.position.distanceTo(beforeLook) < 1e-10);
assert.ok(Math.abs(state().camera.quaternion.dot(beforeRotation)) < .999);
nodes['#walk-height'].value = '.4'; nodes['#walk-height'].fire('input');
assert.ok(Math.abs(state().camera.position.dot(state().movement.up) - state().movement.baseHeight - .4) < 1e-10);
nodes['#movement-speed'].value = '2'; key('ShiftLeft');
const fasterStart = state().camera.position.clone();
frame(1100);
assert.ok(Math.abs(state().camera.position.distanceTo(fasterStart) - .15) < 1e-10);
assert.ok(Math.abs(state().camera.position.dot(state().movement.up) - state().movement.baseHeight - .4) < 1e-10);
document.exitPointerLock();
const released = state().camera.position.clone();
assert.equal(state().keys.size, 0);
frame(1150); frame(1200);
assert.ok(state().camera.position.distanceTo(released) < 1e-10, 'Esc clears held movement keys');

nodes['#free-view'].fire('click');
assert.equal(nodes['#navigation-mode'].value, 'free');
assert.equal(nodes['#walk-height-control'].hidden, true);
assert.equal(document.activeElement, nodes['#c']);
nodes['#c'].fire('pointerdown', { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
nodes['#c'].fire('pointermove', { pointerId: 1, clientX: 100, clientY: 20 });
nodes['#c'].fire('pointerup');
const freeHeight = state().camera.position.dot(state().movement.up);
key('KeyW'); frame(1250); frame(1300); key('KeyW', 'keyup');
assert.ok(state().camera.position.dot(state().movement.up) > freeHeight, 'Free view follows pitch without mouse lock');
const elevated = state().camera.position.dot(state().movement.up);
key('KeyQ'); frame(1350); key('KeyQ', 'keyup');
assert.ok(state().camera.position.dot(state().movement.up) < elevated);
key('KeyE'); frame(1400); key('KeyE', 'keyup');
assert.ok(Math.abs(state().camera.position.dot(state().movement.up) - elevated) < 1e-10);
nodes['#movement-speed'].focus();
assert.equal(key('KeyW', 'keydown', nodes['#movement-speed']).defaultPrevented, undefined);
assert.equal(state().keys.size, 0, 'Form inputs do not trigger navigation');
nodes['#c'].focus();nodes['#pointer-lock'].fire('click');key('KeyW');
const beforeConfig=state().camera.position.clone();
nodes['#model-config-open'].fire('click');
assert.equal(nodes['#model-config-dialog'].open,true);
assert.equal(document.pointerLockElement,null);
assert.equal(state().keys.size,0);
key('KeyW');frame(1410);frame(1420);
assert.ok(state().camera.position.distanceTo(beforeConfig)<1e-10,'Config inspection pauses navigation');
nodes['#model-config-close'].fire('click');
assert.equal(nodes['#model-config-dialog'].open,false);
nodes['#c'].focus(); key('KeyW'); ui.window.fire('blur');
assert.equal(state().keys.size, 0, 'Switching windows stops movement');
key('KeyW'); document.fire('visibilitychange');
assert.equal(state().keys.size, 0, 'Switching tabs stops movement');
nodes['#pointer-lock'].fire('click');
nodes['#navigation-mode'].value = 'orbit'; nodes['#navigation-mode'].fire('change');
assert.equal(document.pointerLockElement, null);
assert.equal(state().controls.enabled, true);
assert.equal(nodes['#movement-controls'].hidden, true);
assert.equal(state().keys.size, 0);

nodes['#c'].requestPointerLock = () => Promise.reject(new Error('Pointer lock denied'));
nodes['#walk-start'].fire('click');
await new Promise(resolve => setImmediate(resolve));
assert.equal(nodes['#pointer-lock'].disabled, false);
assert.match(nodes['#help'].textContent, /ล็อกเมาส์ไม่ได้/);
const fallbackStart = state().camera.position.clone();
key('KeyW'); frame(1500); frame(1550); key('KeyW', 'keyup');
assert.ok(state().camera.position.distanceTo(fallbackStart) > 0, 'WASD and drag remain available when pointer lock is denied');
nodes['#navigation-mode'].value = 'capture'; nodes['#navigation-mode'].fire('change');
const stationary = state().camera.position.clone();
nodes['#c'].fire('pointerdown', { button: 0, pointerId: 2, clientX: 20, clientY: 20 });
nodes['#c'].fire('pointermove', { pointerId: 2, clientX: 60, clientY: 30 });
assert.ok(state().camera.position.distanceTo(stationary) < 1e-10, 'Capture mode still turns in place');

const withoutPoses = await createViewer({ cameras: [] });
assert.equal(withoutPoses.state().ready, true);
withoutPoses.nodes['#free-view'].fire('click');
withoutPoses.key('KeyE'); withoutPoses.frame(100); withoutPoses.frame(150);
assert.ok(withoutPoses.state().camera.position.dot(withoutPoses.state().movement.up) > withoutPoses.state().movement.baseHeight, 'Free view also works for uploaded models without a camera path');
const tiltedCaptures = captures.map(capture => {
  const pose = new THREE.Matrix4().makeRotationX(Math.PI / 2 - .1);
  pose.setPosition(...capture.transform_matrix.map(row => row[3]).slice(0, 3));
  return { ...capture, transform_matrix: [0,1,2,3].map(row => [0,1,2,3].map(column => pose.elements[column*4+row])) };
});
const metric = await createViewer({ cameras: tiltedCaptures, path_span: 104, scene_radius: 60, world_units: 'meters', world_up: [0,0,1] });
metric.nodes['#walk-start'].fire('click');
const metricStart = metric.state().camera.position.clone();
metric.key('KeyW'); metric.frame(100); metric.frame(150);
assert.ok(Math.abs(metric.state().camera.position.distanceTo(metricStart) - .07) < 1e-10, 'A long metric scene uses human walking speed');
assert.ok(Math.abs(metric.state().camera.position.z - metricStart.z) < 1e-10, 'A tilted capture keeps level with the metric floor when walking');
metric.nodes['#capture-position'].value = '0'; metric.nodes['#capture-position'].fire('input');
assert.deepEqual(metric.state().movement.up.toArray(), [0,0,1], 'Jumping to another tilted capture retains world up');
assert.equal(metric.nodes['#walk-height'].step, .01);
assert.equal(metric.nodes['#walk-height'].max, 3);
assert.match(metric.nodes['#walk-height-value'].textContent, /เมตร/);
const xboxPad = () => ({ id: 'Xbox Wireless Controller', index: 0, connected: true, mapping: 'standard',
  axes: [0,0,0,0], buttons: Array.from({ length: 17 }, () => ({ value: 0, pressed: false })) });
const joy = await createViewer({ cameras: tiltedCaptures, path_span: 104, world_units: 'meters', world_up: [0,0,1] });
let pad = xboxPad();
pad.axes[1] = -1;
joy.gamepads([pad]);
const joyStart = joy.state().camera.position.clone();
joy.frame(0); joy.frame(50);
assert.ok(joy.state().camera.position.distanceTo(joyStart) < 1e-10, 'Gamepads do not control the viewer until enabled');
joy.nodes['#gamepad-toggle'].fire('click');
assert.equal(joy.nodes['#navigation-mode'].value, 'walk');
assert.equal(joy.document.pointerLockElement, null, 'A controller does not require mouse lock');
joy.frame(100);
assert.ok(joy.state().camera.position.distanceTo(joyStart) < 1e-10, 'An already-held stick must be released before activating');
pad.axes[1] = 0; joy.frame(150);
pad.axes[1] = -.575; joy.frame(200);
assert.ok(Math.abs(joy.state().camera.position.distanceTo(joyStart) - .035) < 1e-10, 'Partial stick input moves at partial walking speed');
assert.equal(joy.state().camera.position.z, joyStart.z, 'Walking preserves metric floor height');
pad.axes = [1,-1,0,0];
let before = joy.state().camera.position.clone(); joy.frame(250);
assert.ok(Math.abs(joy.state().camera.position.distanceTo(before) - .07) < 1e-10, 'Diagonal sticks do not move faster');
pad.axes = [0,-1,0,0]; pad.buttons[10] = { value: 1, pressed: true };
before = joy.state().camera.position.clone(); joy.frame(300);
assert.ok(Math.abs(joy.state().camera.position.distanceTo(before) - .21) < 1e-10, 'L3 runs at three times walking speed');
pad.buttons[10] = { value: 0, pressed: false };
pad.axes = [0,0,.5,-.5];
before = joy.state().camera.position.clone();
const rotationBefore = joy.state().camera.quaternion.clone(); joy.frame(350);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Right stick turns at the same position');
assert.ok(Math.abs(joy.state().camera.quaternion.dot(rotationBefore)) < .99999);
pad.axes = [0,0,0,0]; pad.buttons[7] = { value: .525, pressed: true };
const zBefore = joy.state().camera.position.z; joy.frame(400);
assert.ok(Math.abs(joy.state().camera.position.z-zBefore-.0175) < 1e-10, 'RT continuously raises walk height');
joy.nodes['#walk-height'].value = '3'; joy.nodes['#walk-height'].fire('input'); joy.frame(450);
assert.equal(joy.state().movement.height, 3, 'Controller height stays within slider limits');
pad.buttons[7] = { value: 0, pressed: false }; pad.buttons[6] = { value: .525, pressed: true }; joy.frame(475);
assert.ok(joy.state().movement.height < 3, 'LT lowers walk height');
pad.buttons[6] = { value: 0, pressed: false }; pad.buttons[7] = { value: .525, pressed: true };
pad.buttons[1] = { value: 1, pressed: true }; joy.frame(500);
assert.equal(joy.nodes['#navigation-mode'].value, 'free');
const freeState = joy.state().movement;
joy.frame(550); const freeZ = joy.state().camera.position.z; joy.frame(600);
assert.ok(joy.state().camera.position.z > freeZ, 'RT flies upward in Free view');
assert.equal(joy.state().movement, freeState, 'Holding B does not repeatedly reset the camera');
pad = xboxPad(); pad.buttons[3] = { value: 1, pressed: true }; joy.gamepads([pad]); joy.frame(650);
assert.equal(joy.nodes['#navigation-mode'].value, 'capture');
before = joy.state().camera.position.clone(); pad.axes = [0,-1,.8,0]; joy.frame(700);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Capture mode only looks around');
pad = xboxPad(); joy.gamepads([pad]); joy.frame(750);
joy.nodes['#capture-position'].value = '0'; joy.nodes['#capture-position'].fire('input');
pad.buttons[5] = { value: 1, pressed: true }; joy.frame(800);
assert.equal(Number(joy.nodes['#capture-position'].value), 1, 'RB jumps to the next camera position');
joy.nodes['#capture-position'].value = '0'; joy.nodes['#capture-position'].fire('input'); joy.frame(850);
assert.equal(Number(joy.nodes['#capture-position'].value), 0, 'Holding RB does not jump every frame');
pad = xboxPad(); pad.buttons[0] = { value: 1, pressed: true }; joy.gamepads([pad]); joy.frame(900);
pad = xboxPad(); pad.axes[1] = -1; joy.gamepads([pad]); joy.frame(950); joy.frame(1000);
pad = xboxPad(); pad.buttons[2] = { value: 1, pressed: true }; joy.gamepads([pad]); joy.frame(1050);
assert.ok(joy.state().camera.position.distanceTo(new THREE.Vector3(0,2,3)) < 1e-10, 'X returns to the selected captured position');
pad = xboxPad(); pad.axes[1] = -1; joy.gamepads([pad]); joy.frame(1100);
joy.nodes['#movement-speed'].focus(); before = joy.state().camera.position.clone(); joy.frame(1150);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Editing toolbar inputs pauses gamepad navigation');
assert.match(joy.nodes['#gamepad-status'].textContent, /พัก/);
joy.nodes['#c'].focus(); joy.frame(1200);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Regaining canvas focus with held sticks does not move');
pad.axes[1] = 0; joy.frame(1250); pad.axes[1] = -1; joy.frame(1300);
assert.ok(joy.state().camera.position.distanceTo(before) > 0);
joy.window.fire('blur'); before = joy.state().camera.position.clone(); joy.frame(1350);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Switching windows pauses gamepad movement');
joy.window.fire('focus'); joy.frame(1400);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'A held stick cannot resume after switching back');
pad.axes[1] = 0; joy.frame(1450); pad.axes[1] = -1; joy.frame(1500);
joy.document.hidden = true; joy.document.fire('visibilitychange'); before = joy.state().camera.position.clone(); joy.frame(1550);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'A hidden tab does not read controller motion');
joy.document.hidden = false; joy.document.fire('visibilitychange'); joy.frame(1600);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Returning to a tab needs neutral controls');
pad.axes[1] = 0; joy.frame(1650); pad.axes[1] = -1; joy.frame(1700);
joy.gamepads([]); before = joy.state().camera.position.clone(); joy.frame(1750);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Disconnect stops movement immediately');
assert.match(joy.nodes['#gamepad-status'].textContent, /รอจอย/);
joy.gamepads([pad]); joy.frame(1800);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Reconnect with a held stick cannot jump');
pad.axes[1] = 0; joy.frame(1850); pad.axes[1] = -1; before = joy.state().camera.position.clone(); joy.frame(100000);
assert.ok(Math.abs(joy.state().camera.position.distanceTo(before)-.14) < 1e-10, 'A stalled frame caps controller movement');
joy.nodes['#gamepad-toggle'].fire('click'); before = joy.state().camera.position.clone(); joy.frame(100050);
assert.ok(joy.state().camera.position.distanceTo(before) < 1e-10, 'Turning off gamepad control stops it');
const noGamepadApi = await createViewer({ cameras: [] }, { unsupported: true });
assert.equal(noGamepadApi.nodes['#gamepad-toggle'].disabled, true);
assert.match(noGamepadApi.nodes['#gamepad-status'].textContent, /ไม่รองรับ/);
const blockedGamepad = await createViewer({ cameras: captures }, { blocked: true });
blockedGamepad.nodes['#gamepad-toggle'].fire('click'); blockedGamepad.frame(0);
assert.match(blockedGamepad.nodes['#gamepad-status'].textContent, /อ่านจอยไม่ได้/);
assert.equal(blockedGamepad.state().ready, true, 'A denied Gamepad API does not break the viewer');
const lockedJoy = await createViewer({ cameras: tiltedCaptures, path_span: 104, world_units: 'meters', world_up: [0,0,1] });
pad = xboxPad(); lockedJoy.gamepads([pad]); lockedJoy.nodes['#gamepad-toggle'].fire('click');
lockedJoy.nodes['#pointer-lock'].fire('click'); lockedJoy.frame(0); pad.axes[1] = -1; lockedJoy.frame(50);
lockedJoy.nodes['#movement-speed'].focus(); before = lockedJoy.state().camera.position.clone(); lockedJoy.frame(100);
assert.ok(lockedJoy.state().camera.position.distanceTo(before) < 1e-10, 'Toolbar editing pauses gamepads even while the mouse is locked');
const joyWithoutPoses = await createViewer({ cameras: [] });
pad = xboxPad(); joyWithoutPoses.gamepads([pad]); joyWithoutPoses.nodes['#gamepad-toggle'].fire('click');
joyWithoutPoses.frame(0); pad.axes[1] = -1; before = joyWithoutPoses.state().camera.position.clone(); joyWithoutPoses.frame(50);
assert.ok(joyWithoutPoses.state().camera.position.distanceTo(before) > 0, 'A controller works on uploaded models without camera poses');
joyWithoutPoses.nodes['#model-config-open'].fire('click');before=joyWithoutPoses.state().camera.position.clone();joyWithoutPoses.frame(100);
assert.ok(joyWithoutPoses.state().camera.position.distanceTo(before)<1e-10,'Inspecting config pauses held controller sticks');
joyWithoutPoses.nodes['#model-config-close'].fire('click');joyWithoutPoses.nodes['#c'].focus();joyWithoutPoses.frame(150);
assert.ok(joyWithoutPoses.state().camera.position.distanceTo(before)<1e-10,'Closing config requires neutral controls before resuming');
console.log('Xbox controls passed: analog walk/look, sprint, height, free flight, captured positions, buttons held, focus, tabs, disconnect, reconnect, frame stalls and API fallback.');
console.log('Model viewer controls passed: mode buttons, pointer lock, WASD, height, speed, free flight, focus, Escape, fallback and models without poses.');

const mapped = await createViewer({ cameras: captures, world_up: [0,0,1], world_units: 'meters', path_span: 2 });
assert.equal(mapped.nodes['#mini-map'].hidden, false);
mapped.nodes['#walk-start'].fire('click');
const firstPoint = mapped.state().miniMap.map.points[0].pixel;
mapped.nodes['#mini-map-svg'].fire('click', { clientX:firstPoint[0], clientY:firstPoint[1] });
assert.equal(mapped.state().index, 1, 'Locked mouse does not teleport through the map');
mapped.document.exitPointerLock();
mapped.key('KeyW');
mapped.nodes['#mini-map-svg'].fire('click', { clientX:firstPoint[0], clientY:firstPoint[1] });
assert.equal(mapped.state().index, 0);
assert.equal(mapped.nodes['#navigation-mode'].value, 'walk', 'Map jump preserves Walk mode');
assert.equal(mapped.state().keys.size, 0, 'Jumping clears movement keys');
assert.ok(mapped.state().camera.position.distanceTo(new THREE.Vector3(0,2,3)) < 1e-10);
assert.equal(mapped.state().movement.height, 0);
mapped.nodes['#mini-map-svg'].fire('keydown', { key:'ArrowRight' });
assert.equal(mapped.state().index, 1);
mapped.nodes['#mini-map-toggle'].fire('click');
assert.equal(mapped.nodes['#mini-map'].hidden,true);
mapped.nodes['#mini-map-toggle'].fire('click');
assert.equal(mapped.nodes['#mini-map'].hidden,false);
mapped.frame(0); mapped.frame(150);
assert.match(mapped.nodes['#mini-map-viewer'].attributes.transform,/translate/);
assert.match(mapped.nodes['#mini-map-label'].textContent,/2\/2/);
assert.equal(withoutPoses.nodes['#mini-map'].hidden,true);
assert.equal(withoutPoses.nodes['#mini-map-toggle'].hidden,true);
console.log('Mini-map viewer controls passed: mouse lock, picking, keyboard, movement reset, mode, markers, hiding and no-pose fallback.');
