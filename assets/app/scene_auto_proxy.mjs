// Generate a coarse room around an already transformed, world-space AABB.
// These boxes approximate the bounds; they do not reconstruct splat surfaces.
const MIN_FOOTPRINT = .1;
const MAX_DIAGONAL = 100000;
const MAX_COORDINATE = 1000000;
let fallbackId = 0;

const makeId = () => globalThis.crypto?.randomUUID?.()
  || `proxy-${Date.now()}-${++fallbackId}-${Math.random().toString(16).slice(2)}`;
const vector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);

function positive(value, label, maximum) {
  if (!Number.isFinite(value) || value < .001 || value > maximum) {
    throw new RangeError(`${label} must be between 0.001 and ${maximum}`);
  }
  return value;
}

/**
 * Return independent floor/wall colliders for the existing scene schema.
 *
 * bounds must already include the model's position, rotation and scale in world
 * coordinates. Floor top is min Y; wall inside faces follow min/max X and Z.
 * The floor supports the wall footprint too. Thin horizontal bounds expand
 * symmetrically to 0.1 units; default wall height is max(3, bounds height).
 * All dimensions are scene units, which are meters only after calibration.
 * The boxes remain in world space if the source model moves later.
 * Throws RangeError for empty, nonfinite, excessive or unsaveable geometry.
 */
export function generateCollisionRoom(bounds, options = {}) {
  if (!bounds || !vector(bounds.min) || !vector(bounds.max)) {
    throw new RangeError('Model bounds must contain finite min/max XYZ arrays');
  }
  const extents = bounds.max.map((value, axis) => value - bounds.min[axis]);
  const diagonal = Math.hypot(...extents);
  if (extents.some(value => value < 0) || !(diagonal > 0) || diagonal > MAX_DIAGONAL
      || [...bounds.min, ...bounds.max].some(value => Math.abs(value) > MAX_COORDINATE)) {
    throw new RangeError('Model bounds are empty or too large; align, crop or rescale the model first');
  }
  if (!options || typeof options !== 'object' || Array.isArray(options)) {
    throw new RangeError('Collision room options must be an object');
  }
  for (const flag of ['floor', 'walls']) {
    if (options[flag] !== undefined && typeof options[flag] !== 'boolean') {
      throw new RangeError(`${flag} must be true or false`);
    }
  }
  const thickness = positive(options.thickness ?? .15, 'Thickness', 1000);
  const height = positive(options.wallHeight ?? Math.max(3, extents[1]), 'Wall height', MAX_DIAGONAL);
  const width = Math.max(MIN_FOOTPRINT, extents[0]);
  const depth = Math.max(MIN_FOOTPRINT, extents[2]);
  const centerX = bounds.min[0] + extents[0] / 2;
  const centerZ = bounds.min[2] + extents[2] / 2;
  const bottom = bounds.min[1];
  const result = [];
  const add = (name, kind, position, size) => {
    if (!position.every(value => Number.isFinite(value) && Math.abs(value) <= MAX_COORDINATE)) {
      throw new RangeError('Generated geometry exceeds scene coordinate limits; move the model closer to the origin');
    }
    result.push({ id: makeId(), name, kind, position, yaw: 0, size, enabled: true });
  };
  if (options.floor !== false) {
    add('พื้นจากกรอบโมเดล (ประมาณ)', 'floor', [centerX, bottom - thickness / 2, centerZ],
      [width + 2 * thickness, thickness, depth + 2 * thickness]);
  }
  if (options.walls !== false) {
    const wallY = bottom + height / 2;
    add('กำแพง −X จากกรอบ (ประมาณ)', 'wall', [centerX - (width + thickness) / 2, wallY, centerZ],
      [thickness, height, depth]);
    add('กำแพง +X จากกรอบ (ประมาณ)', 'wall', [centerX + (width + thickness) / 2, wallY, centerZ],
      [thickness, height, depth]);
    add('กำแพง −Z จากกรอบ (ประมาณ)', 'wall', [centerX, wallY, centerZ - (depth + thickness) / 2],
      [width + 2 * thickness, height, thickness]);
    add('กำแพง +Z จากกรอบ (ประมาณ)', 'wall', [centerX, wallY, centerZ + (depth + thickness) / 2],
      [width + 2 * thickness, height, thickness]);
  }
  return result;
}
