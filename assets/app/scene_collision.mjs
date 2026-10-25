// Walk-mode collision against the simple boxes stored in a scene. Coordinates
// use world Y up; position is the camera's eye, not the player's feet.
const EPSILON = 1e-6;
const MAX_SUBSTEPS = 4096;

const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;
const validVector = value => Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every(Number.isFinite);

function boxes(colliders) {
  return (Array.isArray(colliders) ? colliders : []).flatMap(box => {
    if (!box || box.enabled === false || !['floor', 'wall'].includes(box.kind)
        || !validVector(box.position) || !validVector(box.size)
        || box.size.some(value => value <= 0)
        || (box.yaw !== undefined && !Number.isFinite(box.yaw))) return [];
    const rotation = box.rotation || [0, box.yaw || 0, 0];
    if (Math.abs(rotation[0]) > 1e-7 || Math.abs(rotation[2]) > 1e-7) return [];
    const yaw = rotation[1];
    return [{
      x: box.position[0], z: box.position[2],
      bottom: box.position[1] - box.size[1] / 2,
      top: box.position[1] + box.size[1] / 2,
      halfX: box.size[0] / 2, halfZ: box.size[2] / 2,
      cosine: Math.cos(yaw), sine: Math.sin(yaw),
    }];
  });
}

function tiltedCollisionBoxes(colliders) {
  return (Array.isArray(colliders) ? colliders : []).flatMap(box => {
    if (!box) return [];
    const r = box.rotation;
    if (box.enabled === false || !validVector(r) || !validVector(box.position) || !validVector(box.size)
        || box.size.some(v => v <= 0) || (Math.abs(r[0]) < 1e-7 && Math.abs(r[2]) < 1e-7)) return [];
    const [x,y,z]=r, sx=Math.sin(x),cx=Math.cos(x),sy=Math.sin(y),cy=Math.cos(y),sz=Math.sin(z),cz=Math.cos(z);
    return [{position:box.position,half:box.size.map(v=>v/2),matrix:[[cy*cz,-cy*sz,sy],[sx*sy*cz+cx*sz,-sx*sy*sz+cx*cz,-sx*cy],[-cx*sy*cz+sx*sz,cx*sy*sz+sx*cz,cx*cy]]}];
  });
}

function resolveTiltedBoxes(position, solids, radius, height) {
  let grounded=false; const r=Math.min(radius,height/2);
  for(let pass=0;pass<5;pass++) for(const box of solids) {
    const local = eye => [0,1,2].map(axis => box.matrix.reduce((sum,row,k)=>sum+row[axis]*(eye[k]-box.position[k]),0));
    const a=local([position[0],position[1]-height+r,position[2]]),b=local([position[0],position[1]-r,position[2]]);
    const sample=t=>{const p=a.map((v,k)=>v+(b[k]-v)*t),q=p.map((v,k)=>Math.max(-box.half[k],Math.min(box.half[k],v)));return {p,q,d:p.reduce((sum,v,k)=>sum+(v-q[k])**2,0)};};
    let lo=0,hi=1; for(let i=0;i<28;i++){const l=lo+(hi-lo)/3,h=hi-(hi-lo)/3;if(sample(l).d<sample(h).d)hi=h;else lo=l;}
    const closest=[sample(0),sample(1),sample((lo+hi)/2)].sort((u,v)=>u.d-v.d)[0];
    const distance=Math.sqrt(closest.d); if(distance>r+1e-5)continue;
    let normal,penetration;
    if(distance>1e-8){normal=closest.p.map((v,k)=>(v-closest.q[k])/distance);penetration=Math.max(0,r-distance);}
    else {
      let best=Infinity;normal=[0,0,0];
      for(let k=0;k<3;k++)for(const sign of [-1,1]){const amount=sign>0?box.half[k]-Math.min(a[k],b[k])+r:Math.max(a[k],b[k])+box.half[k]+r;
        if(amount<best){best=amount;normal=[0,0,0];normal[k]=sign;}}
      penetration=best;
    }
    const world=box.matrix.map(row=>row.reduce((sum,v,k)=>sum+v*normal[k],0));
    for(let k=0;k<3;k++)position[k]+=world[k]*penetration;
    if(world[1]>.5)grounded=true;
  }
  return grounded;
}

function localPosition(position, box) {
  const x = position[0] - box.x;
  const z = position[2] - box.z;
  return [box.cosine * x - box.sine * z, box.sine * x + box.cosine * z];
}

function overlapsFootprint(position, box, radius) {
  const [x, z] = localPosition(position, box);
  const dx = Math.max(Math.abs(x) - box.halfX, 0);
  const dz = Math.max(Math.abs(z) - box.halfZ, 0);
  return Math.hypot(dx, dz) < radius - EPSILON;
}

function bodyOverlaps(position, box, height) {
  return position[1] - height < box.top - EPSILON && position[1] > box.bottom + EPSILON;
}

function support(position, solids, radius, height, distance = EPSILON * 4) {
  const feet = position[1] - height;
  let highest = -Infinity;
  for (const box of solids) {
    if (box.top <= feet + EPSILON * 4 && box.top >= feet - distance - EPSILON
        && overlapsFootprint(position, box, radius)) highest = Math.max(highest, box.top);
  }
  return highest;
}

function clearBody(position, solids, radius, height) {
  return !solids.some(box => bodyOverlaps(position, box, height) && overlapsFootprint(position, box, radius));
}

