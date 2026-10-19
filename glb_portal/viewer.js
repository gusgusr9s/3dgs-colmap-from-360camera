import * as THREE from 'three';
import { WebGPURenderer } from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { GLTFGaussianSplatLoaderExtension } from 'three/addons/loaders/GLTFGaussianSplatLoaderExtension.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export async function createViewer(host, url, callbacks = {}) {
  host.replaceChildren();
  let disposed = false;
  const isCurrent = () => callbacks.isCurrent?.() !== false;
  const cancelled = () => ({ setMode() {}, setControls() {}, reset() {}, dispose() {} });
  const isSpz = callbacks.format === 'spz';
  let renderer;
  if (isSpz) {
    renderer = new THREE.WebGLRenderer({ antialias: false });
  } else {
    renderer = new WebGPURenderer({ antialias: false, forceWebGL: !navigator.gpu });
    try { await renderer.init(); }
    catch {
      renderer.dispose();
      renderer = new WebGPURenderer({ antialias: false, forceWebGL: true });
      await renderer.init();
    }
  }
  if (!isCurrent()) { renderer.dispose(); return cancelled(); }
  let renderQuality = Number(callbacks.settings?.renderQuality);
  if (![0.65, 1, 1.5].includes(renderQuality)) renderQuality = 1;
  function applyRenderQuality(value = renderQuality) {
    if ([0.65, 1, 1.5].includes(Number(value))) renderQuality = Number(value);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, renderQuality));
    const width = host.clientWidth, height = host.clientHeight;
    if (width && height) renderer.setSize(width, height, false);
  }
  applyRenderQuality();
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  host.append(renderer.domElement);
  renderer.domElement.tabIndex = 0;
  const scene = new THREE.Scene(); scene.background = new THREE.Color('#151e20');
  const camera = new THREE.PerspectiveCamera(65, 1, .001, 10000);
  let gltf;
  let splats;
  let spark;
  let splatPivot;
  let splatFlipped = callbacks.flipSpz === true;
  let bounds;
  try {
    const progress = e => callbacks.progress?.(e.total ? `กำลังโหลด ${Math.round(e.loaded / e.total * 100)}%` : `กำลังโหลด ${(e.loaded / 1048576).toFixed(1)} MB`);
    if (isSpz) {
      callbacks.progress?.('กำลังเตรียมตัวแสดง SPZ…');
      const { SparkRenderer, SplatMesh } = await import('@sparkjsdev/spark');
      callbacks.progress?.('กำลังเริ่ม Spark…');
      spark = new SparkRenderer({ renderer, preBlurAmount: .3, blurAmount: 0,
        sortRadial: false, maxStdDev: Math.sqrt(12), minAlpha: 1 / 255,
        enableLod: false, focalAdjustment: 1 });
      scene.add(spark);
      splats = new SplatMesh({ url, fileType: 'spz', extSplats: true,
        lod: false, enableLod: false, onProgress: progress });
      callbacks.progress?.('กำลังอ่านไฟล์ SPZ…');
      await splats.initialized;
      const sourceBounds = splats.getBoundingBox();
      const pivot = sourceBounds.getCenter(new THREE.Vector3());
      splatPivot = new THREE.Group();
      splatPivot.position.copy(pivot);
      splats.position.sub(pivot);
      splatPivot.rotation.x = splatFlipped ? Math.PI : 0;
      scene.add(splatPivot);
      splatPivot.add(splats);
      splatPivot.updateMatrixWorld(true);
      bounds = splats.getBoundingBox().applyMatrix4(splats.matrixWorld);
    } else {
      const loader = new GLTFLoader();
      loader.register(parser => new GLTFGaussianSplatLoaderExtension(parser));
      gltf = await loader.loadAsync(url, progress);
      scene.add(gltf.scene);
      bounds = new THREE.Box3().setFromObject(gltf.scene);
    }
  } catch (error) { splats?.dispose(); spark?.dispose(); renderer.dispose(); renderer.domElement.remove(); throw error; }
  if (!isCurrent()) { splats?.dispose(); spark?.dispose(); renderer.dispose(); renderer.domElement.remove(); return cancelled(); }
  const center = bounds.getCenter(new THREE.Vector3());
  const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, .01);
  camera.near = Math.max(radius / 100000, .00001);
  camera.far = Math.max(radius * 20, 1000);
  const initial = gltf?.scene.userData.initial_view || {};
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
  const initialOrbitDistance = Math.max(camera.position.distanceTo(target), radius * 1e-9, 1e-9);
  orbit.minDistance = Math.min(camera.near * 2, Math.max(initialOrbitDistance * .01, 1e-9));
  orbit.target.copy(target); orbit.update(); orbit.saveState();
  orbit.zoomSpeed = 1;
  let movementSpeed = .35;
  let lookSensitivity = .5;
  const orbitReferenceDistance = initialOrbitDistance;
  function updateOrbitPanSpeed() {
    const distance = Math.max(camera.position.distanceTo(orbit.target), radius * 1e-9, 1e-9);
    orbit.panSpeed = lookSensitivity * orbitReferenceDistance / distance;
  }
  function setControls(settings = {}) {
    const movement = Number(settings.movementSpeed);
    const look = Number(settings.lookSensitivity);
    const zoom = Number(settings.zoomSensitivity);
    applyRenderQuality(settings.renderQuality);
    if (Number.isFinite(movement)) movementSpeed = THREE.MathUtils.clamp(movement, .1, 4);
    if (Number.isFinite(look)) lookSensitivity = THREE.MathUtils.clamp(look, .1, 2);
    if (Number.isFinite(zoom)) orbit.zoomSpeed = THREE.MathUtils.clamp(zoom, .1, 2);
    orbit.rotateSpeed = lookSensitivity;
    updateOrbitPanSpeed();
  }
  setControls(callbacks.settings);
  const startingPosition = camera.position.clone();
  const startingQuaternion = camera.quaternion.clone();
  const startingTarget = target.clone();
  let mode = 'orbit';
  let walkHeight = camera.position.y;
  let lastTime = performance.now();
  const pressed = new Set();
  const touchMove = { x: 0, y: 0 };
  const rotation = new THREE.Euler(0, 0, 0, 'YXZ');
  const onKeyDown = e => {
    if (mode === 'orbit' || (document.pointerLockElement !== renderer.domElement && document.activeElement !== renderer.domElement)) return;
    if (!['KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE','ShiftLeft','ShiftRight','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.code)) return;
    pressed.add(e.code);
    e.preventDefault();
  };
  const onKeyUp = e => pressed.delete(e.code);
  const onBlur = () => pressed.clear();
  const onMouseMove = e => {
    if (document.pointerLockElement !== renderer.domElement || mode === 'orbit') return;
    rotation.setFromQuaternion(camera.quaternion, 'YXZ');
    rotation.y -= e.movementX * .002 * lookSensitivity;
    rotation.x = THREE.MathUtils.clamp(rotation.x - e.movementY * .002 * lookSensitivity, -Math.PI / 2 + .01, Math.PI / 2 - .01);
    camera.quaternion.setFromEuler(rotation);
  };
  let touchLook = null;
  const onTouchPointerDown = e => {
    if (e.pointerType !== 'touch' || mode === 'orbit') return;
    touchLook = { id: e.pointerId, x: e.clientX, y: e.clientY };
    renderer.domElement.setPointerCapture?.(e.pointerId);
    renderer.domElement.focus({ preventScroll: true });
    e.preventDefault();
  };
  const onTouchPointerMove = e => {
    if (!touchLook || touchLook.id !== e.pointerId || mode === 'orbit') return;
    rotation.setFromQuaternion(camera.quaternion, 'YXZ');
    rotation.y -= (e.clientX - touchLook.x) * .002 * lookSensitivity;
    rotation.x = THREE.MathUtils.clamp(rotation.x - (e.clientY - touchLook.y) * .002 * lookSensitivity, -Math.PI / 2 + .01, Math.PI / 2 - .01);
    camera.quaternion.setFromEuler(rotation);
    touchLook.x = e.clientX; touchLook.y = e.clientY;
    e.preventDefault();
  };
  const onTouchPointerEnd = e => { if (touchLook?.id === e.pointerId) touchLook = null; };
  const onClick = e => {
    if (mode !== 'orbit' && e.pointerType !== 'touch') {
      renderer.domElement.focus(); renderer.domElement.requestPointerLock?.();
    }
  };
  document.addEventListener('keydown', onKeyDown);
  document.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', onBlur);
  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('pointerlockchange', onBlur);
  document.addEventListener('visibilitychange', onBlur);
  renderer.domElement.addEventListener('pointerdown', onTouchPointerDown);
  renderer.domElement.addEventListener('pointermove', onTouchPointerMove);
  renderer.domElement.addEventListener('pointerup', onTouchPointerEnd);
  renderer.domElement.addEventListener('pointercancel', onTouchPointerEnd);
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
    if (mode === 'orbit') { updateOrbitPanSpeed(); orbit.update(); }
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
      move.addScaledVector(forward, -touchMove.y);
      move.addScaledVector(right, touchMove.x);
      if (mode === 'free') {
        if (pressed.has('KeyE')) move.add(up);
        if (pressed.has('KeyQ')) move.sub(up);
      }
      if (move.lengthSq()) {
        const amount = Math.min(move.length(), 1);
        camera.position.addScaledVector(move.normalize(), dt * radius * .5 * movementSpeed * amount * (pressed.has('ShiftLeft') || pressed.has('ShiftRight') ? 3 : 1));
      }
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
    document.removeEventListener('pointerlockchange', onBlur);
    document.removeEventListener('visibilitychange', onBlur);
    renderer.domElement.removeEventListener('pointerdown', onTouchPointerDown);
    renderer.domElement.removeEventListener('pointermove', onTouchPointerMove);
    renderer.domElement.removeEventListener('pointerup', onTouchPointerEnd);
    renderer.domElement.removeEventListener('pointercancel', onTouchPointerEnd);
    renderer.domElement.removeEventListener('click', onClick);
    splats?.dispose(); spark?.dispose();
    renderer.dispose(); renderer.domElement.remove();
  }
  try { if (isSpz) renderer.render(scene, camera); else await renderer.renderAsync(scene, camera); }
  catch (error) { cleanup(); throw error; }
  if (!isCurrent()) { cleanup(); return cancelled(); }
  renderer.setAnimationLoop(animate);
  let splatCount = isSpz ? splats.numSplats : 0;
  gltf?.scene.traverse(object => { if (object.isGaussianSplat) splatCount += object.geometry.instanceCount || 0; });
  callbacks.ready?.(splatCount ? `${splatCount.toLocaleString()} SPLATS · ${isSpz ? 'SPZ' : 'GLB'}` : 'MESH GLB');
  return {
    setMode(next) {
      if (!['orbit', 'walk', 'free'].includes(next)) return;
      if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
      pressed.clear();
      touchMove.x = 0; touchMove.y = 0;
      mode = next;
      orbit.enabled = next === 'orbit';
      if (next === 'orbit') {
        orbit.target.copy(camera.position).add(new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion).multiplyScalar(Math.max(radius * .1, .1)));
        updateOrbitPanSpeed();
        orbit.update();
      } else { walkHeight = camera.position.y; }
      callbacks.mode?.(mode);
    },
    reset() {
      if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
      camera.position.copy(startingPosition); camera.quaternion.copy(startingQuaternion);
      orbit.target.copy(startingTarget); orbit.update();
      updateOrbitPanSpeed();
      walkHeight = camera.position.y;
    },
    recenter() {
      if (mode !== 'orbit') return;
      const offset = camera.position.clone().sub(orbit.target);
      if (offset.lengthSq() === 0) offset.set(0, 0, 1);
      orbit.target.copy(startingTarget);
      camera.position.copy(startingTarget).add(offset);
      updateOrbitPanSpeed();
      orbit.update();
    },
    setControls,
    setTouchKey(code, down) {
      if (mode === 'orbit' || !['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE'].includes(code)) return;
      if (down) pressed.add(code); else pressed.delete(code);
    },
    setTouchMove(x, y) {
      if (mode === 'orbit') return;
      const length = Math.hypot(x, y);
      const scale = length > 1 ? 1 / length : 1;
      touchMove.x = x * scale;
      touchMove.y = y * scale;
    },
    toggleOrientation() {
      if (!splatPivot) return null;
      splatFlipped = !splatFlipped;
      splatPivot.rotation.x = splatFlipped ? Math.PI : 0;
      return splatFlipped;
    },
    dispose() {
      cleanup();
    }
  };
}
