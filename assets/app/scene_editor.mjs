import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { moveScenePlayer } from './scene_collision.mjs';
import { generateCollisionRoom } from './scene_auto_proxy.mjs';

const clone = value => JSON.parse(JSON.stringify(value));
const degrees = value => THREE.MathUtils.radToDeg(value);
const radians = value => THREE.MathUtils.degToRad(value);
const id = () => globalThis.crypto?.randomUUID?.() || `item-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const assetKey = asset => `${asset.kind}:${asset.ref}`;
const finiteVector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
const formatNumber = value => String(Number(Number(value).toFixed(5)));

// Spark can release the original splats after building LoD. Its public bounds
// method then sees an empty source even though the LoD tree renders normally.
export function sceneModelBounds(mesh) {
  const bounds = mesh.getBoundingBox();
  if (!bounds.isEmpty()) return bounds;
  const lod = mesh.splats?.lodSplats || mesh.extSplats?.lodSplats || mesh.packedSplats?.lodSplats;
  lod?.forEachSplat?.((_index, center) => {
    if ([center.x, center.y, center.z].every(Number.isFinite)) bounds.expandByPoint(center);
  });
  return bounds;
}

// History stores the scene, never the server's optimistic-lock revision.
export class SceneHistory {
  constructor(limit = 80) { this.limit = limit; this.past = []; this.future = []; }
  push(value) { this.past.push(clone(value)); if (this.past.length > this.limit) this.past.shift(); this.future = []; }
  undo(current) { if (!this.past.length) return null; this.future.push(clone(current)); return this.past.pop(); }
  redo(current) { if (!this.future.length) return null; this.past.push(clone(current)); return this.future.pop(); }
}

export function createSceneObject(asset) {
  const rotation = new THREE.Euler();
  if (finiteVector(asset.world_up)) {
    const up = new THREE.Vector3(...asset.world_up);
    if (up.lengthSq() > 1e-10) rotation.setFromQuaternion(new THREE.Quaternion().setFromUnitVectors(up.normalize(), new THREE.Vector3(0, 1, 0)));
  }
  return { id: id(), name: asset.label || asset.ref, asset: { kind: asset.kind, ref: asset.ref },
    position: [0, 0, 0], rotation: [rotation.x, rotation.y, rotation.z], scale: 1, visible: true };
}

export function createSceneCollider(kind, target = [0, 0, 0]) {
  const floor = kind === 'floor';
  return { id: id(), name: floor ? 'พื้น' : 'กำแพง', kind: floor ? 'floor' : 'wall',
    position: [target[0], floor ? -.1 : 1.5, target[2]], yaw: 0,
    size: floor ? [10, .2, 10] : [5, 3, .2], enabled: true };
}

export class SceneEditor {
  constructor(config) {
    this.config = config;
    this.history = new SceneHistory(); this.assets = []; this.chosenAssets = new Set(); this.runtime = new Map(); this.colliderRuntime = new Map();
    this.selected = null; this.doc = null; this.revision = 0; this.dirty = false; this.ready = false; this.mutations = 0; this.saving = false;
    this.renderer = null; this.controls = null; this.scene = new THREE.Scene(); this.scene.background = new THREE.Color(0x10171e);
    this.camera = new THREE.PerspectiveCamera(55, 1, .001, 100000); this.camera.position.set(8, 6, 10);
    this.keys = new Set(); this.walking = false; this.walkYaw = 0; this.walkPitch = 0; this.fallSpeed = 0; this.lastFrame = null; this.windowFocused = typeof document.hasFocus !== 'function' || document.hasFocus();
    this.loadQueue = []; this.loading = false; this.loadCount = 0; this.loadErrors = new Map(); this.pointer = null;
    this.transformMode = 'translate'; this.transformSpace = 'world'; this.transformAxis = null; this.transformDrag = null;
    this.previewHome = { position: [8, 6, 10], target: [0, 0, 0] };
    this.$ = selector => document.querySelector(selector);
    this.canvas = this.$('#scene-canvas'); this.viewport = this.$('#scene-viewport');
    this.createFields(); this.bind();
  }

  createFields() {
    for (const [container, prefix, title] of [['position-fields', 'position', 'ตำแหน่ง'], ['rotation-fields', 'rotation', 'มุมหมุน (องศา)'], ['size-fields', 'size', 'ขนาดกล่องตรวจชน']]) {
      const parent = this.$(`#${container}`); const heading = document.createElement('span'); heading.className = 'vector-heading'; heading.textContent = title; parent.append(heading);
      const row = document.createElement('div'); row.className = 'vector-inputs'; parent.append(row);
      ['X', 'Y', 'Z'].forEach((axis, index) => {
        const label = document.createElement('label'); label.textContent = axis;
        const input = document.createElement('input'); input.type = 'number'; input.step = prefix === 'rotation' ? '1' : '.01'; input.id = `${prefix}-${index}`; input.setAttribute('aria-label', `${title} ${axis}`);
        if (prefix === 'size') { input.min = '.001'; input.max = '1000000'; }
        else { input.min = '-1000000'; input.max = '1000000'; }
        input.addEventListener('change', () => this.editVector(prefix, index, input));
        const controls = document.createElement('span'); controls.className = 'axis-input-group';
        const reset = document.createElement('button'); reset.type = 'button'; reset.id = `${prefix}-reset-${index}`; reset.className = 'axis-reset'; reset.textContent = '↺';
        reset.title = `คืนค่า ${title} ${axis}`; reset.setAttribute('aria-label', reset.title); reset.addEventListener('click', () => this.resetSelected(prefix, index));
        controls.append(input, reset); label.append(controls); row.append(label);
      });
    }
  }

  bind() {
    const on = (selector, type, callback) => this.$(selector)?.addEventListener(type, callback);
    on('#assets-refresh', 'click', () => this.refreshAssets());
    on('#asset-search', 'input', () => this.renderAssets());
    on('#assets-add', 'click', () => this.addAssets());
    on('#scene-name', 'change', event => {
      const name = event.target.value.trim();
      if (!name) { event.target.value = this.doc.name; return; }
      this.change(() => { this.doc.name = name; });
    });
    on('#scene-units', 'change', event => this.change(() => { this.doc.units = event.target.value; }));
    on('#scene-save', 'click', () => this.save());
    on('#scene-download', 'click', () => this.downloadJSON());
    on('#collision-download', 'click', async () => {
      if (this.dirty && !await this.save()) return;
      const link = document.createElement('a'); link.href = `/scene-collision/${encodeURIComponent(this.config.id)}.obj`; link.download = ''; link.click();
    });
    on('#scene-undo', 'click', () => this.undo()); on('#scene-redo', 'click', () => this.redo());
    on('#fit-all', 'click', () => this.fit()); on('#fit-selected', 'click', () => this.fit(this.selected));
    on('#reset-view', 'click', () => this.resetView());
    for (const mode of ['translate', 'rotate', 'scale']) on(`#transform-${mode}`, 'click', () => this.setTransformMode(mode));
    for (const axis of ['X', 'Y', 'Z']) on(`#transform-axis-${axis.toLowerCase()}`, 'click', () => this.setTransformAxis(axis));
    on('#transform-space', 'click', () => { this.transformSpace = this.transformSpace === 'world' ? 'local' : 'world'; this.syncTransform(); });
    for (const kind of ['position', 'rotation', 'scale', 'all']) on(`#item-reset-${kind}`, 'click', () => this.resetSelected(kind));
    on('#add-floor', 'click', () => this.addCollider('floor')); on('#add-wall', 'click', () => this.addCollider('wall'));
    on('#show-grid', 'change', event => { if (this.grid) this.grid.visible = event.target.checked; });
    on('#show-colliders', 'change', () => this.syncColliders());
    on('#item-name', 'change', event => this.change(() => { const item = this.item(); if (item) item.name = event.target.value.trim() || item.name; }));
    on('#item-visible', 'change', event => this.change(() => { const item = this.item(); if (item) item[this.isCollider() ? 'enabled' : 'visible'] = event.target.checked; }));
    on('#item-scale', 'change', event => {
      const value = this.readNumber(event.target, .000001, 1000000); if (value === null || this.isCollider()) return;
      this.change(() => { this.item().scale = value; });
    });
    on('#item-duplicate', 'click', () => this.duplicate()); on('#item-remove', 'click', () => this.remove());
    on('#item-ground', 'click', () => this.groundSelected());
    on('#auto-collision', 'click', () => this.generateCollision());
    for (const button of document.querySelectorAll('[data-nudge]')) button.addEventListener('click', () => {
      const step = this.readNumber(this.$('#nudge-step'), .001, 10000); if (step === null || !this.item()) return;
      const [axis, direction] = button.dataset.nudge.split(',').map(Number);
      this.change(() => { this.item().position[axis] += step * direction; });
    });
    for (const key of ['height', 'radius', 'speed']) on(`#player-${key}`, 'change', event => {
      const value = this.readNumber(event.target, key === 'height' ? .1 : .01, key === 'radius' ? 50 : 100); if (value === null) return;
      this.change(() => { this.doc.player[key] = value; });
    });
    on('#spawn-set', 'click', () => this.change(() => {
      const direction = this.camera.getWorldDirection(new THREE.Vector3());
      this.doc.spawn = { position: this.camera.position.toArray(), yaw: Math.atan2(-direction.x, -direction.z) };
    }));
    on('#walk-toggle', 'click', () => this.setWalking(!this.walking));
    this.canvas.addEventListener('pointerdown', event => {
      this.canvas.focus({ preventScroll: true });
      this.pointer = { x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY, button: event.button, moved: false, gizmo: !!this.transform?.axis || !!this.transform?.dragging };
      if (this.walking) this.canvas.setPointerCapture?.(event.pointerId);
    });
    this.canvas.addEventListener('pointermove', event => {
      if (!this.walking || (!this.pointer && document.pointerLockElement !== this.canvas)) return;
      const locked = document.pointerLockElement === this.canvas;
      const dx = locked ? event.movementX : event.clientX - this.pointer.lastX;
      const dy = locked ? event.movementY : event.clientY - this.pointer.lastY;
      if (this.pointer) { this.pointer.lastX = event.clientX; this.pointer.lastY = event.clientY; if (Math.abs(event.clientX - this.pointer.x) + Math.abs(event.clientY - this.pointer.y) > 4) this.pointer.moved = true; }
      this.walkYaw -= dx * .003; this.walkPitch = Math.max(-1.48, Math.min(1.48, this.walkPitch - dy * .003)); this.applyLook();
    });
    this.canvas.addEventListener('pointerup', event => {
      const pointer = this.pointer; this.pointer = null; if (!pointer || pointer.button !== 0) return;
      if (this.walking) {
        if (!pointer.moved && document.pointerLockElement !== this.canvas) {
          try { this.canvas.requestPointerLock?.()?.catch?.(() => this.status('ล็อกเมาส์ไม่ได้ ใช้การลากเพื่อมองได้')); }
          catch { this.status('ล็อกเมาส์ไม่ได้ ใช้การลากเพื่อมองได้'); }
        }
      } else if (!pointer.gizmo && !this.transform?.dragging && !this.transformDrag && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) < 5) this.pick(event);
    });
    this.canvas.addEventListener('pointercancel', () => { this.pointer = null; this.cancelTransform(); });
    this.canvas.addEventListener('blur', () => { this.keys.clear(); this.pointer = null; });
    addEventListener('keydown', event => this.keyDown(event));
    addEventListener('keyup', event => this.keys.delete(event.code));
    addEventListener('blur', () => { this.windowFocused = false; this.keys.clear(); this.pointer = null; this.lastFrame = null; this.cancelTransform(); });
    addEventListener('focus', () => { this.windowFocused = true; this.lastFrame = null; });
    document.addEventListener('visibilitychange', () => { if (document.hidden) { this.keys.clear(); this.lastFrame = null; } });
    document.addEventListener('pointerlockchange', () => { if (document.pointerLockElement !== this.canvas) this.keys.clear(); });
    document.addEventListener('pointerlockerror', () => this.status('ล็อกเมาส์ไม่ได้ ใช้การลากเพื่อมองได้'));
    addEventListener('beforeunload', event => { if (this.dirty) { event.preventDefault(); event.returnValue = ''; } });
  }

  async start() {
    try {
      const [response] = await Promise.all([fetch(`/api/scenes/${encodeURIComponent(this.config.id)}`), this.refreshAssets()]);
      if (!response.ok) throw new Error(`เปิดฉากไม่ได้ (${response.status})`);
      this.doc = await response.json(); this.revision = this.doc.revision;
      this.doc.objects ||= []; this.doc.colliders ||= []; this.doc.player ||= { height: 1.65, radius: .25, speed: 1.4 };
      this.doc.spawn ||= { position: [0, 1.65, 3], yaw: 0 }; this.doc.units ||= 'relative';
      this.ready = true; this.renderUI();
      try {
        THREE.ColorManagement.workingColorSpace = THREE.SRGBColorSpace;
        this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: false }); this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2)); this.renderer.toneMapping = THREE.NoToneMapping;
        const { SparkRenderer, SplatMesh } = await import('@sparkjsdev/spark'); this.SplatMesh = SplatMesh;
        this.spark = new SparkRenderer({ renderer: this.renderer, preBlurAmount: .3, blurAmount: 0, sortRadial: false, maxStdDev: Math.sqrt(12), minAlpha: 1 / 255, enableLod: true, focalAdjustment: 1 }); this.scene.add(this.spark);
        this.controls = new OrbitControls(this.camera, this.canvas); this.controls.enableDamping = true; this.controls.dampingFactor = .12;
        this.controls.target.set(0, 0, 0); this.controls.minDistance = .001; this.controls.maxDistance = 100000;
        this.transform = new TransformControls(this.camera, this.canvas); this.transform.setSize(.85); this.scene.add(this.transform.getHelper());
        this.transform.addEventListener('dragging-changed', event => {
          if (event.value) this.beginTransform(); else this.endTransform();
        });
        this.transform.addEventListener('objectChange', () => this.readTransform());
        this.grid = new THREE.GridHelper(100, 100, 0x456148, 0x25323c); this.grid.material.transparent = true; this.grid.material.opacity = .65; this.scene.add(this.grid);
        this.selectionBox = new THREE.Box3Helper(new THREE.Box3(), 0xb6e674); this.selectionBox.visible = false; this.scene.add(this.selectionBox);
        if (finiteVector(this.doc.view?.position) && finiteVector(this.doc.view?.target)) { this.camera.position.fromArray(this.doc.view.position); this.controls.target.fromArray(this.doc.view.target); }
        this.previewHome = { position: this.camera.position.toArray(), target: this.controls.target.toArray() };
        this.controls.update(); this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(this.viewport); this.resize();
        this.renderer.setAnimationLoop(time => this.frame(time));
        this.canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); this.setWalking(false); this.error('หน่วยความจำกราฟิกไม่พอหรือ WebGL หยุดทำงาน บันทึกฉาก แล้วเปิดหน้าใหม่พร้อมโมเดลน้อยลง'); });
        this.reconcile(); this.status('พร้อมจัดฉาก · คลิกเลือก ลากหมุน ลูกล้อซูม');
      } catch (error) { this.error(`เปิดภาพสามมิติไม่ได้: ${error.message} · ยังแก้รายการและบันทึกฉากได้`); this.status('ภาพสามมิติไม่พร้อม'); }
      this.renderUI();
    } catch (error) { this.error(error.message); this.$('#save-state').textContent = 'เปิดฉากไม่สำเร็จ'; this.status('เปิดฉากไม่สำเร็จ'); }
  }

  async refreshAssets() {
    this.$('#assets-refresh').disabled = true;
    try {
      const response = await fetch('/api/scene-assets'); if (!response.ok) throw new Error(`อ่านคลังโมเดลไม่ได้ (${response.status})`);
      const data = await response.json(); this.assets = data.assets || [];
      const keys = new Set(this.assets.map(assetKey)); this.chosenAssets = new Set([...this.chosenAssets].filter(key => keys.has(key)));
      this.renderAssets();
      // A previously unavailable source may have returned to the library.
      for (const [key, entry] of this.runtime) if (entry.missing) { this.scene.remove(entry.group); this.runtime.delete(key); }
      if (this.ready) this.reconcile();
    } catch (error) { this.error(error.message); this.$('#asset-list').textContent = 'โหลดคลังไม่สำเร็จ กด ↻ เพื่อลองอีกครั้ง'; }
    finally { this.$('#assets-refresh').disabled = false; }
  }

  renderAssets() {
    const query = this.$('#asset-search').value.trim().toLocaleLowerCase(); const list = this.$('#asset-list'); list.replaceChildren();
    const assets = this.assets.filter(asset => `${asset.label} ${asset.ref} ${asset.kind}`.toLocaleLowerCase().includes(query));
    if (!assets.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = this.assets.length ? 'ไม่พบโมเดลที่ค้นหา' : 'ยังไม่มีโมเดล PLY / SPZ ในคลัง'; list.append(empty); }
    for (const asset of assets) {
      const row = document.createElement('label'); row.className = 'asset-row';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = this.chosenAssets.has(assetKey(asset)); checkbox.setAttribute('aria-label', `เลือก ${asset.label} ${asset.kind.toUpperCase()}`);
      checkbox.addEventListener('change', () => { if (checkbox.checked) this.chosenAssets.add(assetKey(asset)); else this.chosenAssets.delete(assetKey(asset)); this.updateAddButton(); });
      const text = document.createElement('span'); const name = document.createElement('strong'); name.textContent = asset.label; const detail = document.createElement('small'); detail.textContent = `${asset.kind.toUpperCase()} · ${(Number(asset.bytes || 0) / 1048576).toFixed(1)} MB`; text.append(name, detail); row.append(checkbox, text); list.append(row);
    }
    this.updateAddButton();
  }

  updateAddButton() { this.$('#assets-add').disabled = !this.ready || this.walking || !this.chosenAssets.size; this.$('#assets-add').textContent = this.chosenAssets.size ? `เพิ่ม ${this.chosenAssets.size} โมเดลที่เลือก` : 'เพิ่มโมเดลที่เลือก'; }

  addAssets() {
    if (!this.ready || this.walking) return;
    const assets = this.assets.filter(asset => this.chosenAssets.has(assetKey(asset))); if (!assets.length) return;
    if (this.doc.objects.length + assets.length > 128) { this.error('ฉากรองรับได้สูงสุด 128 โมเดล กรุณาลดจำนวนที่เลือก'); return; }
    this.fitWhenLoaded = this.doc.objects.length === 0 && this.doc.colliders.length === 0;
    this.change(() => { for (const asset of assets) { const object = createSceneObject(asset); this.doc.objects.push(object); this.selected = object.id; } });
    this.chosenAssets.clear(); this.renderAssets();
    this.status('เพิ่มโมเดลแล้ว · ปรับตำแหน่งและสเกลให้สถานที่ต่อกัน');
  }

  addCollider(kind) {
    if (this.doc.colliders.length >= 512) { this.error('ฉากรองรับได้สูงสุด 512 กล่องตรวจชน'); return; }
    const collider = createSceneCollider(kind, this.controls?.target.toArray() || [0, 0, 0]);
    this.change(() => {
      if (kind === 'floor' && !this.doc.colliders.some(item => item.kind === 'floor' && item.enabled)) this.doc.spawn = { position: [collider.position[0], collider.position[1] + collider.size[1] / 2 + this.doc.player.height, collider.position[2]], yaw: this.doc.spawn.yaw };
      this.doc.colliders.push(collider); this.selected = collider.id;
    });
    if (kind === 'floor') this.status('เพิ่มพื้นบนระดับ Y = 0 แล้ว · ปรับขนาดให้ครอบโมเดลก่อนทดลองเดิน');
  }

  generateCollision() {
    if (this.walking || this.transformDrag || !this.item() || this.isCollider()) return;
    const bounds = this.boundsFor(this.selected);
    if (!bounds || bounds.isEmpty()) { this.error('รอให้โมเดลโหลดก่อนสร้างพื้นและกำแพง'); return; }
    try {
      const colliders = generateCollisionRoom({ min: bounds.min.toArray(), max: bounds.max.toArray() });
      if (this.doc.colliders.length + colliders.length > 512) { this.error('ฉากรองรับได้สูงสุด 512 กล่องตรวจชน'); return; }
      const floor = colliders[0];
      this.change(() => {
        this.doc.colliders.push(...colliders);
        this.doc.spawn = { position: [floor.position[0], floor.position[1] + floor.size[1] / 2 + this.doc.player.height, floor.position[2]], yaw: this.doc.spawn.yaw };
      });
      this.status('สร้างพื้นและกำแพงจากกรอบแล้ว · ปรับประตูและรอยต่อก่อนทดลองเดิน · Undo ย้อนกลับได้');
    } catch (error) { this.error('สร้างกรอบตรวจชนไม่ได้ กรุณาตรวจจุดฟุ้งและปรับสเกลโมเดล · ' + error.message); }
  }

  item() { return this.doc && [...this.doc.objects, ...this.doc.colliders].find(item => item.id === this.selected); }
  isCollider() { return !!this.doc?.colliders.some(item => item.id === this.selected); }
  select(value) { this.cancelTransform(); this.selected = value; this.renderObjectList(); this.renderInspector(); this.updateSelectionBox(); this.syncTransform(); }
  historyValue() { return { doc: this.doc, selected: this.selected }; }
  change(mutate) {
    if (!this.ready || this.walking) return;
    if (this.transformDrag) this.endTransform();
    this.history.push(this.historyValue()); mutate(); this.mutations++; this.dirty = true; this.reconcile(); this.renderUI();
  }
  restore(value) {
    if (!value) return; this.cancelTransform(); this.doc = value.doc; this.doc.revision = this.revision; this.selected = value.selected; this.mutations++; this.dirty = true; this.reconcile(); this.renderUI();
  }
  undo() { if (!this.walking && this.ready) this.restore(this.history.undo(this.historyValue())); }
  redo() { if (!this.walking && this.ready) this.restore(this.history.redo(this.historyValue())); }
  duplicate() {
    const source = this.item(); if (!source) return; const collider = this.isCollider();
    if ((collider ? this.doc.colliders.length >= 512 : this.doc.objects.length >= 128)) { this.error(collider ? 'ฉากรองรับได้สูงสุด 512 กล่องตรวจชน' : 'ฉากรองรับได้สูงสุด 128 โมเดล'); return; }
    this.change(() => { const copy = clone(source); copy.id = id(); copy.name += ' · สำเนา'; copy.position[0] += Number(this.$('#nudge-step').value) || .1; (collider ? this.doc.colliders : this.doc.objects).push(copy); this.selected = copy.id; });
  }
  remove() {
    if (!this.item()) return; this.change(() => { this.doc.objects = this.doc.objects.filter(item => item.id !== this.selected); this.doc.colliders = this.doc.colliders.filter(item => item.id !== this.selected); this.selected = null; });
  }
  readNumber(input, min = -1000000, max = 1000000) {
    const value = input.value.trim() === '' ? NaN : Number(input.value); const valid = Number.isFinite(value) && value >= min && value <= max;
    input.classList.toggle('invalid', !valid); input.setAttribute('aria-invalid', String(!valid));
    if (!valid) { this.status(`กรุณาใส่ตัวเลข ${min} ถึง ${max}`); return null; } return value;
  }
  editVector(prefix, axis, input) {
    const item = this.item(); if (!item) return;
    const value = this.readNumber(input, prefix === 'size' ? .001 : -1000000); if (value === null) return;
    this.change(() => {
      if (prefix === 'rotation') { if (this.isCollider()) { if (axis === 1) item.yaw = radians(value); } else item.rotation[axis] = radians(value); }
      else item[prefix][axis] = value;
    });
  }

  resetSelected(kind, axis = null) {
    if (!this.item() || this.walking) return;
    const item = this.item(), collider = this.isCollider();
    if (kind === 'rotation' && collider && axis !== null && axis !== 1) return;
    const defaultSize = collider ? item.kind === 'floor' ? [10, .2, 10] : [5, 3, .2] : null;
    this.change(() => {
      if (kind === 'position' || kind === 'all') { if (axis === null) item.position = [0, 0, 0]; else item.position[axis] = 0; }
      if (kind === 'rotation' || kind === 'all') {
        if (collider) item.yaw = 0; else if (axis === null) item.rotation = [0, 0, 0]; else item.rotation[axis] = 0;
      }
      if (kind === 'size' || kind === 'scale' || kind === 'all') {
        if (collider) { if (axis === null) item.size = defaultSize; else item.size[axis] = defaultSize[axis]; }
        else item.scale = 1;
      }
    });
  }

  setTransformMode(mode) {
    if (this.walking || this.transformDrag || !['translate', 'rotate', 'scale'].includes(mode)) return;
    this.transformMode = mode;
    if (this.isCollider() && mode === 'rotate' && this.transformAxis !== 'Y') this.transformAxis = null;
    this.syncTransform();
  }

  setTransformAxis(axis) {
    if (this.walking || this.transformDrag || !['X', 'Y', 'Z'].includes(axis)) return;
    if (this.isCollider() && this.transformMode === 'rotate' && axis !== 'Y') return;
    this.transformAxis = this.transformAxis === axis ? null : axis;
    this.syncTransform();
  }

  syncTransform() {
    const item = this.item(), collider = this.isCollider(), disabled = !item || this.walking || !this.transform;
    if (this.transform && !this.transformDrag) {
      if (collider && this.transformMode === 'rotate' && this.transformAxis && this.transformAxis !== 'Y') this.transformAxis = null;
      this.transform.setMode(this.transformMode); this.transform.setSpace(this.transformMode === 'scale' ? 'local' : this.transformSpace);
      const target = !this.walking && item && (collider ? this.colliderRuntime.get(item.id) : this.runtime.get(item.id)?.group);
      if (target?.visible && (collider ? item.enabled : item.visible)) { if (this.transform.object !== target) this.transform.attach(target); }
      else this.transform.detach();
      for (const axis of ['X', 'Y', 'Z']) this.transform[`show${axis}`] = (!this.transformAxis || this.transformAxis === axis) && (!collider || this.transformMode !== 'rotate' || axis === 'Y');
    }
    for (const mode of ['translate', 'rotate', 'scale']) {
      const button = this.$(`#transform-${mode}`); if (!button) continue;
      button.disabled = disabled || !!this.transformDrag; button.classList.toggle('active', this.transformMode === mode); button.setAttribute('aria-pressed', String(this.transformMode === mode));
    }
    for (const axis of ['X', 'Y', 'Z']) {
      const button = this.$(`#transform-axis-${axis.toLowerCase()}`); if (!button) continue;
      button.disabled = disabled || !!this.transformDrag || (collider && this.transformMode === 'rotate' && axis !== 'Y');
      button.classList.toggle('active', this.transformAxis === axis); button.setAttribute('aria-pressed', String(this.transformAxis === axis));
    }
    const space = this.$('#transform-space'); if (space) { space.disabled = disabled || !!this.transformDrag || this.transformMode === 'scale'; space.textContent = this.transformSpace === 'world' ? 'แกนโลก' : 'แกนวัตถุ'; space.setAttribute('aria-pressed', String(this.transformSpace === 'local')); }
  }

  beginTransform() {
    if (this.transformDrag || !this.item() || this.walking) return;
    this.transformDrag = { before: clone(this.historyValue()), dirty: this.dirty, item: clone(this.item()), mode: this.transformMode };
    if (this.pointer) this.pointer.gizmo = true;
    this.keys.clear(); this.rebuildOrbit(this.controls.target.toArray()); this.controls.enabled = false; this.renderUI();
  }

  readTransform() {
    const drag = this.transformDrag, item = this.item(), object = this.transform?.object;
    if (!drag || !item || !object) return;
    const collider = this.isCollider();
    // The document schema allows one scale for splats and yaw only for colliders.
    if (drag.mode === 'translate') item.position = object.position.toArray().map(value => THREE.MathUtils.clamp(value, -1000000, 1000000));
    if (drag.mode === 'rotate') { if (collider) item.yaw = new THREE.Euler().setFromQuaternion(object.quaternion, 'YXZ').y; else item.rotation = [object.rotation.x, object.rotation.y, object.rotation.z]; }
    if (drag.mode === 'scale') {
      if (collider) item.size = object.scale.toArray().map(value => THREE.MathUtils.clamp(value, .001, 1000000));
      else {
        const components = object.scale.toArray(), original = drag.item.scale;
        const axis = ['X', 'Y', 'Z'].findIndex(axis => this.transform.axis === axis);
        const index = axis >= 0 ? axis : components.reduce((best, value, index) => Math.abs(value - original) > Math.abs(components[best] - original) ? index : best, 0);
        item.scale = THREE.MathUtils.clamp(components[index], .000001, 1000000);
      }
    }
    if (collider) { object.position.fromArray(item.position); object.rotation.set(0, item.yaw, 0); object.scale.fromArray(item.size); object.updateMatrixWorld(true); }
    else this.applyTransform(object, item);
    this.mutations++; this.dirty = true; this.renderInspector(); this.updateSelectionBox();
  }

  endTransform() {
    const drag = this.transformDrag; if (!drag) return;
    this.transformDrag = null;
    if (this.transform?.dragging) this.transform.dragging = false;
    if (JSON.stringify(drag.item) !== JSON.stringify(this.item())) this.history.push(drag.before); else this.dirty = drag.dirty;
    if (this.controls) this.controls.enabled = !this.walking;
    this.reconcile(); this.renderUI();
  }

  cancelTransform() {
    const drag = this.transformDrag; if (!drag) return false;
    this.transformDrag = null; this.pointer = null;
    this.doc = drag.before.doc; this.doc.revision = this.revision; this.selected = drag.before.selected; this.dirty = drag.dirty; this.mutations++;
    if (this.transform) this.transform.dragging = false;
    if (this.controls) this.controls.enabled = !this.walking;
    this.reconcile(); this.renderUI(); return true;
  }

  rebuildOrbit(target) {
    this.controls?.dispose(); this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true; this.controls.dampingFactor = .12; this.controls.minDistance = .001; this.controls.maxDistance = 100000;
    this.controls.target.fromArray(target); this.controls.update();
  }

  resetView() {
    if (!this.controls) return; this.cancelTransform(); if (this.walking) this.setWalking(false);
    const view = finiteVector(this.doc.view?.position) && finiteVector(this.doc.view?.target) ? this.doc.view : this.previewHome;
    this.camera.position.fromArray(view.position); this.camera.up.set(0, 1, 0); this.camera.zoom = 1;
    this.camera.near = .001; this.camera.far = 100000; this.camera.updateProjectionMatrix(); this.rebuildOrbit(view.target);
    this.status('คืนมุมกล้องที่บันทึกไว้แล้ว · Home'); this.syncTransform();
  }

  renderUI() {
    if (!this.doc) return;
    this.$('#scene-name').value = this.doc.name; this.$('#scene-units').value = this.doc.units;
    this.$('#scene-name').disabled = this.walking; this.$('#scene-units').disabled = this.walking;
    for (const name of ['scene-download', 'collision-download', 'add-floor', 'add-wall']) this.$(`#${name}`).disabled = !this.ready || this.walking || !!this.transformDrag;
    for (const name of ['fit-all', 'walk-toggle', 'spawn-set']) this.$(`#${name}`).disabled = !this.controls;
    this.$('#scene-undo').disabled = !this.history.past.length || this.walking || !!this.transformDrag; this.$('#scene-redo').disabled = !this.history.future.length || this.walking || !!this.transformDrag;
    this.$('#scene-save').disabled = !this.ready || this.saving || !!this.transformDrag; this.$('#scene-save').textContent = this.saving ? 'กำลังบันทึก…' : 'บันทึกฉาก';
    this.$('#save-state').textContent = this.saving ? 'กำลังบันทึก…' : this.dirty ? 'มีการแก้ไขที่ยังไม่บันทึก' : `บันทึกแล้ว · รุ่น ${this.revision}`;
    this.$('#scene-empty').hidden = !!this.doc.objects.length || !!this.doc.colliders.length;
    for (const key of ['height', 'radius', 'speed']) { this.$(`#player-${key}`).value = formatNumber(this.doc.player[key]); this.$(`#player-${key}`).disabled = this.walking; }
    this.$('#spawn-set').disabled = !this.controls || this.walking;
    this.$('#spawn-label').textContent = this.doc.spawn ? `จุดเริ่ม X ${formatNumber(this.doc.spawn.position[0])} · Y ${formatNumber(this.doc.spawn.position[1])} · Z ${formatNumber(this.doc.spawn.position[2])}` : '';
    this.$('#reset-view').disabled = !this.controls;
    this.renderObjectList(); this.renderInspector(); this.updateAddButton(); this.updateStats(); this.syncTransform();
  }

  renderObjectList() {
    if (!this.doc) return; const list = this.$('#object-list'); list.replaceChildren(); const all = [...this.doc.objects, ...this.doc.colliders];
    this.$('#object-count').textContent = all.length;
    if (!all.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'เริ่มจากเพิ่มโมเดลในคลัง'; list.append(empty); }
    for (const item of all) {
      const collider = this.doc.colliders.includes(item); const enabled = collider ? item.enabled : item.visible; const entry = this.runtime.get(item.id);
      const row = document.createElement('div'); row.className = `object-row${item.id === this.selected ? ' selected' : ''}`;
      const icon = document.createElement('span'); icon.className = 'object-icon'; icon.textContent = collider ? item.kind === 'floor' ? '▱' : '▥' : '◇';
      const select = document.createElement('button'); select.className = 'object-select'; select.textContent = item.name; select.setAttribute('aria-pressed', String(item.id === this.selected)); select.disabled = this.walking;
      const detail = document.createElement('small'); detail.textContent = collider ? `${item.kind === 'floor' ? 'พื้น' : 'กำแพง'} · ${enabled ? 'ตรวจชน' : 'ปิดตรวจชน'}` : entry?.error ? 'โหลดไม่สำเร็จ' : entry?.mesh ? `${item.asset.kind.toUpperCase()} · พร้อม` : enabled ? 'รอโหลด…' : 'ซ่อน'; select.append(detail); select.addEventListener('click', () => this.select(item.id));
      const visibility = document.createElement('button'); visibility.className = 'object-visibility'; visibility.textContent = enabled ? '◉' : '○'; visibility.title = enabled ? 'ซ่อน / ปิดตรวจชน' : 'แสดง / เปิดตรวจชน'; visibility.setAttribute('aria-label', `${visibility.title} ${item.name}`); visibility.setAttribute('aria-pressed', String(enabled)); visibility.disabled = this.walking;
      visibility.addEventListener('click', () => this.change(() => { item[collider ? 'enabled' : 'visible'] = !enabled; })); row.append(icon, select, visibility); list.append(row);
    }
  }

  renderInspector() {
    const item = this.item(); const collider = this.isCollider(); this.$('#selection-title').textContent = item ? item.name : 'เลือกสิ่งที่ต้องการปรับ'; this.$('#selection-empty').hidden = !!item;
    this.$('#selection-fields').hidden = !item; this.$('#selection-fields').disabled = !item || this.walking || !!this.transformDrag;
    this.$('#fit-selected').disabled = !item || !this.controls || this.walking;
    for (const button of document.querySelectorAll('[data-nudge]')) button.disabled = !item || this.walking;
    this.$('#item-ground').disabled = !item || collider || !this.runtime.get(item?.id)?.bounds || this.walking;
    this.$('#auto-collision').disabled = !item || collider || !this.runtime.get(item?.id)?.bounds || this.walking || !!this.transformDrag;
    this.$('#auto-collision-group').hidden = !item || collider;
    if (!item) return;
    this.$('#item-name').value = item.name; this.$('#item-visible').checked = collider ? item.enabled : item.visible;
    for (let axis = 0; axis < 3; axis++) {
      this.$(`#position-${axis}`).value = formatNumber(item.position[axis]);
      const rotation = this.$(`#rotation-${axis}`); rotation.value = formatNumber(degrees(collider ? axis === 1 ? item.yaw : 0 : item.rotation[axis])); rotation.disabled = collider && axis !== 1;
      this.$(`#rotation-reset-${axis}`).disabled = collider && axis !== 1;
      this.$(`#size-${axis}`).value = formatNumber(collider ? item.size[axis] : 1);
    }
    this.$('#size-fields').hidden = !collider; this.$('#scale-fields').hidden = collider; this.$('#item-scale').value = formatNumber(collider ? 1 : item.scale);
    this.$('#item-reset-scale').textContent = collider ? 'คืนขนาดกล่อง' : 'คืนสเกล';
    const entry = this.runtime.get(item.id);
    this.$('#item-load-status').textContent = collider ? 'ขนาดเต็มของกล่อง · ตำแหน่งอยู่กึ่งกลางกล่อง · หมุนได้รอบแกน Y' : entry?.error ? `โหลดไม่ได้: ${entry.error} · ซ่อนแล้วแสดงเพื่อลองอีกครั้ง` : entry?.mesh ? `${Number(entry.mesh.numSplats || 0).toLocaleString()} splats · ปรับแนวตั้งจากข้อมูลต้นทางเมื่อเพิ่ม` : 'กำลังรอโหลดโมเดล';
  }

  reconcile() {
    if (!this.doc || !this.SplatMesh) return;
    const active = new Set(this.doc.objects.map(object => object.id));
    for (const [key, entry] of this.runtime) if (!active.has(key)) { this.dropRuntime(entry); this.runtime.delete(key); this.loadErrors.delete(key); }
    for (const object of this.doc.objects) {
      let entry = this.runtime.get(object.id);
      if (entry && assetKey(entry.asset) !== assetKey(object.asset)) { this.dropRuntime(entry); this.runtime.delete(object.id); entry = null; }
      // Hidden objects release their decoded GPU data. Showing loads it again.
      if (!object.visible) { if (entry) { this.dropRuntime(entry); this.runtime.delete(object.id); } this.loadErrors.delete(object.id); continue; }
      if (!entry) {
        const group = new THREE.Group(); group.name = object.name; group.userData.sceneObject = object.id; this.scene.add(group);
        entry = { group, asset: clone(object.asset), mesh: null, bounds: null, cancelled: false }; this.runtime.set(object.id, entry);
        this.loadQueue.push({ objectId: object.id, entry });
      }
      this.applyTransform(entry.group, object);
    }
    this.syncColliders(); this.updateSelectionBox(); this.syncTransform(); this.runLoadQueue();
  }

  dropRuntime(entry) {
    entry.cancelled = true; this.scene.remove(entry.group);
    // Spark can still be decoding; the loader disposes cancelled results on completion.
    if (entry.mesh) { entry.mesh.dispose?.(); entry.mesh = null; }
  }

  applyTransform(group, object) { group.position.fromArray(object.position); group.rotation.set(...object.rotation); group.scale.setScalar(object.scale); group.updateMatrixWorld(true); }

  async runLoadQueue() {
    if (this.loading) return; this.loading = true;
    while (this.loadQueue.length) {
      const { objectId, entry } = this.loadQueue.shift(); if (entry.cancelled || this.runtime.get(objectId) !== entry) continue;
      const object = this.doc.objects.find(item => item.id === objectId); if (!object) continue;
      const asset = this.assets.find(item => assetKey(item) === assetKey(object.asset));
      if (!asset) { entry.missing = true; entry.error = 'ไม่พบไฟล์ต้นทางในคลัง'; this.loadErrors.set(objectId, entry.error); this.renderObjectList(); this.renderInspector(); continue; }
      this.status(`กำลังโหลด ${object.name}…`);
      let mesh;
      try {
        mesh = new this.SplatMesh({ url: asset.url, fileType: asset.kind, extSplats: true, lod: true, enableLod: true,
          onProgress: progress => { if (entry.cancelled) return; const total = progress.total || asset.bytes; this.status(`กำลังโหลด ${object.name}${total ? ` · ${Math.min(100, Math.round(Number(progress.loaded || 0) / total * 100))}%` : '…'}`); } });
        await mesh.initialized;
        if (entry.cancelled || this.runtime.get(objectId) !== entry) { mesh.dispose?.(); continue; }
        entry.mesh = mesh; entry.bounds = sceneModelBounds(mesh); entry.sourceCount = Number(asset.splats) || Number(mesh.numSplats) || 0; entry.group.add(mesh); entry.group.updateMatrixWorld(true); this.loadErrors.delete(objectId); this.loadCount++;
        this.updateSelectionBox(); this.renderObjectList(); this.renderInspector(); this.updateStats(); this.syncTransform();
      } catch (error) {
        mesh?.dispose?.(); if (entry.cancelled) continue; entry.error = error.message || 'อ่านโมเดลไม่ได้'; this.loadErrors.set(objectId, entry.error);
        this.error(`${object.name}: โหลดโมเดลไม่ได้ (${entry.error})\nลองซ่อนแล้วแสดงโมเดลอีกครั้ง หรือเลือก SPZ รุ่นเล็ก`); this.renderObjectList(); this.renderInspector();
      }
    }
    this.loading = false;
    if (this.fitWhenLoaded) { this.fitWhenLoaded = false; this.fit(); }
    this.status(this.loadErrors.size ? `โหลดไม่ได้ ${this.loadErrors.size} โมเดล · ดูรายละเอียดในรายการที่เลือก` : 'พร้อมจัดฉาก · คลิกเลือกกรอบ ลากหมุน ลูกล้อซูม');
  }

  syncColliders() {
    if (!this.doc || !this.renderer) return; const active = new Set(this.doc.colliders.map(item => item.id));
    for (const [key, mesh] of this.colliderRuntime) if (!active.has(key)) { this.scene.remove(mesh); mesh.geometry.dispose(); mesh.material.dispose(); this.colliderRuntime.delete(key); }
    for (const item of this.doc.colliders) {
      let mesh = this.colliderRuntime.get(item.id);
      if (!mesh) { mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ color: item.kind === 'floor' ? 0x83c65b : 0xf2be74, transparent: true, opacity: .18, depthWrite: false, side: THREE.DoubleSide })); this.scene.add(mesh); this.colliderRuntime.set(item.id, mesh); }
      mesh.position.fromArray(item.position); mesh.rotation.set(0, item.yaw, 0); mesh.scale.fromArray(item.size); mesh.visible = item.enabled && this.$('#show-colliders').checked;
      mesh.material.opacity = item.id === this.selected ? .35 : .18; mesh.updateMatrixWorld(true);
    }
    this.updateSelectionBox();
    this.syncTransform();
  }

  boundsFor(objectId) {
    const item = [...this.doc.objects, ...this.doc.colliders].find(item => item.id === objectId); if (!item) return null;
    if (this.doc.colliders.includes(item)) { const mesh = this.colliderRuntime.get(item.id); if (!mesh) return null; mesh.updateMatrixWorld(true); return new THREE.Box3(new THREE.Vector3(-.5, -.5, -.5), new THREE.Vector3(.5, .5, .5)).applyMatrix4(mesh.matrixWorld); }
    const entry = this.runtime.get(item.id); if (!entry?.bounds || !item.visible) return null; entry.group.updateMatrixWorld(true); return entry.bounds.clone().applyMatrix4(entry.group.matrixWorld);
  }

  allBounds() {
    const bounds = new THREE.Box3();
    for (const item of [...this.doc.objects.filter(item => item.visible), ...this.doc.colliders.filter(item => item.enabled)]) { const box = this.boundsFor(item.id); if (box && !box.isEmpty()) bounds.union(box); }
    return bounds;
  }

  updateSelectionBox() {
    if (!this.selectionBox) return; const box = this.selected && this.boundsFor(this.selected); this.selectionBox.visible = !!box && !box.isEmpty() && !this.walking;
    if (box) this.selectionBox.box.copy(box); if (this.selectionBox.visible) this.selectionBox.updateMatrixWorld(true);
  }

  fit(objectId) {
    if (!this.controls || this.walking) return; const box = objectId ? this.boundsFor(objectId) : this.allBounds();
    if (!box || box.isEmpty()) { this.status('รอให้โมเดลโหลดเสร็จ หรือเลือกโมเดลที่แสดงอยู่'); return; }
    const center = box.getCenter(new THREE.Vector3()); const radius = Math.max(box.getSize(new THREE.Vector3()).length() * .5, .01);
    const direction = this.camera.position.clone().sub(this.controls.target).normalize(); if (direction.lengthSq() < .1) direction.set(1, .6, 1).normalize();
    const angle = Math.min(radians(this.camera.fov) / 2, Math.atan(Math.tan(radians(this.camera.fov) / 2) * this.camera.aspect));
    this.camera.position.copy(center).addScaledVector(direction, radius / Math.sin(angle) * 1.15); this.controls.target.copy(center); this.camera.near = Math.max(radius / 10000, .00001); this.camera.far = Math.max(radius * 100, 1000); this.camera.updateProjectionMatrix(); this.controls.update();
  }

  groundSelected() {
    if (!this.item() || this.isCollider()) return; const box = this.boundsFor(this.selected); if (!box || box.isEmpty()) return;
    this.change(() => { this.item().position[1] -= box.min.y; });
  }

  pick(event) {
    if (!this.controls || !this.doc) return;
    const rect = this.canvas.getBoundingClientRect(); if (!rect.width || !rect.height) return;
    const raycaster = new THREE.Raycaster(); raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), this.camera);
    let chosen = null, distance = Infinity;
    // Bounding-box picking avoids a synchronous raycast over millions of splats.
    for (const item of [...this.doc.objects.filter(item => item.visible), ...this.doc.colliders.filter(item => item.enabled && this.$('#show-colliders').checked)]) {
      const box = this.boundsFor(item.id); if (!box || box.isEmpty()) continue; const hit = raycaster.ray.intersectBox(box, new THREE.Vector3());
      if (hit) { const next = hit.distanceTo(this.camera.position); if (next < distance) { chosen = item.id; distance = next; } }
    }
    this.select(chosen);
  }

  resize() {
    if (!this.renderer) return; const width = Math.max(1, this.viewport.clientWidth), height = Math.max(1, this.viewport.clientHeight);
    this.renderer.setSize(width, height, false); this.camera.aspect = width / height; this.camera.updateProjectionMatrix();
  }
  gizmoScreen() {
    const object = this.transform?.object; if (!object || this.walking) return null;
    object.updateMatrixWorld(true); this.camera.updateMatrixWorld(true);
    const origin = object.getWorldPosition(new THREE.Vector3()), camera = this.camera.getWorldPosition(new THREE.Vector3());
    const factor = origin.distanceTo(camera) * Math.min(1.9 * Math.tan(radians(this.camera.fov) / 2) / this.camera.zoom, 7) * this.transform.size / 4;
    const rect = this.canvas.getBoundingClientRect(), project = point => { const p = point.project(this.camera); return [rect.left + (p.x + 1) * rect.width / 2, rect.top + (1 - p.y) * rect.height / 2]; };
    const local = this.transformMode === 'scale' || this.transformSpace === 'local', quaternion = object.getWorldQuaternion(new THREE.Quaternion());
    const screen = { origin: project(origin.clone()), radius: factor * .5 };
    for (const [index, axis] of ['X', 'Y', 'Z'].entries()) { const offset = new THREE.Vector3().setComponent(index, factor * .45); if (local) offset.applyQuaternion(quaternion); screen[axis.toLowerCase()] = this.transform[`show${axis}`] ? project(origin.clone().add(offset)) : null; }
    return screen;
  }
  updateStats() { if (!this.doc) return; let count = 0; for (const entry of this.runtime.values()) count += Number(entry.sourceCount || entry.mesh?.numSplats || 0); this.$('#scene-stats').textContent = `${this.doc.objects.length} โมเดล · ${(count / 1000000).toFixed(2)}M splats`; }
  status(message) { this.$('#scene-status').textContent = message; }
  error(message) { this.$('#scene-error').textContent = message; this.$('#scene-error').hidden = !message; }

  keyDown(event) {
    const editing = event.target?.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(event.target?.tagName);
    if (event.code === 'Escape' && this.transformDrag) { event.preventDefault(); this.cancelTransform(); return; }
    if (event.code === 'Escape' && this.walking) { event.preventDefault(); this.setWalking(false); return; }
    if ((event.ctrlKey || event.metaKey) && event.code === 'KeyS') { event.preventDefault(); if (!this.transformDrag) this.save(); return; }
    if (editing) return;
    if (this.transformDrag) return;
    if ((event.ctrlKey || event.metaKey) && event.code === 'KeyZ') { event.preventDefault(); if (event.shiftKey) this.redo(); else this.undo(); return; }
    if (event.code === 'Home') { event.preventDefault(); this.resetView(); return; }
    if (!this.walking && !event.ctrlKey && !event.metaKey) {
      if (event.altKey) {
        const kind = { KeyG: 'position', KeyR: 'rotation', KeyS: 'scale' }[event.code];
        if (kind && this.item()) { event.preventDefault(); this.resetSelected(kind); } return;
      }
      const mode = { KeyW: 'translate', KeyE: 'rotate', KeyR: 'scale' }[event.code];
      if (mode) { event.preventDefault(); this.setTransformMode(mode); return; }
      const axis = { KeyX: 'X', KeyY: 'Y', KeyZ: 'Z' }[event.code];
      if (axis) { event.preventDefault(); this.setTransformAxis(axis); return; }
      if (event.code === 'KeyF') { event.preventDefault(); this.fit(this.selected); return; }
      if (event.code === 'Escape' && this.transformAxis) { event.preventDefault(); this.transformAxis = null; this.syncTransform(); return; }
    }
    if (!this.walking || !this.windowFocused || document.hidden || (document.activeElement !== this.canvas && document.pointerLockElement !== this.canvas)) return;
    if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ShiftLeft', 'ShiftRight'].includes(event.code)) { event.preventDefault(); this.keys.add(event.code); }
  }

  applyLook() { this.camera.quaternion.setFromEuler(new THREE.Euler(this.walkPitch, this.walkYaw, 0, 'YXZ')); this.camera.updateMatrixWorld(true); }
  setWalking(value) {
    if (!this.controls) return; this.cancelTransform(); this.walking = value; this.keys.clear(); this.pointer = null; this.fallSpeed = 0; this.lastFrame = null;
    this.controls.enabled = !value; this.controls.enableDamping = !value;
    if (value) {
      this.camera.position.fromArray(this.doc.spawn.position); this.walkYaw = this.doc.spawn.yaw; this.walkPitch = 0; this.applyLook(); this.canvas.focus({ preventScroll: true });
      this.status(this.doc.colliders.some(item => item.enabled) ? 'กำลังเดิน · ใช้พื้นและกำแพงที่เปิดตรวจชน' : 'ยังไม่มีพื้นตรวจชน · กล้องจะตกลงตามแรงโน้มถ่วง กด Esc เพื่อเพิ่มพื้น');
    } else {
      if (document.pointerLockElement === this.canvas) document.exitPointerLock?.();
      const direction = this.camera.getWorldDirection(new THREE.Vector3());
      // OrbitControls retains damping deltas; rebuild it after first-person use.
      this.rebuildOrbit(this.camera.position.clone().addScaledVector(direction, 3).toArray());
      this.status('กลับสู่การจัดฉากแล้ว');
    }
    this.$('#walk-toggle').textContent = value ? '■ หยุดเดิน' : '▶ ทดลองเดิน'; this.$('#walk-toggle').classList.toggle('active', value); this.$('#walk-banner').hidden = !value; document.body.classList.toggle('walking', value); this.updateSelectionBox(); this.renderUI();
  }

  frame(time) {
    const seconds = this.lastFrame === null ? 0 : Math.max(0, Math.min((time - this.lastFrame) / 1000, .05)); this.lastFrame = time;
    const active = this.windowFocused && !document.hidden && (document.activeElement === this.canvas || document.pointerLockElement === this.canvas);
    if (this.walking && this.doc && seconds > 0 && active) {
      const forward = Number(this.keys.has('KeyW')) - Number(this.keys.has('KeyS')); const right = Number(this.keys.has('KeyD')) - Number(this.keys.has('KeyA'));
      const delta = new THREE.Vector3(-Math.sin(this.walkYaw) * forward + Math.cos(this.walkYaw) * right, 0, -Math.cos(this.walkYaw) * forward - Math.sin(this.walkYaw) * right);
      if (delta.lengthSq() > 1) delta.normalize(); const speed = this.doc.player.speed * (this.keys.has('ShiftLeft') || this.keys.has('ShiftRight') ? 2 : 1); delta.multiplyScalar(speed * seconds);
      this.fallSpeed = Math.max(-50, this.fallSpeed - 9.81 * seconds); delta.y = this.fallSpeed * seconds;
      const result = moveScenePlayer(this.camera.position.toArray(), delta.toArray(), this.doc.colliders, { radius: this.doc.player.radius, height: this.doc.player.height, stepHeight: Math.min(.3, this.doc.player.height * .2) });
      this.camera.position.fromArray(result.position); if (result.grounded) this.fallSpeed = 0;
      if (this.camera.position.y < -1000) { this.setWalking(false); this.error('ตกออกนอกพื้นที่ตรวจชนแล้ว เพิ่มหรือขยายพื้น และตั้งจุดเริ่มให้อยู่เหนือพื้น'); }
    } else if (!this.walking && !this.transformDrag) this.controls?.update();
    this.renderer?.render(this.scene, this.camera);
  }

  snapshot() {
    const payload = clone(this.doc); payload.revision = this.revision;
    if (this.controls && !this.walking) payload.view = { position: this.camera.position.toArray(), target: this.controls.target.toArray() };
    return payload;
  }

  async save() {
    if (!this.doc || this.saving || this.transformDrag) return false; const payload = this.snapshot(); const generation = this.mutations; this.saving = true; this.renderUI();
    try {
      const response = await fetch(`/api/scenes/${encodeURIComponent(this.config.id)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!response.ok) {
        if (response.status === 409) throw new Error('มีการแก้ฉากนี้จากอีกหน้าแล้ว ดาวน์โหลด JSON เพื่อเก็บงานนี้ก่อน แล้วเปิดฉากใหม่เพื่ออ่านรุ่นล่าสุด');
        let detail = ''; try { const body = await response.json(); detail = body.detail || body.error || ''; } catch {}
        throw new Error(`บันทึกไม่ได้ (${response.status})${detail ? ` · ${detail}` : ''}`);
      }
      const saved = await response.json(); this.revision = saved.revision; this.doc.revision = saved.revision; this.doc.view = payload.view;
      if (this.mutations === generation) this.dirty = false; this.error(''); this.status('บันทึกฉากแล้ว · เปิดกลับมาได้จากหน้าฉากทั้งหมด'); return true;
    } catch (error) { this.error(error.message); return false; }
    finally { this.saving = false; this.renderUI(); }
  }

  downloadJSON() {
    if (!this.doc) return; const blob = new Blob([JSON.stringify(this.snapshot(), null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `${this.config.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    this.status('ดาวน์โหลดข้อมูลจัดฉากแล้ว · ไฟล์ JSON อ้างอิงโมเดลต้นทางในคลัง');
  }
}

const configElement = typeof document !== 'undefined' && document.querySelector('#scene-config');
if (configElement) {
  const editor = new SceneEditor(JSON.parse(configElement.textContent));
  // Small, read-only inspection surface for local smoke tests and debugging.
  window.sceneEditor = Object.freeze({ snapshot: () => editor.doc ? editor.snapshot() : null, get ready() { return editor.ready; }, get loaded() { return [...editor.runtime.values()].filter(entry => entry.mesh).length; }, get walking() { return editor.walking; },
    get selected() { return editor.selected; }, get transformMode() { return editor.transformMode; }, get axis() { return editor.transformAxis; }, get dragging() { return !!editor.transformDrag; }, get gizmoScreen() { return editor.gizmoScreen(); } });
  editor.start();
}
