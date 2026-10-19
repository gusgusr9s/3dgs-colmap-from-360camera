import * as THREE from 'three';
import { WebGPURenderer } from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { GLTFGaussianSplatLoaderExtension } from 'three/addons/loaders/GLTFGaussianSplatLoaderExtension.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export async function createViewer(host, url, callbacks = {}) {
  host.replaceChildren();
  let disposed = false;
  const isCurrent = () => callbacks.isCurrent?.() !== false;
  const cancelled = () => ({ setMode() {}, reset() {}, dispose() {} });
  let renderer = new WebGPURenderer({ antialias: false, forceWebGL: !navigator.gpu });
  try { await renderer.init(); }
  catch {
    renderer.dispose();
    renderer = new WebGPURenderer({ antialias: false, forceWebGL: true });
    await renderer.init();
  }
  if (!isCurrent()) { renderer.dispose(); return cancelled(); }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  host.append(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = new THREE.Color('#151e20');
  const camera = new THREE.PerspectiveCamera(65, 1, .001, 10000);
  const loader = new GLTFLoader();
  loader.register(parser => new GLTFGaussianSplatLoaderExtension(parser));
  let gltf;
  try {
    gltf = await loader.loadAsync(url, e => callbacks.progress?.(e.total ? `กำลังโหลด ${Math.round(e.loaded / e.total * 100)}%` : `กำลังโหลด ${(e.loaded / 1048576).toFixed(1)} MB`));
  } catch (error) { renderer.dispose(); renderer.domElement.remove(); throw error; }
  if (!isCurrent()) { renderer.dispose(); renderer.domElement.remove(); return cancelled(); }
  scene.add(gltf.scene);
  const bounds = new THREE.Box3().setFromObject(gltf.scene);
  const center = bounds.getCenter(new THREE.Vector3());
  const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, .01);
  camera.near = Math.max(radius / 10000, .00001);
  camera.far = Math.max(radius * 20, 1000);
  const initial = gltf.scene.userData.initial_view || {};
  const matrix = initial.transform_matrix;
  let target;
  if (Array.isArray(matrix) && matrix.length === 4 && matrix.every(row => Array.isArray(row) && row.length === 4 && row.every(Number.isFinite))) {
    const transform = new THREE.Matrix4().set(...matrix.flat());
    camera.position.setFromMatrixPosition(transform);
    camera.quaternion.setFromRotationMatrix(transform);
    camera.up.set(0, 1, 0).applyQuaternion(camera.quaternion).normalize();
    camera.fov = initial.fov_y || 65;
    target = camera.position.clone().add(new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion).multiplyScalar(Math.max(.1, radius * .02)));
  } else {
    camera.position.copy(center).add(new THREE.Vector3(0, radius * .3, radius * 2.2));
    target = center;
  }
  camera.updateProjectionMatrix();
  const orbit = new OrbitControls(camera, renderer.domElement);
  orbit.target.copy(target); orbit.update(); orbit.saveState();
  const startingPosition = camera.position.clone();
  const startingQuaternion = camera.quaternion.clone();
  const startingTarget = target.clone();
  let mode = 'orbit';
  let walkHeight = camera.position.y;
  let lastTime = performance.now();
  const pressed = new Set();
  const rotation = new THREE.Euler(0, 0, 0, 'YXZ');
  const onKeyDown = e => { if (['KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE','ShiftLeft','ShiftRight','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.code)) { pressed.add(e.code); if (mode !== 'orbit') e.preventDefault(); } };
  const onKeyUp = e => pressed.delete(e.code);
  const onBlur = () => pressed.clear();
  const onMouseMove = e => {
    if (document.pointerLockElement !== renderer.domElement || mode === 'orbit') return;
    rotation.setFromQuaternion(camera.quaternion, 'YXZ');
    rotation.y -= e.movementX * .002;
    rotation.x = THREE.MathUtils.clamp(rotation.x - e.movementY * .002, -Math.PI / 2 + .01, Math.PI / 2 - .01);
    camera.quaternion.setFromEuler(rotation);
  };
  const onClick = () => { if (mode !== 'orbit') renderer.domElement.requestPointerLock?.(); };
  document.addEventListener('keydown', onKeyDown);
  document.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', onBlur);
  document.addEventListener('mousemove', onMouseMove);
  renderer.domElement.addEventListener('click', onClick);
  const observer = new ResizeObserver(() => {
    const width = host.clientWidth, height = host.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height; camera.updateProjectionMatrix();
  });
  observer.observe(host);
  const move = new THREE.Vector3();
  const forward = new THREE.Vector3();
  const right = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  function animate() {
    if (disposed) return;
    const current = performance.now();
    const dt = Math.min((current - lastTime) / 1000, .05);
    lastTime = current;
    if (mode === 'orbit') orbit.update();
    else {
      forward.set(0, 0, -1).applyQuaternion(camera.quaternion);
      if (mode === 'walk') forward.y = 0;
      forward.normalize();
      right.crossVectors(forward, up).normalize();
      move.set(0, 0, 0);
      if (pressed.has('KeyW') || pressed.has('ArrowUp')) move.add(forward);
      if (pressed.has('KeyS') || pressed.has('ArrowDown')) move.sub(forward);
      if (pressed.has('KeyD') || pressed.has('ArrowRight')) move.add(right);
      if (pressed.has('KeyA') || pressed.has('ArrowLeft')) move.sub(right);
      if (mode === 'free') {
        if (pressed.has('KeyE')) move.add(up);
        if (pressed.has('KeyQ')) move.sub(up);
      }
      if (move.lengthSq()) camera.position.addScaledVector(move.normalize(), dt * radius * (pressed.has('ShiftLeft') || pressed.has('ShiftRight') ? 2 : .5));
      if (mode === 'walk') camera.position.y = walkHeight;
    }
    renderer.render(scene, camera);
  }
  function cleanup() {
    if (disposed) return;
    disposed = true;
    renderer.setAnimationLoop(null);
    if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
    observer.disconnect(); orbit.dispose();
    document.removeEventListener('keydown', onKeyDown);
    document.removeEventListener('keyup', onKeyUp);
    window.removeEventListener('blur', onBlur);
    document.removeEventListener('mousemove', onMouseMove);
    renderer.domElement.removeEventListener('click', onClick);
    renderer.dispose(); renderer.domElement.remove();
  }
  try { await renderer.renderAsync(scene, camera); }
  catch (error) { cleanup(); throw error; }
  if (!isCurrent()) { cleanup(); return cancelled(); }
  renderer.setAnimationLoop(animate);
  let splats = 0;
  gltf.scene.traverse(object => { if (object.isGaussianSplat) splats += object.geometry.instanceCount || 0; });
  callbacks.ready?.(splats ? `${splats.toLocaleString()} SPLATS` : 'MESH GLB');
  return {
    setMode(next) {
      if (!['orbit', 'walk', 'free'].includes(next)) return;
      if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
      mode = next;
      orbit.enabled = next === 'orbit';
      if (next === 'orbit') {
        orbit.target.copy(camera.position).add(new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion).multiplyScalar(Math.max(radius * .1, .1)));
        orbit.update();
      } else { walkHeight = camera.position.y; }
      callbacks.mode?.(mode);
    },
    reset() {
      if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
      camera.position.copy(startingPosition); camera.quaternion.copy(startingQuaternion);
      orbit.target.copy(startingTarget); orbit.update();
      walkHeight = camera.position.y;
    },
    dispose() {
      cleanup();
    }
  };
}
