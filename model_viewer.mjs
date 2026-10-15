import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { placeCapturedCamera, turnAtPosition, fitCapturedFov, syncLookTarget, beginFirstPerson, setWalkHeight, moveFirstPerson } from './model_navigation.mjs';
import { GamepadInput } from './model_gamepad.mjs';
import { MiniMap } from './model_minimap.mjs';

const config = JSON.parse(document.querySelector('#model-config').textContent);
const canvas = document.querySelector('#c');
const viewport = document.querySelector('#viewer');
const status = document.querySelector('#status');
const errorBox = document.querySelector('#error');
const mode = document.querySelector('#navigation-mode');
const slider = document.querySelector('#capture-position');
const positionLabel = document.querySelector('#capture-label');
const sourceLink = document.querySelector('#capture-image');
const help = document.querySelector('#help');
const walkButton = document.querySelector('#walk-start');
const freeButton = document.querySelector('#free-view');
const movementControls = document.querySelector('#movement-controls');
const lockButton = document.querySelector('#pointer-lock');
const heightControl = document.querySelector('#walk-height-control');
const heightSlider = document.querySelector('#walk-height');
const heightValue = document.querySelector('#walk-height-value');
const speedSelect = document.querySelector('#movement-speed');
const gamepadButton = document.querySelector('#gamepad-toggle');
const gamepadStatus = document.querySelector('#gamepad-status');
const configDialog = document.querySelector('#model-config-dialog');
const gamepadInput = new GamepadInput();
const gamepadSupported = typeof navigator !== 'undefined' && typeof navigator.getGamepads === 'function';
let gamepadEnabled = false, gamepadBlocked = false;
let windowFocused = typeof document.hasFocus === 'function' ? document.hasFocus() : true;
const camera = new THREE.PerspectiveCamera(55, 1, .001, 10000);
const scene = new THREE.Scene();
// Brush optimizes encoded image RGB. Keep these colors in the same space.
THREE.ColorManagement.workingColorSpace = THREE.SRGBColorSpace;
scene.background = new THREE.Color(0x101217);
let renderer, controls, captures = [], index = 0, focusDistance = .5, horizontalFov = 90;
let pointer = null;
const look = { pitch: 0 };
const keys = new Set();
const movementKeys = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE', 'ShiftLeft', 'ShiftRight']);
let movement = null, movementScale = 1, movementSpeed = .25, metricUnits = false, lastFrameTime = null, ready = false, lockPending = false, lockHint = '';
let navigationUp = null;
let miniMap = null;
const mapDirection = new THREE.Vector3();
const variantSelect = document.querySelector('#model-variant');
if (variantSelect) {
  variantSelect.value = config.variant || 'full';
  variantSelect.addEventListener('change', () => {
    const url = new URL(location.href);
    url.searchParams.set('variant', variantSelect.value);
    url.searchParams.set('position', String(index));
    location.href = url.href;
  });
}

document.querySelector('#model-config-open')?.addEventListener('click', () => {
  keys.clear(); pointer = null; gamepadInput.reset();
  if (document.pointerLockElement === canvas) document.exitPointerLock?.();
  configDialog?.showModal();
});
document.querySelector('#model-config-close')?.addEventListener('click', () => configDialog?.close());

function showGamepadStatus(message) {
  if (gamepadStatus.textContent !== message) gamepadStatus.textContent = message;
}

