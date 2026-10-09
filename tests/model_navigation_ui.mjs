// Real Three camera math; no browser or WebGL screenshot is implied.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const core = fs.readFileSync(new URL('../assets/vendor/three-0.180.0/three.core.js', import.meta.url), 'utf8');
const THREE = await import('data:text/javascript;base64,' + Buffer.from(core).toString('base64'));
const source = fs.readFileSync(new URL('../model_navigation.mjs', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
const context = vm.createContext({ THREE });
vm.runInContext(source, context);
for (const rotation of [new THREE.Quaternion(), new THREE.Quaternion().setFromEuler(new THREE.Euler(.2, .4, Math.PI))]) {
  const camera = new THREE.PerspectiveCamera();
  const controls = { target: new THREE.Vector3() };
  const matrix = new THREE.Matrix4().compose(new THREE.Vector3(2, 3, 4), rotation, new THREE.Vector3(1, 1, 1));
  const capture = { transform_matrix: [0, 1, 2, 3].map(r => [0, 1, 2, 3].map(c => matrix.elements[c * 4 + r])) };
  context.camera = camera; context.controls = controls; context.capture = capture; context.state = { pitch: 0 };
  vm.runInContext('placeCapturedCamera(camera, controls, capture, 1.2)', context);
  assert.ok(camera.position.distanceTo(new THREE.Vector3(2, 3, 4)) < 1e-10);
  assert.ok(Math.abs(camera.quaternion.dot(rotation)) > .999999);
  const before = camera.position.clone();
  vm.runInContext('turnAtPosition(camera, controls, state, 140, 100, 1.2)', context);
  assert.ok(camera.position.distanceTo(before) < 1e-10, 'Looking must not orbit the camera away from the capture path');
  assert.ok(Math.abs(camera.position.distanceTo(controls.target) - 1.2) < 1e-10);
  vm.runInContext('turnAtPosition(camera, controls, state, 0, 100000, 1.2)', context);
  assert.ok(Math.abs(context.state.pitch) <= Math.PI * .47);
  camera.aspect = 3;
  vm.runInContext('fitCapturedFov(camera, 90)', context);
  assert.ok(Math.abs(camera.fov - THREE.MathUtils.radToDeg(2 * Math.atan(1 / 3))) < 1e-10);
}
for (const rotation of [new THREE.Quaternion(), new THREE.Quaternion().setFromEuler(new THREE.Euler(.2, .4, Math.PI))]) {
  const camera = new THREE.PerspectiveCamera();
  const controls = { target: new THREE.Vector3() };
  camera.position.set(2, 3, 4);
  camera.quaternion.copy(rotation);
  camera.up.set(0, 1, 0).applyQuaternion(rotation).normalize();
  camera.rotateX(.5);
  const state = context.beginFirstPerson(camera);
  const start = camera.position.clone();
  const initialHeight = start.dot(state.up);
  context.moveFirstPerson(camera, controls, state, new Set(['KeyW']), .05, 2, 1.2);
  assert.ok(Math.abs(camera.position.distanceTo(start) - .1) < 1e-10, 'Walking speed uses elapsed time');
  assert.ok(Math.abs(camera.position.dot(state.up) - initialHeight) < 1e-10, 'Walking remains level even while looking up');
  camera.position.copy(start);
  context.moveFirstPerson(camera, controls, state, new Set(['KeyW', 'KeyD']), .05, 2, 1.2);
  assert.ok(Math.abs(camera.position.distanceTo(start) - .1) < 1e-10, 'Diagonal input must not increase speed');
  context.setWalkHeight(camera, controls, state, .7, 1.2);
  assert.ok(Math.abs(camera.position.dot(state.up) - initialHeight - .7) < 1e-10, 'Height follows the captured up direction');
  context.moveFirstPerson(camera, controls, state, new Set(['KeyW', 'ShiftLeft']), .05, 2, 1.2);
  assert.ok(Math.abs(camera.position.dot(state.up) - initialHeight - .7) < 1e-10, 'WASD preserves the chosen height');
  const freeStart = camera.position.clone();
  context.moveFirstPerson(camera, controls, state, new Set(['KeyW']), .05, 2, 1.2, true);
  assert.ok(camera.position.clone().sub(freeStart).dot(state.up) > .04, 'Free view follows camera pitch');
  const elevated = camera.position.dot(state.up);
  context.moveFirstPerson(camera, controls, state, new Set(['KeyQ']), .05, 2, 1.2, true);
  assert.ok(Math.abs(camera.position.dot(state.up) - elevated + .1) < 1e-10);
  context.moveFirstPerson(camera, controls, state, new Set(['KeyE']), .05, 2, 1.2, true);
  assert.ok(Math.abs(camera.position.dot(state.up) - elevated) < 1e-10);
  const stalled = camera.position.clone();
  context.moveFirstPerson(camera, controls, state, new Set(['KeyW', 'ShiftRight']), 10, 2, 1.2, true);
  assert.ok(Math.abs(camera.position.distanceTo(stalled) - .6) < 1e-10, 'A stalled frame must not teleport the camera');
  const stopped = camera.position.clone();
  context.moveFirstPerson(camera, controls, state, new Set(['KeyW', 'KeyS', 'KeyA', 'KeyD']), .05, 2, 1.2);
  context.moveFirstPerson(camera, controls, state, new Set(['KeyW']), -1, 2, 1.2);
  assert.ok(camera.position.distanceTo(stopped) < 1e-10);
}
const poleCamera = new THREE.PerspectiveCamera();
poleCamera.lookAt(new THREE.Vector3(0, 1, 0));
const poleState = context.beginFirstPerson(poleCamera);
assert.ok(Math.abs(poleState.pitch) <= Math.PI * .47);
assert.ok(poleCamera.quaternion.toArray().every(Number.isFinite));
const viewer = fs.readFileSync(new URL('../model_viewer.mjs', import.meta.url), 'utf8');
assert.match(viewer, /preBlurAmount: \.3, blurAmount: 0/);
assert.match(viewer, /sortRadial: false/);
assert.match(viewer, /enableLod: false/);
assert.match(viewer, /workingColorSpace = THREE\.SRGBColorSpace/);
assert.match(viewer, /controls\.enabled = mode\.value === 'orbit'/);
console.log('Model navigation passed: captured poses, turning, walk height, diagonal speed, free flight, Shift, stalled frames, pitch limits and rendering settings.');