function resolveHorizontal(position, solids, radius, height) {
  // Several passes retain sliding along a wall and resolve corners shared by
  // multiple boxes, without depending on the scene's collider order.
  for (let pass = 0; pass < 8; pass++) {
    let changed = false;
    for (const box of solids) {
      if (!bodyOverlaps(position, box, height)) continue;
      const [x, z] = localPosition(position, box);
      const closestX = Math.max(-box.halfX, Math.min(box.halfX, x));
      const closestZ = Math.max(-box.halfZ, Math.min(box.halfZ, z));
      const dx = x - closestX;
      const dz = z - closestZ;
      const distance = Math.hypot(dx, dz);
      if (distance >= radius - EPSILON) continue;
      let pushX;
      let pushZ;
      if (distance > EPSILON) {
        const penetration = radius - distance;
        pushX = dx / distance * penetration;
        pushZ = dz / distance * penetration;
      } else if (box.halfX - Math.abs(x) < box.halfZ - Math.abs(z)) {
        pushX = (x < 0 ? -1 : 1) * (box.halfX - Math.abs(x) + radius);
        pushZ = 0;
      } else {
        pushX = 0;
        pushZ = (z < 0 ? -1 : 1) * (box.halfZ - Math.abs(z) + radius);
      }
      position[0] += box.cosine * pushX + box.sine * pushZ;
      position[2] += -box.sine * pushX + box.cosine * pushZ;
      changed = true;
    }
    if (!changed) break;
  }
}

/**
 * Move a standing cylindrical player against enabled floor/wall OBBs.
 *
 * Box position is its center, size its full dimensions, and yaw its rotation
 * around world Y in radians (matching Three.js). Vertical delta is supplied by
 * the caller, e.g. gravity * frame time. This function does not integrate time.
 * A supported player can step up/down by stepHeight; airborne players only land
 * when their feet cross a box's top. Returned inputs are never mutated.
 *
 * Motion is substepped to prevent tunneling through thin boxes. Extremely large
 * deltas are shortened to MAX_SUBSTEPS safe increments to bound per-frame work.
 */
export function moveScenePlayer(positionArray, deltaArray, colliders, options = {}) {
  options ??= {};
  const radius = Math.max(.01, finite(options.radius, .25));
  const height = Math.max(.05, finite(options.height, 1.65));
  const stepHeight = Math.max(0, finite(options.stepHeight, .3));
  const position = [0, 1, 2].map(index => finite(positionArray?.[index], 0));
  const delta = [0, 1, 2].map(index => finite(deltaArray?.[index], 0));
  const solids = boxes(colliders);
  const tilted = tiltedCollisionBoxes(colliders);
  let tiltGrounded = false;
  const increment = Math.min(.1, radius * .4);
  const distance = Math.hypot(...delta);
  const travelLimit = increment * MAX_SUBSTEPS;
  if (distance > travelLimit) {
    // Divide first so even enormous finite input cannot overflow the multiplier.
    for (let axis = 0; axis < 3; axis++) delta[axis] = delta[axis] / distance * travelLimit;
  }
  const steps = Math.max(1, Math.min(MAX_SUBSTEPS, Math.ceil(Math.hypot(...delta) / increment)));
  const movement = delta.map(value => value / steps);

  for (let index = 0; index < steps; index++) {
    const wasGrounded = Number.isFinite(support(position, solids, radius, height));
    const next = [position[0] + movement[0], position[1], position[2] + movement[2]];

    if (wasGrounded && movement[1] <= EPSILON && stepHeight > 0) {
      const feet = position[1] - height;
      const stepTops = solids.filter(box => box.top > feet + EPSILON
        && box.top <= feet + stepHeight + EPSILON && overlapsFootprint(next, box, radius))
        .map(box => box.top).sort((a, b) => b - a);
      for (const top of stepTops) {
        const raised = [next[0], top + height, next[2]];
        if (clearBody(raised, solids, radius, height)) {
          next[1] = raised[1];
          break;
        }
      }
    }

    resolveHorizontal(next, solids, radius, height);
    const oldEye = next[1];
    const oldFeet = oldEye - height;
    let newEye = oldEye + movement[1];
    if (movement[1] <= 0) {
      let landing = -Infinity;
      for (const box of solids) {
        if (box.top <= oldFeet + EPSILON * 4 && box.top >= newEye - height - EPSILON
            && overlapsFootprint(next, box, radius)) landing = Math.max(landing, box.top);
      }
      if (Number.isFinite(landing)) newEye = landing + height;
    } else {
      for (const box of solids) {
        if (box.bottom >= oldEye - EPSILON && box.bottom < newEye
            && overlapsFootprint(next, box, radius)) newEye = Math.min(newEye, box.bottom);
      }
    }
    next[1] = newEye;

    if (wasGrounded && movement[1] <= EPSILON && (movement[0] || movement[2])) {
      const lowerSupport = support(next, solids, radius, height, stepHeight);
      if (Number.isFinite(lowerSupport)) next[1] = lowerSupport + height;
    }
    tiltGrounded = resolveTiltedBoxes(next, tilted, radius, height);
    // Malformed/extreme source coordinates must never poison camera matrices.
    for (let axis = 0; axis < 3; axis++) position[axis] = finite(next[axis], position[axis]);
  }
  return { position, grounded: tiltGrounded || Number.isFinite(support(position, solids, radius, height)) };
}