function updateGamepad(seconds) {
  if (!gamepadEnabled || !gamepadSupported || gamepadBlocked) return null;
  let pads;
  try { pads = navigator.getGamepads(); }
  catch {
    gamepadBlocked = true;
    gamepadInput.reset();
    showGamepadStatus('เบราว์เซอร์นี้อ่านจอยไม่ได้ ลองเปิดผ่าน localhost หรือ HTTPS');
    return null;
  }
  const editing = document.activeElement?.isContentEditable || /^(INPUT|SELECT|TEXTAREA|BUTTON|SUMMARY)$/.test(document.activeElement?.tagName);
  const active = windowFocused && !document.hidden && !configDialog?.open && !editing && (isMouseLocked() || document.activeElement === canvas);
  const sample = gamepadInput.read(pads, active);
  const messages = {
    waiting: 'รอจอย Xbox · กดปุ่มบนจอยหนึ่งครั้งเพื่อตรวจพบ',
    unsupported: 'จอยนี้ยังไม่รองรับผังปุ่มมาตรฐาน',
    paused: 'จอยพักอยู่ · คลิกภาพเพื่อควบคุม',
    release: 'ปล่อยอนาล็อกและปุ่มทั้งหมดเพื่อเริ่มควบคุม',
    active: 'จอยพร้อม · A เดิน / B Free view',
  };
  showGamepadStatus(messages[sample.state]);
  if (!sample.movement) return null;
  if (sample.pressed.has(0)) { mode.value = 'walk'; setMode(); }
  else if (sample.pressed.has(1)) { mode.value = 'free'; setMode(); }
  else if (sample.pressed.has(3) && captures.length) { mode.value = 'capture'; choosePosition(index); }
  if (sample.pressed.has(2) && captures.length) choosePosition(index);
  const step = Number(sample.pressed.has(5)) - Number(sample.pressed.has(4));
  if (step && captures.length) choosePosition(index + step);
  const dt = Number.isFinite(seconds) ? Math.max(0, Math.min(seconds, .1)) : 0;
  if (mode.value !== 'orbit') {
    // turnAtPosition consumes mouse pixels at .003 radians per pixel.
    turnAtPosition(camera, controls, look, sample.look.x * dt * 2.2 / .003,
      sample.look.y * dt * 2.2 / .003, focusDistance);
  }
  if (mode.value === 'walk' && movement && sample.movement.up) {
    const height = THREE.MathUtils.clamp(movement.height + sample.movement.up * dt * movementSpeed * .5,
      Number(heightSlider.min), Number(heightSlider.max));
    setWalkHeight(camera, controls, movement, height, focusDistance);
    heightSlider.value = String(height);
    updateHeightLabel();
  }
  return sample.movement;
}

gamepadButton.addEventListener('click', () => {
  if (!ready || !gamepadSupported) return;
  gamepadEnabled = !gamepadEnabled;
  gamepadBlocked = false;
  gamepadInput.reset();
  clearMovement();
  gamepadButton.setAttribute('aria-pressed', String(gamepadEnabled));
  gamepadButton.textContent = gamepadEnabled ? 'ปิดจอย Xbox' : 'เปิดจอย Xbox';
  if (gamepadEnabled) {
    if (!isFirstPerson()) { mode.value = 'walk'; setMode(); }
    canvas.focus({ preventScroll: true });
    updateGamepad(0);
  } else showGamepadStatus('ปิดการควบคุมด้วยจอย');
});
addEventListener('gamepaddisconnected', () => { gamepadInput.reset(); if (gamepadEnabled) updateGamepad(0); });
addEventListener('gamepadconnected', () => { if (gamepadEnabled) updateGamepad(0); });

document.querySelector('#sibling-model')?.addEventListener('change', e => { location.href = e.target.value; });
document.querySelector('#renderer-mode').value = config.renderer;
document.querySelector('#renderer-mode').addEventListener('change', e => {
  const url = new URL(location.href);
  url.searchParams.set('renderer', e.target.value);
  location.href = url.href;
});
document.querySelector('#refine-open')?.addEventListener('click', () => document.querySelector('#refine-dialog').showModal());
document.querySelector('#refine-close')?.addEventListener('click', () => document.querySelector('#refine-dialog').close());

