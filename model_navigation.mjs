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

export function fitCapturedFov(camera, horizontalFov) {
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(horizontalFov) / 2) / camera.aspect));
  camera.updateProjectionMatrix();
}
