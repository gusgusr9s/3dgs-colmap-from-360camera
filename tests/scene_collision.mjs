import assert from 'node:assert/strict';
import { moveScenePlayer } from '../assets/app/scene_collision.mjs';

const EYE = 1.65;
const floor = (id, x = 0, top = 0, z = 0, width = 20, depth = 20) => ({
  id, kind: 'floor', position: [x, top - .1, z], size: [width, .2, depth], yaw: 0, enabled: true,
});
const wall = (id, x = 0, z = 0, yaw = 0, width = .05, depth = 20) => ({
  id, kind: 'wall', position: [x, 1.5, z], size: [width, 3, depth], yaw, enabled: true,
});
const near = (actual, expected, label) => assert.ok(Math.abs(actual - expected) < 1e-5, `${label}: ${actual} vs ${expected}`);

let result = moveScenePlayer([0, EYE, 0], [1, -.1, 1], [floor('ground')]);
result.position.forEach((value, axis) => near(value, [1, EYE, 1][axis], 'Ground contact keeps eye height while walking'));
assert.equal(result.grounded, true);
result = moveScenePlayer([0, 5, 0], [0, -20, 0], [floor('ground')]);
near(result.position[1], EYE, 'A large fall lands on the floor');
assert.equal(result.grounded, true);
result = moveScenePlayer([0, 5, 0], [0, -20, 0], [floor('lower'), floor('upper', 0, 2)]);
near(result.position[1], EYE + 2, 'The highest crossed floor catches a falling player');
result = moveScenePlayer([0, EYE, 0], [0, 10, 0], [floor('ground'), floor('ceiling', 0, 3)]);
near(result.position[1], 2.8, 'The top of the body cannot pass through a ceiling');

result = moveScenePlayer([-2, EYE, 0], [100, 0, 0], [wall('thin')]);
near(result.position[0], -.275, 'Large movement cannot tunnel through a thin wall');
result = moveScenePlayer([-1, EYE, 0], [3, 0, 2], [wall('thin')]);
near(result.position[0], -.275, 'Moving diagonally stops against a wall');
near(result.position[2], 2, 'The remaining movement slides along the wall');
result = moveScenePlayer([-1, EYE, -1], [3, 0, 3], [wall('x'), wall('z', 0, 0, Math.PI / 2)]);
near(result.position[0], -.275, 'An inside corner blocks X');
near(result.position[2], -.275, 'An inside corner blocks Z');

const angle = Math.PI / 4;
const normal = [Math.cos(angle), -Math.sin(angle)];
const tangent = [Math.sin(angle), Math.cos(angle)];
result = moveScenePlayer([-2 * normal[0], EYE, -2 * normal[1]],
  [4 * normal[0] + tangent[0], 0, 4 * normal[1] + tangent[1]], [wall('rotated', 0, 0, angle)]);
near(result.position[0] * normal[0] + result.position[2] * normal[1], -.275, 'Rotated box uses its local collision normal');
near(result.position[0] * tangent[0] + result.position[2] * tangent[1], 1, 'Rotated wall preserves tangent motion');

const steps = [floor('low', -2.5, 0, 0, 5), floor('step', 2.5, .25, 0, 5)];
result = moveScenePlayer([-1, EYE, 0], [2, 0, 0], steps);
near(result.position[0], 1, 'Walk onto a small step');
near(result.position[1], EYE + .25, 'Small steps raise the eye');
assert.equal(result.grounded, true);
result = moveScenePlayer([1, EYE + .25, 0], [-2, 0, 0], steps);
near(result.position[1], EYE, 'Walking down a small step follows the floor');
result = moveScenePlayer([-1, EYE, 0], [2, 0, 0], [steps[0], floor('tall', 2.5, .5, 0, 5)]);
near(result.position[0], -.25, 'A ledge above stepHeight blocks the player');
near(result.position[1], EYE, 'A tall ledge does not raise the player');
result = moveScenePlayer([-1, EYE, 0], [2, 0, 0], [floor('left', -2.5, 0, 0, 5), floor('right', 2.5, 0, 0, 5)]);
near(result.position[0], 1, 'Adjacent level floor boxes do not create a blocking seam');
result = moveScenePlayer([-1, EYE, 0], [2, 0, 0], [...steps, floor('low ceiling', 0, 2, 0, 20)]);
assert.ok(result.position[0] <= -.25 + 1e-5, 'A step cannot push the head through a low ceiling');
result = moveScenePlayer([-1, EYE + .6, 0], [2, 0, 0], [steps[0], floor('high step', 2.5, .8, 0, 5)]);
near(result.position[1], EYE + .6, 'An airborne player cannot climb a step in mid-air');

result = moveScenePlayer([0, EYE, 0], [1, -.1, 0], [{ ...floor('off'), enabled: false }]);
near(result.position[1], EYE - .1, 'Disabled floors provide no support');
assert.equal(result.grounded, false);
result = moveScenePlayer([-1, EYE, 0], [2, 0, 0], [{ ...wall('off'), enabled: false }]);
near(result.position[0], 1, 'Disabled walls do not block movement');
result = moveScenePlayer([0, EYE, 0], [0, -.1, 0], []);
assert.equal(result.grounded, false, 'A scene without floors remains airborne');
result = moveScenePlayer([NaN, Infinity, -Infinity], [NaN, 1, Infinity], [null, {}, { ...wall('bad'), size: [0, 1, 1] }], { radius: NaN, height: Infinity });
assert.ok(result.position.every(Number.isFinite), 'Invalid data produces finite camera coordinates');
result = moveScenePlayer([-2, EYE, 0], [1e300, 0, 0], [wall('limit')]);
near(result.position[0], -.275, 'Bounded processing of extreme deltas still prevents tunneling');

const originalPosition = [-1, EYE, 0];
const originalDelta = [2, 0, 0];
const originalBoxes = [wall('unchanged')];
const snapshot = JSON.stringify(originalBoxes);
moveScenePlayer(originalPosition, originalDelta, originalBoxes);
assert.deepEqual(originalPosition, [-1, EYE, 0]);
assert.deepEqual(originalDelta, [2, 0, 0]);
assert.equal(JSON.stringify(originalBoxes), snapshot, 'Collision does not mutate saved scene data');
console.log('Scene collision passed: floors, falls, ceilings, thin/yawed walls, sliding, corners, steps, seams, disabled boxes and invalid input.');
