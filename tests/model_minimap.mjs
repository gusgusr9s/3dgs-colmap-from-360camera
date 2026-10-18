import assert from 'node:assert/strict';
import { mapBasis, routeMap } from '../assets/app/model_minimap.mjs';

const capture = (x,y,z) => ({ transform_matrix: [[1,0,0,x],[0,1,0,y],[0,0,1,z],[0,0,0,1]] });
const zUp = routeMap([capture(0,0,5),capture(20,0,5),capture(20,10,6)], [0,0,1]);
assert.equal(zUp.points.length, 3);
assert.ok(zUp.points[0].pixel[0] < zUp.points[1].pixel[0]);
assert.ok(zUp.points[2].pixel[1] < zUp.points[1].pixel[1], 'Z-up maps XY, not XZ');
assert.deepEqual(zUp.pixel([20,0,999]), zUp.pixel([20,0,5]), 'height does not change the map');
assert.equal(zUp.heading([0,1,0]), 0);
assert.equal(zUp.heading([1,0,0]), 90);
assert.equal(zUp.heading([0,0,1]), null);
for (const p of zUp.points) {
  assert.equal(zUp.nearest(...p.pixel), p.index);
  assert.ok(p.pixel.every(Number.isFinite));
}
assert.equal(zUp.nearest(-1000, -1000), null);
const yUp = routeMap([capture(0,0,0),capture(2,0,-1)], [0,1,0]);
assert.ok(yUp.points[1].pixel[1] < yUp.points[0].pixel[1]);
assert.ok(yUp.pixel([999,0,999])[0] > yUp.width, 'off-route position remains detectable');
assert.deepEqual(mapBasis([0,0,0]).up, [0,1,0]);
assert.deepEqual(mapBasis([NaN,0,1]).up, [0,1,0]);
assert.ok(mapBasis([1,0,0]).right.every(Number.isFinite));
assert.equal(routeMap([], [0,0,1]), null);
const single = routeMap([capture(1,2,3)], [0,0,1]);
assert.deepEqual(single.points[0].pixel, [130,85]);
const invalid = routeMap([capture(NaN,0,0), {}, capture(1,2,3)], [0,0,1]);
assert.equal(invalid.points[0].index, 2, 'keep original captured-position indices');
const loop = routeMap([capture(0,0,0),capture(1,0,0),capture(0,0,0)], [0,0,1]);
assert.equal(loop.nearest(...loop.points[0].pixel,16,2), 2, 'select current visit at overlapping points');
console.log('Mini-map projection, picking, loop and world-up checks passed');
