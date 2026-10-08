import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { placeCapturedCamera, turnAtPosition, fitCapturedFov } from './model_navigation.mjs';

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
const camera = new THREE.PerspectiveCamera(55, 1, .001, 10000);
const scene = new THREE.Scene();
// Brush optimizes encoded image RGB. Keep these colors in the same space.
THREE.ColorManagement.workingColorSpace = THREE.SRGBColorSpace;
scene.background = new THREE.Color(0x070b13);
let renderer, controls, captures = [], index = 0, focusDistance = .5, horizontalFov = 90;
let pointer = null;
const look = { pitch: 0 };

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
  const width = Math.max(1, viewport.clientWidth), height = Math.max(1, viewport.clientHeight);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  fitCapturedFov(camera, horizontalFov);
}

function setMode() {
  controls.enabled = mode.value === 'orbit';
  controls.enableDamping = controls.enabled;
  help.textContent = controls.enabled
    ? 'Orbit: ลากซ้ายหมุนรอบฉาก · ขวาเลื่อน · ซูมด้วยล้อเมาส์ · นอกเส้นทางถ่ายอาจมีส่วนที่ไม่มีข้อมูล'
    : 'ลากเพื่อหันมอง ณ ตำแหน่งเดิม · เลื่อนแถบหรือปุ่มเพื่อเดินตามจุดถ่าย · ล้อเมาส์ขยายภาพ';
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
  setMode();
}

function choosePosition(value) {
  index = Math.max(0, Math.min(captures.length - 1, Number(value)));
  const capture = captures[index];
  if (!capture) return;
  slider.value = index;
  horizontalFov = capture.fov_x || 90;
  placeCapturedCamera(camera, controls, capture, focusDistance);
  resetOrbitFrame();
  look.pitch = 0;
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
canvas.addEventListener('pointerdown', e => {
  if (mode.value !== 'capture' || e.button !== 0) return;
  pointer = { id: e.pointerId, x: e.clientX, y: e.clientY };
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener('pointermove', e => {
  if (!pointer || pointer.id !== e.pointerId || mode.value !== 'capture') return;
  turnAtPosition(camera, controls, look, e.clientX - pointer.x, e.clientY - pointer.y, focusDistance);
  pointer.x = e.clientX; pointer.y = e.clientY;
});
function releasePointer() { pointer = null; }
canvas.addEventListener('pointerup', releasePointer);
canvas.addEventListener('pointercancel', releasePointer);
canvas.addEventListener('wheel', e => {
  if (mode.value !== 'capture') return;
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
  const splats = new SplatMesh({ url: config.url, extSplats: true,
    lod: false, enableLod: false, onProgress: progress });
  scene.add(splats);
  await splats.initialized;
  return { count: splats.numSplats, label: 'Spark · Brush compatible', bounds: splats.getBoundingBox() };
}

function progress(e) {
  status.textContent = e.total ? `กำลังโหลด ${Math.round(e.loaded / e.total * 100)}%` : 'กำลังอ่าน Model…';
}

async function start() {
  try {
    const poseResponse = await fetch(config.cameras_url);
    const poses = poseResponse.ok ? await poseResponse.json() : { cameras: [] };
    captures = poses.cameras || [];
    const result = await loadModel();
    renderer.toneMapping = THREE.NoToneMapping;
    controls = new OrbitControls(camera, canvas);
    controls.enabled = mode.value === 'orbit';
    focusDistance = Math.max((poses.path_span || 1) * .15, .01);
    controls.minDistance = focusDistance * .02;
    controls.maxDistance = focusDistance * 20;
    camera.near = Math.max((poses.path_span || 1) * .0001, .00001);
    camera.far = Math.max((poses.scene_radius || 100) * 50, 1000);
    resize();
    if (captures.length) {
      slider.max = captures.length - 1;
      slider.disabled = false;
      choosePosition(Math.floor(captures.length / 2));
    } else {
      document.querySelector('#capture-prev').disabled = true;
      document.querySelector('#capture-next').disabled = true;
      const initial = config.initial_view;
      if (initial.transform_matrix) {
        horizontalFov = initial.fov_x || initial.fov_y || 90;
        placeCapturedCamera(camera, controls, initial, focusDistance);
        resetOrbitFrame();
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
    setMode();
    status.textContent = `พร้อม · ${Number(result.count || config.splats || 0).toLocaleString()} splats · ${result.label}`;
    renderer.setAnimationLoop(() => { if (controls.enabled) controls.update(); renderer.render(scene, camera); });
  } catch (error) {
    console.error(error);
    status.textContent = 'เปิด Model ไม่สำเร็จ';
    errorBox.style.display = 'block';
    errorBox.textContent = 'เปิด Gaussian Splat ไม่สำเร็จ\n' + (error?.message || error);
  }
}

addEventListener('resize', resize);
start();
