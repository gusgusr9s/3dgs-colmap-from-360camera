import * as THREE from 'three';

export function sceneObjectMatrix(item) {
  return new THREE.Matrix4().compose(new THREE.Vector3(...item.position),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...item.rotation)), new THREE.Vector3().setScalar(item.scale));
}

export function colliderRotation(item) { return item.rotation || [0, item.yaw || 0, 0]; }

export function worldCollider(item, objects) {
  const parent = item.parent && objects.find(object => object.id === item.parent);
  if (!parent) return {...item, rotation: [...colliderRotation(item)]};
  const local = new THREE.Matrix4().compose(new THREE.Vector3(...item.position),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...colliderRotation(item))), new THREE.Vector3(...item.size));
  const position = new THREE.Vector3(), quaternion = new THREE.Quaternion(), size = new THREE.Vector3();
  sceneObjectMatrix(parent).multiply(local).decompose(position, quaternion, size);
  const rotation = new THREE.Euler().setFromQuaternion(quaternion);
  return {...item, position: position.toArray(), rotation: [rotation.x, rotation.y, rotation.z], yaw: rotation.y, size: size.toArray()};
}

export function bindCollider(item, parent, objects) {
  const world = worldCollider(item, objects);
  const owner = parent && objects.find(object => object.id === parent);
  const matrix = new THREE.Matrix4().compose(new THREE.Vector3(...world.position),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...world.rotation)), new THREE.Vector3(...world.size));
  if (owner) matrix.premultiply(sceneObjectMatrix(owner).invert());
  const position = new THREE.Vector3(), quaternion = new THREE.Quaternion(), size = new THREE.Vector3();
  matrix.decompose(position, quaternion, size);
  const rotation = new THREE.Euler().setFromQuaternion(quaternion);
  return {...item, parent: owner?.id || null, position: position.toArray(),
    rotation: [rotation.x, rotation.y, rotation.z], yaw: rotation.y, size: size.toArray()};
}

export function setColliderWorld(item, world, objects) {
  Object.assign(item, bindCollider({...item, ...world, parent: null}, item.parent, objects));
}

export function sceneWorldColliders(doc) { return doc.colliders.map(item => worldCollider(item, doc.objects)); }

export function applySceneCalibration(doc, pivot, factor) {
  if (!Number.isFinite(factor) || factor <= 0) throw new Error('ระยะสอบเทียบไม่ถูกต้อง');
  const convert = point => point.map((value, axis) => pivot[axis] + (value - pivot[axis]) * factor);
  for (const item of doc.objects) { item.position = convert(item.position); item.scale *= factor; }
  for (const item of doc.colliders) if (!item.parent) {
    item.position = convert(item.position); item.size = item.size.map(value => value * factor);
  }
  const feet = [...doc.spawn.position]; feet[1] -= doc.player.height;
  doc.spawn.position = convert(feet); doc.spawn.position[1] += doc.player.height;
  doc.view.position = convert(doc.view.position); doc.view.target = convert(doc.view.target);
  doc.units = 'meters';
}