function resize() {
  if (!renderer) return;
  let width = Math.max(1, viewport.clientWidth), height = Math.max(1, viewport.clientHeight);
  if (config.review_camera) {
    const aspect = config.review_camera.w / config.review_camera.h;
    width = Math.min(width, height * aspect); height = width / aspect;
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    canvas.style.margin = 'auto';
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  fitCapturedFov(camera, horizontalFov);
}

function resetReviewCamera() {
  if (!ready || !config.review_camera) return;
  mode.value = 'capture'; setMode(); clearMovement();
  horizontalFov = config.review_camera.fov_x;
  placeCapturedCamera(camera, controls, config.review_camera, focusDistance);
  fitCapturedFov(camera, horizontalFov);
  positionLabel.textContent = 'มุมตรวจภาพ · ' + config.review_camera.name;
}
document.querySelector('#review-reset')?.addEventListener('click', resetReviewCamera);
document.querySelector('#review-save')?.addEventListener('click', async () => {
  if (!ready) return;
  try {
    renderer.render(scene, camera);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('สร้างภาพไม่ได้');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = `${config.model}-${config.variant}-${config.review_camera?.name || 'view'}.png`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (error) { status.textContent = 'บันทึกภาพไม่ได้ · ' + error.message; }
});

function isFirstPerson() { return mode.value === 'walk' || mode.value === 'free'; }
function isMouseLocked() { return document.pointerLockElement === canvas; }

function clearMovement() { keys.clear(); lastFrameTime = null; pointer = null; }

function updateHelp() {
  const locked = isMouseLocked();
  lockButton.disabled = !ready || lockPending;
  lockButton.textContent = locked ? 'ปล่อยเมาส์ (Esc)' : isFirstPerson() ? 'ล็อกเมาส์' : 'Walk + ล็อกเมาส์';
  lockButton.setAttribute('aria-pressed', String(locked));
  lockButton.title = locked ? 'กด Esc หรือปุ่มนี้เพื่อปล่อยเมาส์' : 'ล็อกเมาส์เพื่อเดินและหันมอง · Esc เพื่อปล่อย';
  if (mode.value === 'orbit') {
    help.textContent = 'Orbit: ลากซ้ายหมุนรอบฉาก · ขวาเลื่อน · ซูมด้วยล้อเมาส์';
  } else if (isFirstPerson()) {
    const movementHelp = mode.value === 'walk'
      ? 'Walk: WASD เดิน · ปรับความสูงด้วยแถบ · Shift เดินเร็ว'
      : 'Free view: WASD เคลื่อนตามทิศที่มอง · Q ลง / E ขึ้น · Shift เคลื่อนเร็ว';
    help.textContent = movementHelp + (locked ? ' · ขยับเมาส์หันมอง · Esc ปล่อยเมาส์' : ' · ลากเมาส์หันมอง · คลิกภาพแล้วใช้ WASD หรือกดล็อกเมาส์') + (lockHint ? ' · ' + lockHint : '');
  } else {
    help.textContent = 'ลากเพื่อหันมอง ณ ตำแหน่งเดิม · เลื่อนแถบหรือปุ่มเพื่อเดินตามจุดถ่าย · ล้อเมาส์ขยายภาพ';
  }
}

function updateHeightLabel() {
  const value = Number(heightSlider.value);
  heightValue.textContent = `${value > 0 ? '+' : ''}${value.toLocaleString('th-TH', { maximumFractionDigits: 3 })} ${metricUnits ? 'เมตร' : 'หน่วยฉาก'}`;
}

function setMode() {
  if (!controls) return;
  // Rebuild OrbitControls so its remaining damping cannot move the new view.
  resetOrbitFrame();
  clearMovement();
  lockHint = '';
  controls.enabled = mode.value === 'orbit';
  controls.enableDamping = controls.enabled;
  controls.enablePan = controls.enabled;
  controls.enableRotate = controls.enabled;
  controls.enableZoom = controls.enabled;
  movementControls.hidden = !isFirstPerson();
  heightControl.hidden = mode.value !== 'walk';
  walkButton.setAttribute('aria-pressed', String(mode.value === 'walk'));
  freeButton.setAttribute('aria-pressed', String(mode.value === 'free'));
  if (isFirstPerson()) {
    if (navigationUp) camera.up.copy(navigationUp);
    movement = beginFirstPerson(camera);
    look.pitch = movement.pitch;
    heightSlider.value = '0';
    updateHeightLabel();
    syncLookTarget(camera, controls, focusDistance);
  } else {
    movement = null;
    if (isMouseLocked()) document.exitPointerLock();
  }
  updateHelp();
}

function pointerLockFailed() {
  lockPending = false;
  lockHint = 'ล็อกเมาส์ไม่ได้ ลองกดอีกครั้ง หรือใช้การลากเมาส์';
  updateHelp();
}

function lockMouse() {
  if (!ready || lockPending) return;
  if (isMouseLocked()) { document.exitPointerLock(); return; }
  if (!isFirstPerson()) { mode.value = 'walk'; setMode(); }
  canvas.focus({ preventScroll: true });
  if (!canvas.requestPointerLock) { pointerLockFailed(); return; }
  lockPending = true;
  lockHint = '';
  updateHelp();
  try {
    const request = canvas.requestPointerLock();
    request?.catch(pointerLockFailed);
  } catch { pointerLockFailed(); }
}

function resetOrbitFrame() {
  // OrbitControls caches camera.up when constructed. Rebuild it when jumping
  // to another captured pose, preserving the camera and discarding inertia.
  const position = camera.position.clone(), quaternion = camera.quaternion.clone();
  const target = controls.target.clone();
  const limits = { minDistance: controls.minDistance, maxDistance: controls.maxDistance };
  controls.dispose();
  controls = new OrbitControls(camera, canvas);
  Object.assign(controls, limits);
  camera.position.copy(position);
  camera.quaternion.copy(quaternion);
  controls.target.copy(target);
  camera.updateMatrixWorld();
}

function choosePosition(value) {
  index = Math.max(0, Math.min(captures.length - 1, Number(value)));
  const capture = captures[index];
  if (!capture) return;
  slider.value = index;
  horizontalFov = capture.fov_x || 90;
  placeCapturedCamera(camera, controls, capture, focusDistance);
  look.pitch = 0;
  setMode();
  fitCapturedFov(camera, horizontalFov);
  positionLabel.textContent = `จุด ${index + 1}/${captures.length} · Frame ${capture.frame_index} · ภาพเทรน ${capture.w}×${capture.h}`;
  sourceLink.href = capture.image_url;
  sourceLink.hidden = false;
  if (controls.enabled) controls.update();
}

slider.addEventListener('input', e => choosePosition(e.target.value));
document.querySelector('#capture-prev').addEventListener('click', () => choosePosition(index - 1));
document.querySelector('#capture-next').addEventListener('click', () => choosePosition(index + 1));
mode.addEventListener('change', setMode);
walkButton.addEventListener('click', () => {
  if (!ready) return;
  mode.value = 'walk'; setMode(); lockMouse();
});
freeButton.addEventListener('click', () => {
  if (!ready) return;
  mode.value = 'free'; setMode(); canvas.focus({ preventScroll: true });
});
lockButton.addEventListener('click', lockMouse);
heightSlider.addEventListener('input', () => {
  if (!ready || mode.value !== 'walk' || !movement) return;
  setWalkHeight(camera, controls, movement, Number(heightSlider.value), focusDistance);
  updateHeightLabel();
});
document.addEventListener('pointerlockchange', () => {
  lockPending = false;
  clearMovement();
  if (isMouseLocked() && !isFirstPerson()) document.exitPointerLock();
  if (isMouseLocked()) lockHint = '';
  updateHelp();
});
document.addEventListener('pointerlockerror', pointerLockFailed);
document.addEventListener('mousemove', e => {
  if (!ready || configDialog?.open || !isMouseLocked() || !isFirstPerson()) return;
  turnAtPosition(camera, controls, look, e.movementX, e.movementY, focusDistance);
});
document.addEventListener('keydown', e => {
  if (!ready || configDialog?.open || !isFirstPerson() || (!isMouseLocked() && document.activeElement !== canvas)) return;
  if (e.target?.isContentEditable || /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(e.target?.tagName)) return;
  if (!movementKeys.has(e.code)) return;
  e.preventDefault();
  keys.add(e.code);
});
document.addEventListener('keyup', e => keys.delete(e.code));
document.addEventListener('visibilitychange', () => { clearMovement(); if (gamepadEnabled) updateGamepad(0); });
addEventListener('blur', () => { windowFocused = false; clearMovement(); if (gamepadEnabled) updateGamepad(0); });
addEventListener('focus', () => { windowFocused = true; clearMovement(); });
canvas.addEventListener('blur', () => { if (!isMouseLocked()) clearMovement(); });
canvas.addEventListener('pointerdown', e => {
  if (!ready || mode.value === 'orbit' || isMouseLocked() || e.button !== 0) return;
  canvas.focus({ preventScroll: true });
  pointer = { id: e.pointerId, x: e.clientX, y: e.clientY };
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener('pointermove', e => {
  if (!pointer || pointer.id !== e.pointerId || mode.value === 'orbit' || isMouseLocked()) return;
  turnAtPosition(camera, controls, look, e.clientX - pointer.x, e.clientY - pointer.y, focusDistance);
  pointer.x = e.clientX; pointer.y = e.clientY;
});
function releasePointer() { pointer = null; }
canvas.addEventListener('pointerup', releasePointer);
canvas.addEventListener('pointercancel', releasePointer);
canvas.addEventListener('wheel', e => {
  if (!ready || mode.value === 'orbit') return;
  e.preventDefault();
  horizontalFov = THREE.MathUtils.clamp(horizontalFov * Math.exp(e.deltaY * .001), 25, 110);
  fitCapturedFov(camera, horizontalFov);
}, { passive: false });

async function loadModel() {
  if (config.renderer === 'native') {
    const { WebGPURenderer } = await import('three/webgpu');
    const { GaussianSplatPLYLoader } = await import('three/addons/loaders/GaussianSplatPLYLoader.js');
    const { GaussianSplat } = await import('three/addons/objects/GaussianSplat.js');
    renderer = new WebGPURenderer({ canvas, antialias: false, forceWebGL: !navigator.gpu });
    await renderer.init();
    const geometry = await new GaussianSplatPLYLoader().loadAsync(config.url, progress);
    scene.add(new GaussianSplat(geometry));
    return { count: geometry.getAttribute('position').count, label: 'Three.js Native', bounds: geometry.boundingBox };
  }
  const { SparkRenderer, SplatMesh } = await import('@sparkjsdev/spark');
  renderer = new THREE.WebGLRenderer({ canvas, antialias: false });
  // Match Brush 0.3: covariance +0.3px, without opacity compensation.
  // Avoid LoD merging and radial sorting when validating a trained PLY.
  const spark = new SparkRenderer({ renderer, preBlurAmount: .3, blurAmount: 0,
    sortRadial: false, maxStdDev: Math.sqrt(12), minAlpha: 1 / 255,
    enableLod: false, focalAdjustment: 1 });
  scene.add(spark);
  const splats = new SplatMesh({ url: config.url, fileType: config.variant === 'fast' ? 'spz' : 'ply', extSplats: true,
    lod: false, enableLod: false, onProgress: progress });
  scene.add(splats);
  await splats.initialized;
  return { count: splats.numSplats, label: 'Spark · Brush compatible', bounds: splats.getBoundingBox() };
}

function progress(e) {
  const total = e.total || config.bytes;
  const loaded = Number(e.loaded) || 0;
  status.textContent = total ? `กำลังโหลด ${Math.min(100, Math.round(loaded / total * 100))}% · ${(loaded/1048576).toFixed(1)} / ${(total/1048576).toFixed(1)} MB` : 'กำลังอ่าน Model…';
}

async function start() {
  try {
    const poseResponse = await fetch(config.cameras_url);
    const poses = poseResponse.ok ? await poseResponse.json() : { cameras: [] };
    if (Array.isArray(poses.world_up) && poses.world_up.length === 3 && poses.world_up.every(Number.isFinite)) {
      const up = new THREE.Vector3(...poses.world_up);
      if (up.lengthSq() > 1e-10) navigationUp = up.normalize();
    }
    captures = poses.cameras || [];
    const result = await loadModel();
    renderer.toneMapping = THREE.NoToneMapping;
    controls = new OrbitControls(camera, canvas);
    controls.enabled = mode.value === 'orbit';
    focusDistance = Math.max((poses.path_span || 1) * .15, .01);
    const boundsSize = result.bounds ? result.bounds.getSize(new THREE.Vector3()).length() : 1;
    movementScale = Math.max(poses.path_span || boundsSize || 1, .01);
    metricUnits = poses.world_units === 'meters';
    movementSpeed = metricUnits ? 1.4 : movementScale * .25;
    heightSlider.min = metricUnits ? -3 : -movementScale * .5;
    heightSlider.max = metricUnits ? 3 : movementScale * .5;
    heightSlider.step = metricUnits ? .01 : movementScale / 200;
    controls.minDistance = focusDistance * .02;
    controls.maxDistance = focusDistance * 20;
    camera.near = Math.max((poses.path_span || 1) * .0001, .00001);
    camera.far = Math.max((poses.scene_radius || 100) * 50, 1000);
    resize();
    if (captures.length) {
      slider.max = captures.length - 1;
      slider.disabled = false;
      const requestedPosition = new URL(location.href).searchParams.get('position');
      const initialPosition = requestedPosition !== null && /^\d+$/.test(requestedPosition) ? Number(requestedPosition) : Math.floor(captures.length / 2);
      choosePosition(Math.min(initialPosition, captures.length-1));
    } else {
      document.querySelector('#capture-prev').disabled = true;
      document.querySelector('#capture-next').disabled = true;
      const initial = config.initial_view || {};
      if (initial.transform_matrix) {
        horizontalFov = initial.fov_x || initial.fov_y || 90;
        placeCapturedCamera(camera, controls, initial, focusDistance);
        fitCapturedFov(camera, horizontalFov);
        positionLabel.textContent = 'มองจากจุดถ่ายที่บันทึกไว้ · รุ่นนี้ไม่มีเส้นทางกล้อง';
      } else {
        mode.value = 'orbit';
        mode.querySelector('option[value="capture"]').disabled = true;
        const bounds = result.bounds;
        const center = bounds ? bounds.getCenter(new THREE.Vector3()) : new THREE.Vector3();
        const radius = bounds ? Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, .01) : 1;
        controls.target.copy(center);
        camera.position.copy(center).add(new THREE.Vector3(0, radius * .3, radius * 2.2));
        controls.minDistance = radius * .01;
        controls.maxDistance = radius * 20;
        camera.near = Math.max(radius / 10000, .00001);
        camera.far = Math.max(radius * 20, 1000);
        camera.updateProjectionMatrix();
        positionLabel.textContent = 'รุ่นนี้ไม่มีตำแหน่งกล้องที่เก็บไว้';
      }
    }
    ready = true;
    miniMap = new MiniMap(document, captures, navigationUp?.toArray() || camera.up.toArray(), metricUnits, value => {
      keys.clear(); pointer = null; gamepadInput.reset();
      choosePosition(value);
      miniMap.last = -Infinity;
      miniMap.update(camera.position.toArray(), camera.getWorldDirection(mapDirection).toArray(), index, 0, false);
    });
    mode.disabled = false;
    walkButton.disabled = false;
    freeButton.disabled = false;
    gamepadButton.disabled = !gamepadSupported;
    if (!gamepadSupported) showGamepadStatus('เบราว์เซอร์นี้ไม่รองรับจอย');
    setMode();
    if (config.review_camera) {
      resetReviewCamera();
      for (const id of ['#review-reset', '#review-save']) {
        const button = document.querySelector(id);
        if (button) { button.hidden = false; button.disabled = false; }
      }
    }
    status.textContent = `พร้อม · ${Number(result.count || config.splats || 0).toLocaleString()} splats · ${result.label} · ${config.variant === 'fast' ? 'โหลดเร็ว' : 'เต็ม'}`;
    if (config.review_error) { errorBox.style.display = 'block'; errorBox.textContent = config.review_error; }
    renderer.setAnimationLoop(time => {
      const seconds = lastFrameTime === null ? 0 : (time - lastFrameTime) / 1000;
      lastFrameTime = time;
      const analog = updateGamepad(seconds);
      if (controls.enabled) controls.update();
      else if (!configDialog?.open && movement && (isMouseLocked() || document.activeElement === canvas)) {
        moveFirstPerson(camera, controls, movement, keys, seconds, movementSpeed * Number(speedSelect.value), focusDistance, mode.value === 'free', analog);
      }
      miniMap?.update(camera.position.toArray(), camera.getWorldDirection(mapDirection).toArray(), index, time, isMouseLocked());
      renderer.render(scene, camera);
    });
  } catch (error) {
    console.error(error);
    status.textContent = 'เปิด Model ไม่สำเร็จ';
    errorBox.style.display = 'block';
    errorBox.textContent = 'เปิด Gaussian Splat ไม่สำเร็จ\n' + (error?.message || error);
  }
}

addEventListener('resize', resize);
start();
