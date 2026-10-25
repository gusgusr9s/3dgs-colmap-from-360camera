import * as THREE from 'three';
import { WebGPURenderer } from 'three/webgpu';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { GLTFGaussianSplatLoaderExtension } from 'three/addons/loaders/GLTFGaussianSplatLoaderExtension.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { MTLLoader } from 'three/addons/loaders/MTLLoader.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';
import { sceneObjectMatrix, sceneWorldColliders } from './scene_geometry.mjs';
import { moveScenePlayer } from './scene_collision.mjs';

export async function createViewer(host, url, callbacks = {}) {
  host.replaceChildren();
  let disposed = false;
  const isCurrent = () => callbacks.isCurrent?.() !== false;
  const cancelled = () => ({ setMode() {}, setControls() {}, reset() {}, dispose() {} });
  const details = callbacks.details || {};
  const sceneDocument = details.scene;
  const packageMode = callbacks.format === 'zip';
  const isSplatFile = ['spz', 'splat'].includes(callbacks.format) || callbacks.format === 'ply' && details.metadata?.representation === 'gaussian';
  const classicRenderer = isSplatFile || ['obj','ply'].includes(callbacks.format) || packageMode && details.renderer !== 'webgpu';
  const formatLabel = packageMode ? 'SCENE ZIP' : (callbacks.format || 'glb').toUpperCase();
  let renderer;
  if (classicRenderer) {
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
  scene.add(new THREE.HemisphereLight(0xffffff, 0x49586a, 2));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.5);
  keyLight.position.set(3, 6, 4); scene.add(keyLight);
  const camera = new THREE.PerspectiveCamera(65, 1, .001, 10000);
  let gltf;
  let splats;
  const gltfs = [], splatMeshes = [], roots = [], mixers = [];
  let spark;
  let sparkUpdate = null, sparkView = '', sparkRevision = 0;
  let splatPivot;
  let splatFlipped = callbacks.flipSpz === true;
  let bounds;
  function updateSpark(force=false) {
    if(!spark||disposed)return Promise.resolve();
    if(sparkUpdate)return sparkUpdate;
    const key=[...camera.position.toArray(),...camera.quaternion.toArray(),camera.fov,camera.aspect,
      renderer.domElement.width,renderer.domElement.height,sparkRevision].map(value=>Number(value.toPrecision(10))).join(',');
    if(!force&&key===sparkView)return Promise.resolve();
    sparkView=key;
    sparkUpdate=spark.update({scene,camera}).finally(()=>{sparkUpdate=null;});
    sparkUpdate.catch(error=>{if(!disposed)callbacks.error?.(error);});
    return sparkUpdate;
  }
  const progress = e => callbacks.progress?.(e.total ? `กำลังโหลด ${Math.round(e.loaded / e.total * 100)}%` : `กำลังโหลด ${(e.loaded / 1048576).toFixed(1)} MB`);
  async function loadAsset(asset, parent) {
    const gaussian = ['spz','splat'].includes(asset.kind) || asset.kind === 'ply' && asset.representation === 'gaussian';
    if (gaussian) {
      const { SparkRenderer, SplatMesh } = await import('@sparkjsdev/spark');
      if (!spark) {
        spark = new SparkRenderer({ renderer, autoUpdate:false, preBlurAmount: .3, blurAmount: 0,
        sortRadial: false, maxStdDev: Math.sqrt(12), minAlpha: 1 / 255,
        enableLod: false, focalAdjustment: 1 });
        scene.add(spark);
      }
      const mesh = new SplatMesh({ url: asset.url, fileType: asset.kind, extSplats: true,
        lod: false, enableLod: false, onProgress: progress });
      splatMeshes.push(mesh);await mesh.initialized;parent.add(mesh);return mesh;
    }
    if (asset.kind === 'obj') {
      const loader = new OBJLoader();
      const creators = [];
      for (const library of packageMode ? asset.material_libraries || [] : []) {
        const creator = await new MTLLoader().loadAsync(new URL(library, new URL(asset.url, location.origin)).href);
        creator.preload();creators.push(creator);
      }
      if (creators.length) loader.setMaterials({create: materialName => {
        const owner = creators.find(creator => creator.materialsInfo[materialName]);
        return owner ? owner.create(materialName) : new THREE.MeshStandardMaterial({color:0xb7cabd});
      }});
      const root = await loader.loadAsync(asset.url, progress);parent.add(root);roots.push(root);return root;
    }
    if (asset.kind === 'ply') {
      const geometry = await new PLYLoader().loadAsync(asset.url, progress);
      geometry.computeBoundingSphere();const colors = !!geometry.getAttribute('color');
      let root;
      if (asset.representation === 'mesh' || geometry.index) {
        if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
        root = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({vertexColors:colors,color:colors ? 0xffffff : 0xb7cabd,side:THREE.DoubleSide,roughness:.8}));
      } else root = new THREE.Points(geometry,new THREE.PointsMaterial({vertexColors:colors,color:colors ? 0xffffff : 0xa0e1c3,size:Math.max(.0001,geometry.boundingSphere.radius*.012)}));
      parent.add(root);roots.push(root);return root;
    }
    const loader = new GLTFLoader();loader.register(parser => new GLTFGaussianSplatLoaderExtension(parser));
    const loaded = await loader.loadAsync(asset.url, progress);gltfs.push(loaded);parent.add(loaded.scene);
    if (loaded.animations.length) {const mixer=new THREE.AnimationMixer(loaded.scene);mixer.clipAction(loaded.animations[0]).play();mixers.push(mixer);}
    return loaded.scene;
  }
  try {
    if (packageMode) {
      bounds = new THREE.Box3();
      for (const item of details.composition.filter(item => item.visible)) {
        if (!isCurrent()) break;
        const asset = details.assets.find(asset => asset.kind === item.asset.kind && asset.ref === item.asset.ref);
        if (!asset) throw new Error('ไม่พบไฟล์ '+item.name);
        callbacks.progress?.('กำลังเปิด '+item.name);
        const group = new THREE.Group();group.applyMatrix4(sceneObjectMatrix(item));scene.add(group);
        const root = await loadAsset(asset,group);group.updateMatrixWorld(true);
        bounds.union(root.getBoundingBox ? root.getBoundingBox().applyMatrix4(root.matrixWorld) : new THREE.Box3().setFromObject(root));
      }
    } else if (isSplatFile) {
      splats = await loadAsset({url,kind:callbacks.format,...details.metadata},scene);
      const pivot = splats.getBoundingBox().getCenter(new THREE.Vector3());
      splatPivot = new THREE.Group();splatPivot.position.copy(pivot);splats.position.sub(pivot);
      splatPivot.rotation.x = splatFlipped ? Math.PI : 0;scene.add(splatPivot);splatPivot.add(splats);
      splatPivot.updateMatrixWorld(true);bounds = splats.getBoundingBox().applyMatrix4(splats.matrixWorld);
    } else {
      const root = await loadAsset({url,kind:callbacks.format,...details.metadata},scene);
      gltf = gltfs[0];bounds = new THREE.Box3().setFromObject(root);
    }
  } catch (error) { disposeModel(); renderer.dispose(); renderer.domElement.remove(); throw error; }
  if (!isCurrent()) { disposeModel(); renderer.dispose(); renderer.domElement.remove(); return cancelled(); }
  if (bounds.isEmpty() || ![...bounds.min, ...bounds.max].every(Number.isFinite)) {
    disposeModel(); renderer.dispose(); renderer.domElement.remove();
    throw new Error('โมเดลไม่มีข้อมูลภาพที่แสดงได้');
  }
  const center = bounds.getCenter(new THREE.Vector3());
  const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() / 2, .01);
  camera.near = Math.max(radius / 100000, .00001);
  camera.far = Math.max(radius * 20, 1000);
  const initial = gltf?.scene.userData.initial_view || {};
  const matrix = initial.transform_matrix;
  let target;
  if (sceneDocument) {
    camera.position.fromArray(sceneDocument.view.position);target=new THREE.Vector3(...sceneDocument.view.target);camera.lookAt(target);
  } else if (Array.isArray(matrix) && matrix.length === 4 && matrix.every(row => Array.isArray(row) && row.length === 4 && row.every(Number.isFinite))) {
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
  let fall = 0;
  const worldBoxes = sceneDocument ? sceneWorldColliders(sceneDocument).filter(box => box.enabled) : [];
  const gravity = worldBoxes.some(box => box.kind === 'floor');
  const collisionHelpers = new THREE.Group();scene.add(collisionHelpers);collisionHelpers.visible=false;
  for (const box of worldBoxes) {
    const helper=new THREE.Mesh(new THREE.BoxGeometry(...box.size),new THREE.MeshBasicMaterial({color:box.kind==='floor'?0x8ad7b8:0xf0b880,transparent:true,opacity:.2,depthWrite:false}));
    helper.position.fromArray(box.position);helper.rotation.set(...box.rotation);collisionHelpers.add(helper);roots.push(helper);
  }
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
  const onBlur = () => { pressed.clear();touchMove.x=0;touchMove.y=0; };
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
  renderer.domElement.addEventListener('blur', onBlur);
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
    else if (!document.hidden && !document.querySelector('dialog[open]')) {
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
      const canMove = document.pointerLockElement === renderer.domElement || document.activeElement === renderer.domElement || touchMove.x || touchMove.y;
      const delta = new THREE.Vector3();
      if (canMove && move.lengthSq()) {
        const amount = Math.min(move.length(), 1);
        const speed=mode==='walk' && sceneDocument ? sceneDocument.player.speed : radius*.5;
        delta.copy(move.normalize()).multiplyScalar(dt*speed*movementSpeed*amount*(pressed.has('ShiftLeft') || pressed.has('ShiftRight') ? 3 : 1));
      }
      if (mode === 'walk' && worldBoxes.length) {
        if (gravity && canMove) {fall=Math.max(-50,fall-9.81*dt);delta.y=fall*dt;}
        const result=moveScenePlayer(camera.position.toArray(),delta.toArray(),worldBoxes,sceneDocument.player);
        camera.position.fromArray(result.position);if(result.grounded)fall=0;
        if (!gravity) camera.position.y=walkHeight;
        if (camera.position.y<bounds.min.y-Math.max(20,radius*2)) respawn();
      } else {camera.position.add(delta);if(mode==='walk')camera.position.y=walkHeight;}
    }
    mixers.forEach(mixer=>mixer.update(dt));
    updateSpark();
    renderer.render(scene, camera);
  }
  function disposeModel() {
    splatMeshes.forEach(mesh=>mesh.dispose());spark?.dispose();
    const geometries = new Set(), materials = new Set(), textures = new Set();
    for (const root of [...gltfs.flatMap(loaded=>loaded.scenes),...roots]) root.traverse(object => {
      if (object.geometry) geometries.add(object.geometry);
      for (const material of [].concat(object.material || [])) {
        materials.add(material);
        for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
      }
      if (object.isGaussianSplat) object.dispose?.();
    });
    geometries.forEach(value => value.dispose()); materials.forEach(value => value.dispose());
    textures.forEach(value => { value.source?.data?.close?.(); value.dispose(); });
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
    renderer.domElement.removeEventListener('blur', onBlur);
    mixers.forEach(mixer=>mixer.stopAllAction());
    renderer.domElement.remove();
    const release=()=>{disposeModel();renderer.dispose();};
    // Keep the context alive until the last GPU readback/sort has settled.
    // Terminating its worker earlier creates an unhandled rejection on swaps.
    if(sparkUpdate)sparkUpdate.then(release,release);else release();
  }
  try { if (classicRenderer) {renderer.render(scene,camera);await updateSpark();renderer.render(scene,camera);} else await renderer.renderAsync(scene, camera); }
  catch (error) { cleanup(); throw error; }
  if (!isCurrent()) { cleanup(); return cancelled(); }
  renderer.setAnimationLoop(animate);
  let splatCount = splatMeshes.reduce((sum,mesh)=>sum+mesh.numSplats,0);
  for (const loaded of gltfs) loaded.scene.traverse(object => { if (object.isGaussianSplat) splatCount += object.geometry.instanceCount || 0; });
  callbacks.ready?.(splatCount ? `${splatCount.toLocaleString()} SPLATS · ${formatLabel}` : `${details.metadata?.representation==='points'?'POINT CLOUD':'MESH'} ${formatLabel}`);
  const fileViews=[];
  for(const loaded of gltfs){loaded.scene.updateMatrixWorld(true);const cameras=[];loaded.scene.traverse(object=>{if(object.isPerspectiveCamera)cameras.push(object);});for(const source of cameras){if(fileViews.length>=32)continue;
    const position=source.getWorldPosition(new THREE.Vector3()),quaternion=source.getWorldQuaternion(new THREE.Quaternion());
    fileViews.push({name:source.userData.name||source.name||`กล้อง ${fileViews.length+1}`,position:position.toArray(),quaternion:quaternion.toArray(),
      target:position.clone().add(new THREE.Vector3(0,0,-1).applyQuaternion(quaternion).multiplyScalar(Math.max(.1,radius*.1))).toArray(),fov:source.fov,mode:'free'});
  }}
  function respawn() {
    if (!sceneDocument) return;
    camera.position.fromArray(sceneDocument.spawn.position);camera.up.set(0,1,0);
    camera.quaternion.setFromEuler(new THREE.Euler(0,sceneDocument.spawn.yaw,0,'YXZ'));
    walkHeight=camera.position.y;fall=0;pressed.clear();touchMove.x=0;touchMove.y=0;
  }
  function changeMode(next,placeAtSpawn=true) {
    if (!['orbit','walk','free'].includes(next)) return;
    if (document.pointerLockElement===renderer.domElement)document.exitPointerLock();
    pressed.clear();touchMove.x=0;touchMove.y=0;fall=0;
    const changed=mode!==next;mode=next;orbit.enabled=next==='orbit';
    if (next==='orbit') {
      orbit.target.copy(camera.position).add(new THREE.Vector3(0,0,-1).applyQuaternion(camera.quaternion).multiplyScalar(Math.max(radius*.1,.1)));
      updateOrbitPanSpeed();orbit.update();
    } else if (next==='walk'&&changed&&placeAtSpawn&&sceneDocument)respawn();
    walkHeight=camera.position.y;callbacks.mode?.(mode);
  }
  return {
    get fileViews() {return fileViews.map(view=>({...view}));},
    setMode: changeMode,
    reset() {
      if (mode==='walk'&&sceneDocument) {respawn();return;}
      if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
      camera.position.copy(startingPosition); camera.quaternion.copy(startingQuaternion);
      orbit.target.copy(startingTarget); orbit.update();
      updateOrbitPanSpeed();
      walkHeight = camera.position.y;
    },
    respawn() {changeMode('walk',false);respawn();},
    collisionVisibility(value) {collisionHelpers.visible=!!value;},
    getView() {
      return {position:camera.position.toArray(),target:mode==='orbit'?orbit.target.toArray():camera.position.clone().add(new THREE.Vector3(0,0,-1).applyQuaternion(camera.quaternion).multiplyScalar(Math.max(.1,radius*.1))).toArray(),
              quaternion:camera.quaternion.toArray(),fov:camera.fov,mode};
    },
    gotoView(view) {
      changeMode(view.mode,false);camera.position.fromArray(view.position);camera.quaternion.fromArray(view.quaternion);camera.fov=view.fov;
      camera.up.set(0,1,0);camera.updateProjectionMatrix();orbit.target.fromArray(view.target);if(mode==='orbit')orbit.update();walkHeight=camera.position.y;
    },
    async thumbnail() {
      if (disposed) return null;
      if (spark) {
        if(sparkUpdate)await sparkUpdate;
        await updateSpark(true);
      }
      if (disposed) return null;
      if (classicRenderer)renderer.render(scene,camera);else await renderer.renderAsync(scene,camera);
      if (disposed) return null;
      const cover=document.createElement('canvas');cover.width=320;cover.height=200;
      const context=cover.getContext('2d');context.fillStyle='#151e20';context.fillRect(0,0,320,200);
      const source=renderer.domElement,scale=Math.min(320/source.width,200/source.height),width=source.width*scale,height=source.height*scale;
      context.drawImage(source,(320-width)/2,(200-height)/2,width,height);
      return new Promise(resolve=>cover.toBlob(resolve,'image/png'));
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
      sparkRevision++;
      return splatFlipped;
    },
    dispose() {
      cleanup();
    }
  };
}
