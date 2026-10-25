import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { moveScenePlayer } from './scene_collision.mjs';
import { generateCollisionRoom } from './scene_auto_proxy.mjs';
import { sceneObjectMatrix, colliderRotation, worldCollider, bindCollider, setColliderWorld, sceneWorldColliders, applySceneCalibration } from './scene_geometry.mjs';
import { solveSceneAlignment } from './scene_alignment.mjs';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const clone = value => JSON.parse(JSON.stringify(value));
const degrees = value => THREE.MathUtils.radToDeg(value);
const radians = value => THREE.MathUtils.degToRad(value);
const id = () => globalThis.crypto?.randomUUID?.() || `item-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const assetKey = asset => `${asset.kind}:${asset.ref}`;
const finiteVector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
const formatNumber = value => String(Number(Number(value).toFixed(5)));

function disposeGLB(root) {
  const resources=new Set();root.traverse(node=>{if(node.geometry)resources.add(node.geometry);for(const material of [node.material].flat().filter(Boolean)){resources.add(material);for(const value of Object.values(material))if(value?.isTexture)resources.add(value);}});
  for(const resource of resources){if(resource.isTexture)resource.source?.data?.close?.();resource.dispose?.();}
}

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
  return { id: id(), name: asset.display_name || asset.label || asset.ref, asset: { kind: asset.kind, ref: asset.ref },
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
    this.alignment = null; this.pointMode = null; this.calibrationPoints = []; this.packageTimer = null;
    this.glbTimer=null;
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
        const reset = document.createElement('button'); reset.type = 'button'; reset.id = `${prefix}-reset-${index}`; reset.className = 'axis-reset'; reset.dataset.icon = 'reset';
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
    this.bindSceneTools();
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
      } else if (this.pointMode && !pointer.moved && Math.hypot(event.clientX-pointer.x,event.clientY-pointer.y)<5) { this.pickSceneToolPoint(event); } else if (!pointer.gizmo && !this.transform?.dragging && !this.transformDrag && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) < 5) this.pick(event);
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
        this.scene.add(new THREE.HemisphereLight(0xffffff,0x6b7785,2)); const sunlight=new THREE.DirectionalLight(0xffffff,2); sunlight.position.set(5,10,4); this.scene.add(sunlight);
        this.controls = new OrbitControls(this.camera, this.canvas); this.controls.enableDamping = true; this.controls.dampingFactor = .12;
        this.controls.target.set(0, 0, 0); this.controls.minDistance = .001; this.controls.maxDistance = 100000;
        this.transform = new TransformControls(this.camera, this.canvas); this.transform.setSize(.85); this.scene.add(this.transform.getHelper());
        this.transform.addEventListener('dragging-changed', event => {
          if (event.value) this.beginTransform(); else this.endTransform();
        });
        this.transform.addEventListener('objectChange', () => this.readTransform());
        this.grid = new THREE.GridHelper(100, 100, 0x456148, 0x25323c); this.grid.material.transparent = true; this.grid.material.opacity = .65; this.grid.visible=this.$('#show-grid').checked; this.scene.add(this.grid);
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
    const assets = this.assets.filter(asset => `${asset.display_name||asset.label} ${asset.kind} ${query.length>=8?`${asset.label} ${asset.ref}`:''}`.toLocaleLowerCase().includes(query));
    if (!assets.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = this.assets.length ? 'ไม่พบโมเดลที่ค้นหา' : 'ยังไม่มีโมเดล PLY / SPZ ในคลัง'; list.append(empty); }
    for (const asset of assets) {
      const row = document.createElement('label'); row.className = 'asset-row'; row.title=asset.label;
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = this.chosenAssets.has(assetKey(asset)); checkbox.setAttribute('aria-label', `เลือก ${asset.label} ${asset.kind.toUpperCase()}`);
      checkbox.addEventListener('change', () => { if (checkbox.checked) this.chosenAssets.add(assetKey(asset)); else this.chosenAssets.delete(assetKey(asset)); this.updateAddButton(); });
      const text = document.createElement('span'); const name = document.createElement('strong'); name.textContent = asset.display_name||asset.label; const detail = document.createElement('small'); detail.textContent = `${asset.kind.toUpperCase()} · ${(Number(asset.bytes || 0) / 1048576).toFixed(1)} MB`; text.append(name, detail); row.append(checkbox, text); list.append(row);
    }
    this.updateAddButton();
  }

  updateAddButton() { this.$('#assets-add').disabled = !this.ready || this.walking || !this.chosenAssets.size; this.$('#assets-add').textContent = this.chosenAssets.size ? `เพิ่ม ${this.chosenAssets.size} โมเดล` : 'เพิ่มโมเดลที่เลือก'; this.$('#library-selection').textContent=this.chosenAssets.size?`เลือกแล้ว ${this.chosenAssets.size} รายการ`:'เลือกโมเดลที่ต้องการเพิ่ม'; }

  addAssets() {
    if (!this.ready || this.walking) return;
    const assets = this.assets.filter(asset => this.chosenAssets.has(assetKey(asset))); if (!assets.length) return;
    if (this.doc.objects.length + assets.length > 128) { this.error('ฉากรองรับได้สูงสุด 128 โมเดล กรุณาลดจำนวนที่เลือก'); return; }
    this.fitWhenLoaded = this.doc.objects.length === 0 && this.doc.colliders.length === 0;
    this.change(() => { for (const asset of assets) { const object = createSceneObject(asset); this.doc.objects.push(object); this.selected = object.id; } });
    this.chosenAssets.clear(); this.renderAssets();
    if(typeof CustomEvent!=='undefined')document.dispatchEvent(new CustomEvent('scene-assets-added'));
    this.status('เพิ่มโมเดลแล้ว · ปรับตำแหน่งและสเกลให้สถานที่ต่อกัน');
  }

  addCollider(kind) {
    if (this.doc.colliders.length >= 512) { this.error('ฉากรองรับได้สูงสุด 512 กล่องตรวจชน'); return; }
    this.$('#show-colliders').checked=true;
    const owner = !this.isCollider() && this.item();
    const collider = createSceneCollider(kind, this.controls?.target.toArray() || [0, 0, 0]);
    this.change(() => {
      if (kind === 'floor' && !this.doc.colliders.some(item => item.kind === 'floor' && item.enabled)) this.doc.spawn = { position: [collider.position[0], collider.position[1] + collider.size[1] / 2 + this.doc.player.height, collider.position[2]], yaw: this.doc.spawn.yaw };
      this.doc.colliders.push(owner ? bindCollider(collider,owner.id,this.doc.objects) : collider); this.selected = collider.id;
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
      const floor = colliders[0], owner = this.selected;
      this.$('#show-colliders').checked=true;
      this.change(() => {
        this.doc.colliders.push(...colliders.map(item=>bindCollider(item,owner,this.doc.objects)));
        this.doc.spawn = { position: [floor.position[0], floor.position[1] + floor.size[1] / 2 + this.doc.player.height, floor.position[2]], yaw: this.doc.spawn.yaw };
      });
      this.status('สร้างพื้นและกำแพงจากกรอบแล้ว · ปรับประตูและรอยต่อก่อนทดลองเดิน · Undo ย้อนกลับได้');
    } catch (error) { this.error('สร้างกรอบตรวจชนไม่ได้ กรุณาตรวจจุดฟุ้งและปรับสเกลโมเดล · ' + error.message); }
  }

  bindSceneTools() {
    const on=(selector,type,handler)=>this.$(selector)?.addEventListener(type,handler);
    on('#collider-parent','change',event=>{if(!this.isCollider())return;this.change(()=>Object.assign(this.item(),bindCollider(this.item(),event.target.value||null,this.doc.objects)));});
    on('#glb-upload','change',event=>this.uploadGLB(event.target));
    on('#package-start','click',()=>this.startPackage());
    on('#glb-start','click',()=>this.startGLB());
    document.addEventListener('scene-export-opened',()=>this.refreshExports());
    for(const name of ['snap-enabled','snap-distance','snap-angle'])on('#'+name,'change',()=>this.updateSnap());
    on('#align-target','change',event=>{this.alignTarget=event.target.value;this.alignment=null;this.renderSceneTools();});
    on('#align-pick-source','click',()=>this.startPointPick('align-source',this.selected));
    on('#align-pick-target','click',()=>this.startPointPick('align-target',this.$('#align-target').value));
    on('#align-add','click',()=>this.addPointPair());
    on('#align-apply','click',()=>this.applyAlignment());
    on('#align-clear','click',()=>{this.alignment=null;this.stopPointPick();this.renderSceneTools();});
    on('#calibration-a','click',()=>this.startPointPick('cal-a',this.selected));
    on('#calibration-b','click',()=>this.startPointPick('cal-b',this.selected));
    on('#calibration-apply','click',()=>this.calibrate());
    for(const side of ['source','target'])for(let axis=0;axis<3;axis++) {
      const parent=this.$('#align-'+side+'-fields');if(!parent)continue;
      const label=document.createElement('label');label.textContent=['X','Y','Z'][axis];
      const input=document.createElement('input');input.type='number';input.step='.001';input.value='0';input.id=`align-${side}-${axis}`;input.setAttribute('aria-label',`${side} ${['X','Y','Z'][axis]}`);label.append(input);parent.append(label);
    }
  }

  renderParentSelector() {
    const select=this.$('#collider-parent');if(!select)return;
    const active=this.isCollider();this.$('#collider-parent-group').hidden=!active;
    if(!active)return;select.replaceChildren();
    const option=(value,label)=>{const node=document.createElement('option');node.value=value;node.textContent=label;select.append(node);};
    option('','อิสระในฉาก');for(const object of this.doc.objects)option(object.id,object.name);
    select.value=this.item().parent||'';select.disabled=this.walking||!!this.transformDrag;
  }

  renderSceneTools() {
    if(!this.doc)return;const model=this.item()&&!this.isCollider()?this.item():null;
    this.$('#alignment-source-name').textContent=model?`กำลังปรับ: ${model.name}`:'เลือกโมเดลที่จะขยับจากรายการ';
    const target=this.$('#align-target');if(!target)return;
    const current=this.alignTarget||target.value;target.replaceChildren();
    for(const object of this.doc.objects.filter(item=>item.id!==model?.id)){const option=document.createElement('option');option.value=object.id;option.textContent=object.name;target.append(option);}
    target.value=this.doc.objects.some(item=>item.id===current&&item.id!==model?.id)?current:this.doc.objects.find(item=>item.id!==model?.id)?.id||'';this.alignTarget=target.value;
    if(this.alignment&&(this.alignment.source!==model?.id||this.alignment.target!==target.value))this.alignment=null;
    const disabled=!model||this.walking||!!this.transformDrag;
    for(const name of ['align-pick-source','align-pick-target','align-add'])this.$('#'+name).disabled=disabled||!target.value;
    for(const name of ['calibration-a','calibration-b'])this.$('#'+name).disabled=disabled;
    this.$('#align-apply').disabled=disabled||(this.alignment?.pairs.length||0)<3;
    this.$('#calibration-apply').disabled=this.walking||!this.calibrationPoints[0]||!this.calibrationPoints[1];
    this.$('#align-count').textContent=`${this.alignment?.pairs.length||0}/3+ คู่`;
    const list=this.$('#align-pairs');list.replaceChildren();
    for(const [index,pair]of(this.alignment?.pairs||[]).entries()){const row=document.createElement('li');row.textContent=`คู่ ${index+1}: ${pair.source.map(formatNumber).join(', ')} ↔ ${pair.target.map(formatNumber).join(', ')}`;list.append(row);}
    this.$('#calibration-label').textContent=this.calibrationPoints.filter(Boolean).map((point,index)=>`${index?'B':'A'}: ${point.local.map(formatNumber).join(', ')}`).join(' · ');
    this.updateSnap();this.drawPointMarkers();
  }

  updateSnap() {
    if(!this.transform)return;
    const enabled=this.$('#snap-enabled')?.checked,distance=Number(this.$('#snap-distance')?.value),angle=Number(this.$('#snap-angle')?.value);
    this.transform.setTranslationSnap?.(enabled&&distance>0&&Number.isFinite(distance)?distance:null);
    this.transform.setRotationSnap?.(enabled&&angle>0&&Number.isFinite(angle)?radians(angle):null);
    this.$('#precision-label').textContent=[this.transformAxis,enabled?'Snap':null].filter(Boolean).join(' · ')||'แกน / Snap';
  }

  startPointPick(kind,object) {
    if(!object||this.walking||this.transformDrag||!this.runtime.get(object)?.mesh)return;
    this.pointMode={kind,object};this.transform?.detach();this.canvas.focus({preventScroll:true});
    this.status('คลิกจุดบนโมเดลที่ระบุ · ลากหมุนดูได้ · Esc ยกเลิก · จุดคู่ต้องเป็นบริเวณเดียวกันที่ถ่ายซ้อน');
  }
  stopPointPick(){this.pointMode=null;this.syncTransform();}

  pointOnModel(event,identifier) {
    const entry=this.runtime.get(identifier);if(!entry?.mesh)return null;
    const rect=this.canvas.getBoundingClientRect(),raycaster=new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),this.camera);
    if(entry.asset.kind==='glb'){const hit=raycaster.intersectObject(entry.mesh,true)[0];return hit?.point||null;}
    const mesh=entry.mesh,original=mesh.splats;
    const source=original?.getNumSplats?.()?original:mesh.extSplats?.lodSplats||mesh.packedSplats?.lodSplats||original;
    let best=Infinity,result=null;entry.group.updateMatrixWorld(true);const point=new THREE.Vector3();
    source?.forEachSplat?.((_index,center)=>{
      point.copy(center).applyMatrix4(entry.group.matrixWorld);const projected=point.clone().project(this.camera);if(projected.z< -1||projected.z>1)return;
      const dx=(projected.x+1)*rect.width/2-(event.clientX-rect.left),dy=(1-projected.y)*rect.height/2-(event.clientY-rect.top);
      const score=dx*dx+dy*dy+point.distanceTo(this.camera.position)*.0001;
      if(dx*dx+dy*dy<100&&score<best){best=score;result=point.clone();}
    });
    return result;
  }

  pickSceneToolPoint(event) {
    const mode=this.pointMode;if(!mode)return;const world=this.pointOnModel(event,mode.object);
    if(!world){this.status('ไม่พบจุดบนโมเดลนี้ ลองซูมเข้าและคลิกอีกครั้ง');return;}
    if(mode.kind.startsWith('align-')){const side=mode.kind.slice(6);world.toArray().forEach((value,index)=>{this.$(`#align-${side}-${index}`).value=formatNumber(value);});}
    else {const object=this.doc.objects.find(item=>item.id===mode.object);this.calibrationPoints[mode.kind==='cal-a'?0:1]={object:object.id,local:world.applyMatrix4(sceneObjectMatrix(object).invert()).toArray()};}
    this.stopPointPick();this.renderSceneTools();this.status('เลือกจุดแล้ว ตรวจพิกัดและเพิ่มคู่ หรือเลือกระยะสอบเทียบจุดถัดไป');
    if(typeof CustomEvent!=='undefined')document.dispatchEvent(new CustomEvent('scene-point-picked'));
  }

  addPointPair() {
    const moving=this.item(),target=this.doc.objects.find(item=>item.id===this.$('#align-target').value);if(!moving||this.isCollider()||!target)return;
    try{
      const points={};for(const side of ['source','target']){points[side]=[0,1,2].map(index=>{const input=this.$(`#align-${side}-${index}`);if(!input.value.trim())throw new Error('กรอกพิกัด XYZ ให้ครบ');const value=Number(input.value);if(!Number.isFinite(value))throw new Error('พิกัดต้องเป็นตัวเลข');return value;});}
      if(!this.alignment||this.alignment.source!==moving.id||this.alignment.target!==target.id)this.alignment={source:moving.id,target:target.id,pairs:[]};
      if(this.alignment.pairs.length>=24)throw new Error('รองรับได้สูงสุด 24 คู่');
      this.alignment.pairs.push({source:new THREE.Vector3(...points.source).applyMatrix4(sceneObjectMatrix(moving).invert()).toArray(),target:new THREE.Vector3(...points.target).applyMatrix4(sceneObjectMatrix(target).invert()).toArray()});
      this.renderSceneTools();
    }catch(error){this.error(error.message);}
  }

  applyAlignment() {
    const alignment=this.alignment;if(!alignment||this.walking)return;
    try{
      const moving=this.doc.objects.find(item=>item.id===alignment.source),target=this.doc.objects.find(item=>item.id===alignment.target);
      const sourceMatrix=sceneObjectMatrix(moving),targetMatrix=sceneObjectMatrix(target);
      const source=alignment.pairs.map(pair=>new THREE.Vector3(...pair.source).applyMatrix4(sourceMatrix).toArray());
      const destination=alignment.pairs.map(pair=>new THREE.Vector3(...pair.target).applyMatrix4(targetMatrix).toArray());
      const solved=solveSceneAlignment(source,destination,this.$('#align-scale').checked);
      const p=new THREE.Vector3(),q=new THREE.Quaternion(),scale=new THREE.Vector3();solved.matrix.multiply(sourceMatrix).decompose(p,q,scale);
      if(!p.toArray().every(value=>Number.isFinite(value)&&Math.abs(value)<=1e6)||!Number.isFinite(scale.x)||scale.x<1e-6||scale.x>1e6)throw new Error('ผลจัดแนวเกินขอบเขต ตรวจพิกัดจุดคู่');
      this.change(()=>{moving.position=p.toArray();const r=new THREE.Euler().setFromQuaternion(q);moving.rotation=[r.x,r.y,r.z];moving.scale=scale.x;});
      this.stopPointPick();this.status(`จัดแนวแล้ว · RMS ${solved.rms.toFixed(5)} ${this.doc.units==='meters'?'เมตร':'หน่วยฉาก'} · Undo ย้อนกลับได้`);
    }catch(error){this.error(error.message);}
  }

  calibrate() {
    try{
      if(!this.calibrationPoints[0]||!this.calibrationPoints[1])throw new Error('เลือกจุด A และ B');
      const points=this.calibrationPoints.map(point=>{const object=this.doc.objects.find(item=>item.id===point.object);if(!object)throw new Error('โมเดลจุดสอบเทียบถูกลบแล้ว');return new THREE.Vector3(...point.local).applyMatrix4(sceneObjectMatrix(object));});
      const meters=Number(this.$('#calibration-distance').value),distance=points[0].distanceTo(points[1]);
      if(!Number.isFinite(meters)||meters<=0||distance<1e-8)throw new Error('ระยะจริงต้องมากกว่า 0 และสองจุดต้องไม่ซ้ำกัน');
      const factor=meters/distance;
      if(this.doc.objects.some(item=>item.scale*factor<1e-6||item.scale*factor>1e6))throw new Error('สเกลเกินขอบเขต ตรวจระยะจริง');
      this.change(()=>{this.doc.view=this.snapshot().view;applySceneCalibration(this.doc,points[0].toArray(),factor);this.camera.position.fromArray(this.doc.view.position);this.rebuildOrbit(this.doc.view.target);});
      this.status(`สอบเทียบทั้งฉากเป็นเมตรแล้ว · คูณสเกล ${factor.toFixed(6)} · Undo ย้อนกลับได้`);
    }catch(error){this.error(error.message);}
  }

  drawPointMarkers() {
    if(!this.renderer)return;
    if(this.pointMarkers){this.scene.remove(this.pointMarkers);disposeGLB(this.pointMarkers);}
    this.pointMarkers=new THREE.Group();this.scene.add(this.pointMarkers);
    if(!this.alignment)return;
    const radius=Math.max(.01,this.allBounds().getSize(new THREE.Vector3()).length()*.003);
    for(const pair of this.alignment.pairs)for(const [side,color]of[['source',0xff765e],['target',0x8feb69]]){
      const object=this.doc.objects.find(item=>item.id===this.alignment[side]);if(!object)continue;
      const marker=new THREE.Mesh(new THREE.SphereGeometry(radius,10,8),new THREE.MeshBasicMaterial({color,depthTest:false}));
      marker.position.fromArray(pair[side]).applyMatrix4(sceneObjectMatrix(object));this.pointMarkers.add(marker);
    }
  }

  async uploadGLB(input) {
    const file=input.files?.[0];if(!file)return;
    if(file.size>256*1024*1024){this.error('GLB ต้องไม่เกิน 256 MB');input.value='';return;}
    input.disabled=true;this.status('กำลังอัปโหลดและตรวจไฟล์ GLB…');
    try{const response=await fetch('/api/scene-assets/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-File-Name':encodeURIComponent(file.name)},body:file});
      const asset=await response.json();if(!response.ok)throw new Error(asset.error||'นำเข้า GLB ไม่สำเร็จ');
      await this.refreshAssets();this.chosenAssets=new Set([assetKey(asset)]);this.addAssets();
    }catch(error){this.error(error.message);}finally{input.disabled=false;input.value='';}
  }

  async startPackage() {
    if(this.dirty&&!await this.save())return;
    this.packageGeneration=this.mutations;
    try{const response=await fetch(`/api/scene-package/${this.config.id}`,{method:'POST'}),state=await response.json();if(!response.ok)throw new Error(state.error||'เตรียม ZIP ไม่สำเร็จ');this.paintPackage(state);if(['queued','running'].includes(state.state))this.pollPackage();}
    catch(error){this.error(error.message);}
  }
  async refreshExports() {
    if(!this.doc||this.dirty)return;
    const revision=this.revision,generation=this.mutations;
    await Promise.allSettled([['scene-export','glb'],['scene-package','package']].map(async([endpoint,kind])=>{
      const response=await fetch(`/api/${endpoint}/${this.config.id}`);if(!response.ok)return;const state=await response.json();
      if(this.dirty||this.revision!==revision||this.mutations!==generation||state.revision!==revision||!['ready','queued','running'].includes(state.state))return;
      if(kind==='glb'){this.glbGeneration=generation;this.paintGLB(state);if(state.state!=='ready')this.pollGLB();}
      else{this.packageGeneration=generation;this.paintPackage(state);if(state.state!=='ready')this.pollPackage();}
    }));
  }
  async startGLB() {
    if(!this.ready||this.walking||this.transformDrag)return;
    if(this.dirty&&!await this.save())return;
    this.glbGeneration=this.mutations;this.paintGLB({state:'queued',progress:0});
    try{
      const response=await fetch(`/api/scene-export/${this.config.id}`,{method:'POST'}),state=await response.json();
      if(!response.ok)throw new Error(state.error||'ส่งออก GLB ไม่สำเร็จ');
      this.paintGLB(state);if(['queued','running'].includes(state.state))this.pollGLB();
    }catch(error){this.paintGLB({state:'error',error:error.message});this.error(error.message);}
  }
  paintGLB(state) {
    this.glbState=state;const busy=['queued','running'].includes(state.state),current=this.glbGeneration===this.mutations;
    this.$('#glb-start').disabled=busy||!this.ready||this.walking||!!this.transformDrag||this.saving;
    const ready=state.state==='ready'&&current;
    this.$('#glb-start').hidden=ready;
    this.$('#glb-download').hidden=!ready;this.$('#glb-viewer').hidden=!ready;
    if(ready){this.$('#glb-download').href=`/scene-glb-download/${this.config.id}/${state.key}.glb`;this.$('#glb-viewer').href=`/scene-glb-viewer/${this.config.id}/${state.key}.glb`;}
    this.$('#glb-status').textContent=state.state==='ready'?(current?`GLB พร้อม · ${(state.bytes/1048576).toFixed(1)} MB`:'ฉากเปลี่ยนแล้ว · ส่งออก GLB ใหม่'):state.error||`กำลังส่งออก GLB ${state.progress||0}%`;
  }
  pollGLB() {
    clearTimeout(this.glbTimer);this.glbTimer=setTimeout(async()=>{
      try{const response=await fetch(`/api/scene-export/${this.config.id}`),state=await response.json();if(!response.ok)throw new Error(state.error||'อ่านสถานะ GLB ไม่ได้');this.paintGLB(state);if(['queued','running'].includes(state.state))this.pollGLB();}
      catch(error){this.paintGLB({state:'error',error:error.message});}
    },1000);
  }
  paintPackage(state) {
    this.packageState=state;
    const busy=['queued','running'].includes(state.state);this.$('#package-start').disabled=busy;
    this.$('#package-status').textContent=state.state==='ready'?`ZIP พร้อม · ${(state.bytes/1048576).toFixed(1)} MB`:state.error||`${busy?'กำลังเตรียม ZIP':'ZIP'} ${state.progress||0}%`;
    const link=this.$('#package-download');link.hidden=state.state!=='ready';if(state.state==='ready')link.href=`/scene-package-download/${this.config.id}/${state.key}.zip`;
    if(state.state==='ready'&&this.packageGeneration!==this.mutations){link.hidden=true;this.$('#package-status').textContent='ฉากเปลี่ยนแล้ว · เตรียม ZIP ใหม่';}
    this.$('#package-start').hidden=!link.hidden;
  }
  pollPackage() {
    clearTimeout(this.packageTimer);this.packageTimer=setTimeout(async()=>{try{const response=await fetch(`/api/scene-package/${this.config.id}`),state=await response.json();this.paintPackage(state);if(['queued','running'].includes(state.state))this.pollPackage();}catch(error){this.error('อ่านสถานะ ZIP ไม่ได้ ลองกดเตรียม ZIP อีกครั้ง');}},1000);
  }

  item() { return this.doc && [...this.doc.objects, ...this.doc.colliders].find(item => item.id === this.selected); }
  isCollider() { return !!this.doc?.colliders.some(item => item.id === this.selected); }
  select(value) { this.cancelTransform(); this.selected = value; if(this.isCollider()&&!this.$('#show-colliders').checked){this.$('#show-colliders').checked=true;this.syncColliders();} this.renderObjectList(); this.renderInspector(); this.updateSelectionBox(); this.syncTransform(); this.renderSceneTools(); }
  historyValue() { return { doc: this.doc, selected: this.selected, view:this.controls?{position:this.camera.position.toArray(),target:this.controls.target.toArray()}:null }; }
  change(mutate) {
    if (!this.ready || this.walking) return;
    if (this.transformDrag) this.endTransform();
    this.history.push(this.historyValue()); mutate(); this.mutations++; this.dirty = true; this.reconcile(); this.renderUI();
  }
  restore(value) {
    if (!value) return; this.cancelTransform(); this.doc = value.doc; this.doc.revision = this.revision; this.selected = value.selected;
    if(value.view&&this.controls){this.camera.position.fromArray(value.view.position);this.rebuildOrbit(value.view.target);}
    this.mutations++; this.dirty = true; this.reconcile(); this.renderUI();
  }
  undo() { if (!this.walking && this.ready) this.restore(this.history.undo(this.historyValue())); }
  redo() { if (!this.walking && this.ready) this.restore(this.history.redo(this.historyValue())); }
  duplicate() {
    const source=this.item(); if(!source)return;const collider=this.isCollider(),attached=collider?[]:this.doc.colliders.filter(item=>item.parent===source.id);
    if(this.doc.objects.length+Number(!collider)>128||this.doc.colliders.length+attached.length+Number(collider)>512){this.error('จำนวนรายการเกินขีดจำกัดฉาก');return;}
    this.change(()=>{const copy=clone(source);copy.id=id();copy.name+=' · สำเนา';
      if(collider){const world=worldCollider(copy,this.doc.objects);world.position[0]+=.1;setColliderWorld(copy,world,this.doc.objects);this.doc.colliders.push(copy);}
      else {copy.position[0]+=.1;this.doc.objects.push(copy);this.doc.colliders.push(...attached.map(item=>({...clone(item),id:id(),parent:copy.id})));}
      this.selected=copy.id;
    });
  }
  remove() {
    if(!this.item())return; this.change(()=>{const removed=this.selected;this.doc.objects=this.doc.objects.filter(item=>item.id!==removed);this.doc.colliders=this.doc.colliders.filter(item=>item.id!==removed&&item.parent!==removed);this.selected=null;});
  }
  readNumber(input, min = -1000000, max = 1000000) {
    const value = input.value.trim() === '' ? NaN : Number(input.value); const valid = Number.isFinite(value) && value >= min && value <= max;
    input.classList.toggle('invalid', !valid); input.setAttribute('aria-invalid', String(!valid));
    if (!valid) { this.status(`กรุณาใส่ตัวเลข ${min} ถึง ${max}`); return null; } return value;
  }
  editVector(prefix,axis,input) {
    const item=this.item();if(!item)return;const value=this.readNumber(input,prefix==='size'?.001:-1000000);if(value===null)return;
    this.change(()=>{if(this.isCollider()){const world=worldCollider(item,this.doc.objects);if(prefix==='rotation')world.rotation[axis]=radians(value);else world[prefix][axis]=value;setColliderWorld(item,world,this.doc.objects);}
      else if(prefix==='rotation')item.rotation[axis]=radians(value);else item[prefix][axis]=value;});
  }
  resetSelected(kind,axis=null) {
    if(!this.item()||this.walking)return;const item=this.item(),collider=this.isCollider();
    this.change(()=>{const target=collider?worldCollider(item,this.doc.objects):item;
      if(kind==='position'||kind==='all'){if(axis===null)target.position=[0,0,0];else target.position[axis]=0;}
      if(kind==='rotation'||kind==='all'){if(axis===null)target.rotation=[0,0,0];else target.rotation[axis]=0;}
      if(kind==='size'||kind==='scale'||kind==='all'){if(collider){const size=item.kind==='floor'?[10,.2,10]:[5,3,.2];if(axis===null)target.size=size;else target.size[axis]=size[axis];}else target.scale=1;}
      if(collider)setColliderWorld(item,target,this.doc.objects);
    });
  }
  setTransformMode(mode) {
    if (this.walking || this.transformDrag || !['translate', 'rotate', 'scale'].includes(mode)) return;
    this.transformMode = mode;
    this.syncTransform();
  }

  setTransformAxis(axis) {
    if (this.walking || this.transformDrag || !['X', 'Y', 'Z'].includes(axis)) return;
    this.transformAxis = this.transformAxis === axis ? null : axis;
    this.syncTransform();
  }

  syncTransform() {
    const item = this.item(), collider = this.isCollider(), disabled = !item || this.walking || !this.transform;
    if (this.transform && !this.transformDrag) {
      this.transform.setMode(this.transformMode); this.transform.setSpace(this.transformMode === 'scale' ? 'local' : this.transformSpace);
      const target = !this.walking && !this.pointMode && item && (collider ? this.colliderRuntime.get(item.id) : this.runtime.get(item.id)?.group);
      if (target?.visible && (collider ? item.enabled : item.visible)) { if (this.transform.object !== target) this.transform.attach(target); }
      else this.transform.detach();
      for (const axis of ['X', 'Y', 'Z']) this.transform[`show${axis}`] = (!this.transformAxis || this.transformAxis === axis);
    }
    for (const mode of ['translate', 'rotate', 'scale']) {
      const button = this.$(`#transform-${mode}`); if (!button) continue;
      button.disabled = disabled || !!this.transformDrag; button.classList.toggle('active', this.transformMode === mode); button.setAttribute('aria-pressed', String(this.transformMode === mode));
    }
    for (const axis of ['X', 'Y', 'Z']) {
      const button = this.$(`#transform-axis-${axis.toLowerCase()}`); if (!button) continue;
      button.disabled = disabled || !!this.transformDrag;
      button.classList.toggle('active', this.transformAxis === axis); button.setAttribute('aria-pressed', String(this.transformAxis === axis));
    }
    const space = this.$('#transform-space'); if (space) { space.disabled = disabled || !!this.transformDrag || this.transformMode === 'scale'; space.textContent = this.transformSpace === 'world' ? 'แกนโลก' : 'แกนวัตถุ'; space.setAttribute('aria-pressed', String(this.transformSpace === 'local')); }
    this.updateSnap();
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
    const edited = collider ? worldCollider(item,this.doc.objects) : item;
    // Models keep a uniform scale; collider boxes allow separate axis sizes.
    if (drag.mode === 'translate') edited.position = object.position.toArray().map(value => THREE.MathUtils.clamp(value, -1000000, 1000000));
    if (drag.mode === 'rotate') edited.rotation = [object.rotation.x,object.rotation.y,object.rotation.z];
    if (drag.mode === 'scale') {
      if (collider) edited.size = object.scale.toArray().map(value => THREE.MathUtils.clamp(value, .001, 1000000));
      else {
        const components = object.scale.toArray(), original = drag.item.scale;
        const axis = ['X', 'Y', 'Z'].findIndex(axis => this.transform.axis === axis);
        const index = axis >= 0 ? axis : components.reduce((best, value, index) => Math.abs(value - original) > Math.abs(components[best] - original) ? index : best, 0);
        item.scale = THREE.MathUtils.clamp(components[index], .000001, 1000000);
      }
    }
    if (collider) { setColliderWorld(item,edited,this.doc.objects); object.position.fromArray(edited.position); object.rotation.set(...edited.rotation); object.scale.fromArray(edited.size); object.updateMatrixWorld(true); }
    else this.applyTransform(object, item);
    this.mutations++; this.dirty = true; this.syncColliders(); this.renderInspector(); this.updateSelectionBox();
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
    if(this.packageState)this.paintPackage(this.packageState);
    if(this.glbState)this.paintGLB(this.glbState);else this.$('#glb-start').disabled=!this.ready||this.walking||!!this.transformDrag||this.saving;
    this.$('#scene-name').value = this.doc.name; this.$('#scene-units').value = this.doc.units;
    this.$('#scene-name').disabled = this.walking; this.$('#scene-units').disabled = this.walking;
    for (const name of ['scene-download', 'collision-download', 'add-floor', 'add-wall']) this.$(`#${name}`).disabled = !this.ready || this.walking || !!this.transformDrag;
    for (const name of ['fit-all', 'walk-toggle', 'spawn-set']) this.$(`#${name}`).disabled = !this.controls;
    this.$('#scene-undo').disabled = !this.history.past.length || this.walking || !!this.transformDrag; this.$('#scene-redo').disabled = !this.history.future.length || this.walking || !!this.transformDrag;
    this.$('#scene-save').disabled = !this.ready || this.saving || !!this.transformDrag; this.$('#scene-save').textContent = this.saving ? 'กำลังบันทึก…' : 'บันทึก';
    this.$('#save-state').textContent = this.saving ? 'กำลังบันทึก…' : this.dirty ? 'ยังไม่ได้บันทึก' : `บันทึกแล้ว · รุ่น ${this.revision}`;
    this.$('#save-state').classList.toggle('is-dirty',this.dirty);
    for(const button of document.querySelectorAll('[data-dialog-open]'))button.disabled=!this.ready||this.walking||!!this.transformDrag;
    this.$('#scene-empty').hidden = !!this.doc.objects.length || !!this.doc.colliders.length;
    for (const key of ['height', 'radius', 'speed']) { this.$(`#player-${key}`).value = formatNumber(this.doc.player[key]); this.$(`#player-${key}`).disabled = this.walking; }
    this.$('#spawn-set').disabled = !this.controls || this.walking;
    this.$('#spawn-label').textContent = this.doc.spawn ? `จุดเริ่ม X ${formatNumber(this.doc.spawn.position[0])} · Y ${formatNumber(this.doc.spawn.position[1])} · Z ${formatNumber(this.doc.spawn.position[2])}` : '';
    this.$('#reset-view').disabled = !this.controls;
    this.renderObjectList(); this.renderInspector(); this.updateAddButton(); this.updateStats(); this.syncTransform(); this.renderSceneTools();
  }

  renderObjectList() {
    if (!this.doc) return; const list = this.$('#object-list'); list.replaceChildren(); const all = [...this.doc.objects, ...this.doc.colliders];
    this.$('#object-count').textContent = all.length;
    if (!all.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'เริ่มจากเพิ่มโมเดลในคลัง'; list.append(empty); }
    for (const item of all) {
      const collider = this.doc.colliders.includes(item); const enabled = collider ? item.enabled : item.visible; const entry = this.runtime.get(item.id);
      if(item===this.doc.objects[0]||item===this.doc.colliders[0]){const heading=document.createElement('h3');heading.className='object-group-title';heading.textContent=collider?`พื้นที่ตรวจชน · ${this.doc.colliders.length}`:`โมเดล · ${this.doc.objects.length}`;list.append(heading);}
      const row = document.createElement('div'); row.className = `object-row${item.id === this.selected ? ' selected' : ''}${collider?' is-collider':''}${enabled?'':' is-hidden'}`;
      const icon = document.createElement('span'); icon.className = 'object-icon'; icon.dataset.icon = collider ? item.kind === 'floor' ? 'floor' : 'wall' : 'cube';
      const select = document.createElement('button'); select.className = 'object-select'; select.textContent = item.name; select.setAttribute('aria-pressed', String(item.id === this.selected)); select.disabled = this.walking;
      const detail = document.createElement('small'); detail.textContent = collider ? `${item.kind === 'floor' ? 'พื้น' : 'กำแพง'} · ${enabled ? 'ตรวจชน' : 'ปิดตรวจชน'}` : entry?.error ? 'โหลดไม่สำเร็จ' : entry?.mesh ? `${item.asset.kind.toUpperCase()} · พร้อม` : enabled ? 'รอโหลด…' : 'ซ่อน'; select.append(detail); select.addEventListener('click', () => this.select(item.id));
      const visibility = document.createElement('button'); visibility.className = 'object-visibility'; visibility.dataset.icon = enabled ? 'eye' : 'eyeOff'; visibility.title = enabled ? 'ซ่อน / ปิดตรวจชน' : 'แสดง / เปิดตรวจชน'; visibility.setAttribute('aria-label', `${visibility.title} ${item.name}`); visibility.setAttribute('aria-pressed', String(enabled)); visibility.disabled = this.walking;
      visibility.addEventListener('click', () => this.change(() => { item[collider ? 'enabled' : 'visible'] = !enabled; })); row.append(icon, select, visibility); list.append(row);
    }
  }

  renderInspector() {
    const item = this.item(); const collider = this.isCollider(); this.$('#selection-title').textContent = item ? item.name : 'เลือกโมเดลเพื่อปรับแต่ง'; this.$('#selection-empty').hidden = !!item;
    this.$('#selection-fields').hidden = !item; this.$('#selection-fields').disabled = !item || this.walking || !!this.transformDrag;
    this.$('#fit-selected').disabled = !item || !this.controls || this.walking;
    this.$('#item-ground').disabled = !item || collider || !this.runtime.get(item?.id)?.bounds || this.walking;
    this.$('#auto-collision').disabled = !item || collider || !this.runtime.get(item?.id)?.bounds || this.walking || !!this.transformDrag;
    this.$('#auto-collision-group').hidden = !item || collider;
    this.renderParentSelector();
    if (!item) return;
    const displayed=collider?worldCollider(item,this.doc.objects):item;
    this.$('#item-name').value = item.name; this.$('#item-visible').checked = collider ? item.enabled : item.visible;
    for (let axis = 0; axis < 3; axis++) {
      this.$(`#position-${axis}`).value = formatNumber(displayed.position[axis]);
      const rotation = this.$(`#rotation-${axis}`); rotation.value = formatNumber(degrees(displayed.rotation[axis])); rotation.disabled = false;
      this.$(`#rotation-reset-${axis}`).disabled = false;
      this.$(`#size-${axis}`).value = formatNumber(collider ? displayed.size[axis] : 1);
    }
    this.$('#size-fields').hidden = !collider; this.$('#scale-fields').hidden = collider; this.$('#item-scale').value = formatNumber(collider ? 1 : item.scale);
    this.$('#item-reset-scale').textContent = collider ? 'คืนขนาดกล่อง' : 'คืนสเกล';
    const entry = this.runtime.get(item.id);
    this.$('#item-load-status').textContent = entry?.error ? `โหลดไม่ได้: ${entry.error} · ซ่อนแล้วแสดงเพื่อลองอีกครั้ง` : ''; this.$('#item-load-status').hidden=!entry?.error;
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
    if (entry.mesh) { if(entry.asset.kind==='glb')disposeGLB(entry.mesh);else entry.mesh.dispose?.(); entry.mesh = null; }
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
        if(asset.kind==='glb') {
          const gltf=await new GLTFLoader().loadAsync(asset.url); mesh=gltf.scene;
        } else {
          mesh=new this.SplatMesh({url:asset.url,fileType:asset.kind,extSplats:true,lod:true,enableLod:true,
            onProgress:progress=>{if(!entry.cancelled)this.status(`กำลังโหลด ${object.name} · ${progress.total?Math.round(progress.loaded/progress.total*100)+'%':'?'}`);}});
          await mesh.initialized;
        }
        if(entry.cancelled||this.runtime.get(objectId)!==entry){if(asset.kind==='glb')disposeGLB(mesh);else mesh.dispose?.();continue;}
        entry.mesh=mesh;mesh.updateMatrixWorld(true);entry.bounds=asset.kind==='glb'?new THREE.Box3().setFromObject(mesh):sceneModelBounds(mesh);entry.group.add(mesh);
        entry.sourceCount=Number(asset.splats)||Number(mesh.numSplats)||0;entry.group.updateMatrixWorld(true);this.loadErrors.delete(objectId);this.loadCount++;
        this.updateSelectionBox(); this.renderObjectList(); this.renderInspector(); this.updateStats(); this.syncTransform();
      } catch (error) {
        if(asset.kind==='glb'&&mesh)disposeGLB(mesh);else mesh?.dispose?.(); if (entry.cancelled) continue; entry.error = error.message || 'อ่านโมเดลไม่ได้'; this.loadErrors.set(objectId, entry.error);
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
    for (const item of sceneWorldColliders(this.doc)) {
      let mesh = this.colliderRuntime.get(item.id);
      if (!mesh) { mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ color: item.kind === 'floor' ? 0x83c65b : 0xf2be74, transparent: true, opacity: .18, depthWrite: false, side: THREE.DoubleSide })); this.scene.add(mesh); this.colliderRuntime.set(item.id, mesh); }
      mesh.position.fromArray(item.position); mesh.rotation.set(...item.rotation); mesh.scale.fromArray(item.size); mesh.visible = item.enabled && this.$('#show-colliders').checked;
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
    if(document.querySelector('dialog[open]'))return;
    const editing = event.target?.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(event.target?.tagName);
    if (event.code === 'Escape' && this.pointMode) { event.preventDefault();this.stopPointPick();return; }
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
    this.$('#walk-toggle').textContent = value ? 'หยุดเดิน' : 'ทดลองเดิน'; this.$('#walk-toggle').dataset.icon=value?'close':'walk'; this.$('#walk-toggle').classList.toggle('active', value); this.$('#walk-banner').hidden = !value; document.body.classList.toggle('walking', value); this.updateSelectionBox(); this.renderUI();
  }

  frame(time) {
    const seconds = this.lastFrame === null ? 0 : Math.max(0, Math.min((time - this.lastFrame) / 1000, .05)); this.lastFrame = time;
    const active = this.windowFocused && !document.hidden && (document.activeElement === this.canvas || document.pointerLockElement === this.canvas);
    if (this.walking && this.doc && seconds > 0 && active) {
      const forward = Number(this.keys.has('KeyW')) - Number(this.keys.has('KeyS')); const right = Number(this.keys.has('KeyD')) - Number(this.keys.has('KeyA'));
      const delta = new THREE.Vector3(-Math.sin(this.walkYaw) * forward + Math.cos(this.walkYaw) * right, 0, -Math.cos(this.walkYaw) * forward - Math.sin(this.walkYaw) * right);
      if (delta.lengthSq() > 1) delta.normalize(); const speed = this.doc.player.speed * (this.keys.has('ShiftLeft') || this.keys.has('ShiftRight') ? 2 : 1); delta.multiplyScalar(speed * seconds);
      this.fallSpeed = Math.max(-50, this.fallSpeed - 9.81 * seconds); delta.y = this.fallSpeed * seconds;
      const result = moveScenePlayer(this.camera.position.toArray(), delta.toArray(), sceneWorldColliders(this.doc), { radius: this.doc.player.radius, height: this.doc.player.height, stepHeight: Math.min(.3, this.doc.player.height * .2) });
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
    get selected() { return editor.selected; }, get transformMode() { return editor.transformMode; }, get axis() { return editor.transformAxis; }, get dragging() { return !!editor.transformDrag; }, get gizmoScreen() { return editor.gizmoScreen(); },
    get worldColliders() { return editor.doc ? sceneWorldColliders(editor.doc) : []; }, get measurement() { return clone(editor.calibrationPoints); },
    projectPoint(id, point) { const item=editor.doc?.objects.find(item=>item.id===id); if(!item)return null; const p=new THREE.Vector3(...point).applyMatrix4(sceneObjectMatrix(item)).project(editor.camera), rect=editor.canvas.getBoundingClientRect(); return [rect.left+(p.x+1)*rect.width/2,rect.top+(1-p.y)*rect.height/2]; } });
  editor.start();
}
