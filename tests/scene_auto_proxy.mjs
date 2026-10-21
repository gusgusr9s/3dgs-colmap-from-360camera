import assert from 'node:assert/strict';
import { generateCollisionRoom } from '../assets/app/scene_auto_proxy.mjs';
import { moveScenePlayer } from '../assets/app/scene_collision.mjs';

const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-7, `${message}: ${actual} vs ${expected}`);
const bounds = { min: [-4, 2, -3], max: [6, 7, 5] };
const snapshot = JSON.stringify(bounds);
const room = generateCollisionRoom(bounds);
assert.equal(room.length, 5);
assert.equal(new Set(room.map(box => box.id)).size, 5, 'Every generated collider has an independent ID');
for (const box of room) {
  assert.match(box.id, /^[A-Za-z0-9_-]{1,80}$/);
  assert.ok(box.name.length > 0 && box.name.length <= 160);
  assert.equal(box.enabled, true);
  assert.equal(box.yaw, 0);
  assert.ok(box.position.every(Number.isFinite));
  assert.ok(box.size.every(value => Number.isFinite(value) && value >= .001));
}
const [floor, left, right, front, back] = room;
assert.equal(floor.kind, 'floor');
assert.ok(room.slice(1).every(box => box.kind === 'wall'));
near(floor.position[1] + floor.size[1] / 2, 2, 'Floor top aligns with model min Y');
near(left.position[0] + left.size[0] / 2, -4, 'Left inside face follows min X');
near(right.position[0] - right.size[0] / 2, 6, 'Right inside face follows max X');
near(front.position[2] + front.size[2] / 2, -3, 'Front inside face follows min Z');
near(back.position[2] - back.size[2] / 2, 5, 'Back inside face follows max Z');
for (const wall of room.slice(1)) {
  near(wall.position[1] - wall.size[1] / 2, 2, 'Walls start at floor top');
  near(wall.size[1], 5, 'Default walls cover model height');
}
near(front.position[0] - front.size[0] / 2, left.position[0] - left.size[0] / 2, 'Front extends to outside left edge');
near(back.position[0] + back.size[0] / 2, right.position[0] + right.size[0] / 2, 'Back extends to outside right edge');
near(floor.size[0], front.size[0], 'Floor supports the corner boxes');

let player = moveScenePlayer([0, 10, 0], [0, -20, 0], room);
near(player.position[1], 3.65, 'Player lands on generated floor');
assert.equal(player.grounded, true);
player = moveScenePlayer([0, 3.65, 0], [20, 0, 20], room);
assert.ok(player.position[0] <= 5.75 + 1e-7 && player.position[2] <= 4.75 + 1e-7, 'Joined generated walls stop diagonal escape at corner');
assert.equal(player.grounded, true);

assert.equal(generateCollisionRoom(bounds, { walls: false }).length, 1, 'Floor can be generated separately');
assert.equal(generateCollisionRoom(bounds, { floor: false }).length, 4, 'Walls can be generated separately');
assert.deepEqual(generateCollisionRoom(bounds, { floor: false, walls: false }), []);
const options = { thickness: .4, wallHeight: 2.3 };
const optionsSnapshot = JSON.stringify(options);
const custom = generateCollisionRoom(bounds, options);
near(custom[0].size[1], .4, 'Custom thickness applied');
near(custom[1].size[1], 2.3, 'Custom wall height applied');
assert.equal(JSON.stringify(options), optionsSnapshot, 'Options stay untouched');
assert.equal(JSON.stringify(bounds), snapshot, 'Bounds stay untouched');
assert.ok(!generateCollisionRoom(bounds).some(box => room.some(old => old.id === box.id)), 'Repeated generation creates fresh IDs');

const thin = generateCollisionRoom({ min: [2, 0, 3], max: [2, 0, 8] });
near(thin[0].position[0], 2, 'Thin scene stays centered');
near(thin[0].size[0], .4, 'Thin X footprint grows to minimum plus walls');
near(thin[1].size[1], 3, 'Flat scene gets default wall height');
const transformed = generateCollisionRoom({ min: [100, -5, -20], max: [108, -3, -16] });
near(transformed[0].position[0], 104, 'Already transformed world bounds preserve world X');
near(transformed[0].position[2], -18, 'Already transformed world bounds preserve world Z');
near(transformed[0].position[1] + transformed[0].size[1] / 2, -5, 'Translated world floor preserves min Y');

for (const invalid of [
  null, {}, { min: [], max: [] }, { min: [0, 0, 0], max: [NaN, 1, 1] },
  { min: [-Infinity, 0, 0], max: [1, 1, 1] },
  { min: [2, 0, 0], max: [1, 1, 1] },
  { min: [0, 0, 0], max: [0, 0, 0] },
  { min: [0, 0, 0], max: [100001, 1, 1] },
  { min: [-1e300, 0, 0], max: [1e300, 1, 1] },
  { min: [1e6 + 1, 0, 0], max: [1e6 + 2, 1, 1] },
  { min: [0, -1e6, 0], max: [1, -999999, 1] },
]) assert.throws(() => generateCollisionRoom(invalid), RangeError, 'Invalid or unsaveable bounds rejected');
for (const invalid of [null, [], { thickness: 0 }, { thickness: -.1 }, { thickness: Infinity },
  { thickness: 1001 }, { wallHeight: NaN }, { wallHeight: 0 }, { wallHeight: 100001 },
  { floor: 'false' }, { walls: 0 }]) {
  assert.throws(() => generateCollisionRoom(bounds, invalid), RangeError, 'Invalid options rejected');
}
console.log('Scene auto proxy passed: world bounds, floor level, closed corners, collision, subsets, thin scenes, fresh IDs and invalid/extreme geometry.');
