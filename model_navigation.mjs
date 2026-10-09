import * as THREE from 'three';

export function placeCapturedCamera(camera, controls, capture, distance) {
  const matrix = new THREE.Matrix4().set(...capture.transform_matrix.flat().map(Number));
  matrix.decompose(camera.position, camera.quaternion, camera.scale);
  camera.up.set(0, 1, 0).applyQuaternion(camera.quaternion).normalize();
  syncLookTarget(camera, controls, distance);
  camera.updateMatrixWorld();
}

export function syncLookTarget(camera, controls, distance) {
  const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
  controls.target.copy(camera.position).addScaledVector(forward, distance);
}

export function turnAtPosition(camera, controls, state, dx, dy, distance) {
  const pitch = THREE.MathUtils.clamp(state.pitch - dy * .003, -Math.PI * .47, Math.PI * .47);
  const yawRotation = new THREE.Quaternion().setFromAxisAngle(camera.up, -dx * .003);
  camera.quaternion.premultiply(yawRotation);
  camera.rotateX(pitch - state.pitch);
  state.pitch = pitch;
  syncLookTarget(camera, controls, distance);
  camera.updateMatrixWorld();
}

export function beginFirstPerson(camera) {
  const up = camera.up.clone().normalize();
  const forward = camera.getWorldDirection(new THREE.Vector3());
  const pitch = THREE.MathUtils.clamp(Math.asin(THREE.MathUtils.clamp(forward.dot(up), -1, 1)), -Math.PI * .47, Math.PI * .47);
  const horizontal = forward.clone().projectOnPlane(up);
  if (horizontal.lengthSq() < 1e-10) {
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion).projectOnPlane(up).normalize();
    horizontal.crossVectors(up, right);
  }
  forward.copy(horizontal.normalize()).multiplyScalar(Math.cos(pitch)).addScaledVector(up, Math.sin(pitch));
  // Keep the captured world-up direction, including models whose up is -Y.
  camera.lookAt(camera.position.clone().add(forward));
  camera.updateMatrixWorld();
  return { up, baseHeight: camera.position.dot(up), height: 0, pitch };
}

export function setWalkHeight(camera, controls, state, height, distance) {
  if (!Number.isFinite(height)) return;
  state.height = height;
  camera.position.addScaledVector(state.up, state.baseHeight + height - camera.position.dot(state.up));
  syncLookTarget(camera, controls, distance);
  camera.updateMatrixWorld();
}

export function moveFirstPerson(camera, controls, state, keys, seconds, speed, distance, free = false, analog = null) {
  if (!Number.isFinite(seconds) || !Number.isFinite(speed) || seconds <= 0 || speed <= 0) return;
  const axis = value => Number.isFinite(value) ? Math.max(-1, Math.min(1, value)) : 0;
  const forwardInput = Number(keys.has('KeyW')) - Number(keys.has('KeyS')) + axis(analog?.forward);
  const rightInput = Number(keys.has('KeyD')) - Number(keys.has('KeyA')) + axis(analog?.right);
  const upInput = free ? Number(keys.has('KeyE')) - Number(keys.has('KeyQ')) + axis(analog?.up) : 0;
  if (!forwardInput && !rightInput && !upInput) return;
  const forward = camera.getWorldDirection(new THREE.Vector3());
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion).projectOnPlane(state.up).normalize();
  if (!free) {
    forward.projectOnPlane(state.up);
    if (forward.lengthSq() < 1e-10) forward.crossVectors(state.up, right);
    forward.normalize();
  }
  const movement = forward.multiplyScalar(forwardInput).addScaledVector(right, rightInput).addScaledVector(state.up, upInput);
  const boost = keys.has('ShiftLeft') || keys.has('ShiftRight') || analog?.sprint ? 3 : 1;
  if (movement.lengthSq() > 1) movement.normalize();
  camera.position.addScaledVector(movement, speed * boost * Math.min(seconds, .1));
  if (!free) setWalkHeight(camera, controls, state, state.height, distance);
  else {
    syncLookTarget(camera, controls, distance);
    camera.updateMatrixWorld();
  }
}

export function fitCapturedFov(camera, horizontalFov) {
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(horizontalFov) / 2) / camera.aspect));
  camera.updateProjectionMatrix();
}
