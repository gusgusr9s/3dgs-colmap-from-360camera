// SPZ v3 -> KHR_gaussian_splatting. Decode stored values directly, without
// repacking through a viewer's lower-precision GPU representation. CPU only.
// Format reference: https://github.com/nianticlabs/spz (MIT), load-spz.cc / splat-utils.h.
import fs from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';

const [source, target, metadataPath, progressPath] = process.argv.slice(2);
const progress = async (value) => {
  if (progressPath) await fs.writeFile(progressPath, JSON.stringify({progress: value}));
};
await progress(5);
const packed = gunzipSync(await fs.readFile(source), {maxOutputLength: 1024*1024*1024});
if (packed.length < 16 || packed.readUInt32LE(0) !== 0x5053474e || packed.readUInt32LE(4) !== 3 || packed[14] & ~1 || packed[15])
  throw new Error('Only SPZ v3 without extensions is supported');
const count = packed.readUInt32LE(8), degree = packed[12], fractionalBits = packed[13];
if (!count || !Number.isInteger(degree) || degree < 0 || degree > 3) throw new Error('Invalid SPZ count / SH degree');
const coefficientCount = (degree + 1) ** 2;
if (fractionalBits > 24 || packed.length !== 16 + count * (20 + (coefficientCount-1)*3)) throw new Error('Invalid SPZ payload');
const alphaOffset = 16 + count*9, colorOffset = alphaOffset + count;
const scaleOffset = colorOffset + count*3, rotationOffset = scaleOffset + count*3, shOffset = rotationOffset + count*4;
const position = (i, k) => packed.readIntLE(16 + i*9 + k*3, 3) / 2**fractionalBits;
function rotation(i) {
  let bits = packed.readUInt32LE(rotationOffset + 4*i);
  const largest = bits >>> 30, quaternion = [0,0,0,0];
  let sum = 0;
  for (let k = 3; k >= 0; --k) if (k !== largest) {
    const value = (bits & 511) / (511 * Math.SQRT2) * (bits & 512 ? -1 : 1);
    quaternion[k] = value; sum += value*value; bits >>>= 10;
  }
  if (sum > 1) throw new Error('Invalid SPZ quaternion');
  quaternion[largest] = Math.sqrt(1-sum);
  return quaternion;
}
const floats = 11 + coefficientCount * 3, stride = floats * 4;
const binLength = count * stride;
if (binLength > 0xffffffff - 65536) throw new Error('Model exceeds the GLB 4 GB limit');
const extension = 'KHR_gaussian_splatting';
const attrs = {}, accessors = [];
let byteOffset = 0;
function attribute(name, type, size) {
  attrs[name] = accessors.length;
  accessors.push({bufferView: 0, byteOffset, componentType: 5126, count, type});
  byteOffset += size * 4;
}
attribute('POSITION', 'VEC3', 3);
attribute(`${extension}:ROTATION`, 'VEC4', 4);
attribute(`${extension}:SCALE`, 'VEC3', 3);
attribute(`${extension}:OPACITY`, 'SCALAR', 1);
for (let band = 0; band <= degree; ++band)
  for (let k = 0; k < 2 * band + 1; ++k) attribute(`${extension}:SH_DEGREE_${band}_COEF_${k}`, 'VEC3', 3);
const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
for (let i = 0; i < count; ++i) for (let k = 0; k < 3; ++k) {
  const value = position(i, k);
  if (!Number.isFinite(value)) throw new Error('Non-finite position');
  min[k] = Math.min(min[k], value); max[k] = Math.max(max[k], value);
}
Object.assign(accessors[0], {min, max});
const extras = metadataPath ? JSON.parse(await fs.readFile(metadataPath, 'utf8')) : {};
const gltf = {
  asset: {version: '2.0', generator: '360-to-3D / SPZ v3 to GLB'},
  extensionsUsed: [extension], extensionsRequired: [extension],
  scene: 0, scenes: [{nodes: [0], extras}], nodes: [{mesh: 0}],
  meshes: [{primitives: [{mode: 0, attributes: attrs,
    extensions: {[extension]: {kernel: 'ellipse', colorSpace: 'srgb_rec709_display', projection: 'perspective', sortingMethod: 'cameraDistance'}}}]}],
  buffers: [{byteLength: binLength}],
  bufferViews: [{buffer: 0, byteOffset: 0, byteLength: binLength, byteStride: stride, target: 34962}], accessors,
};
const json = Buffer.from(JSON.stringify(gltf));
const jsonLength = (json.length + 3) & ~3;
const header = Buffer.alloc(20); header.writeUInt32LE(0x46546c67, 0); header.writeUInt32LE(2, 4);
header.writeUInt32LE(28 + jsonLength + binLength, 8); header.writeUInt32LE(jsonLength, 12); header.writeUInt32LE(0x4e4f534a, 16);
const binHeader = Buffer.alloc(8); binHeader.writeUInt32LE(binLength, 0); binHeader.writeUInt32LE(0x004e4942, 4);
const file = await fs.open(target, 'wx');
try {
  // FileHandle.write can be partial; writeFile writes the complete chunk at the current position.
  await file.writeFile(header); await file.writeFile(json);
  await file.writeFile(Buffer.alloc(jsonLength - json.length, 32)); await file.writeFile(binHeader);
  for (let start = 0; start < count; start += 8192) {
    const size = Math.min(8192, count - start), bytes = Buffer.alloc(size * stride);
    for (let offset = 0; offset < size; ++offset) {
      const i = start + offset;
      const values = [position(i,0), position(i,1), position(i,2), ...rotation(i)];
      for (let k = 0; k < 3; ++k) values.push(Math.exp(packed[scaleOffset+i*3+k]/16-10));
      values.push(packed[alphaOffset+i]/255);
      for (let k = 0; k < 3; ++k) values.push((packed[colorOffset+i*3+k]/255-.5)/.15);
      for (let k = 0; k < (coefficientCount-1)*3; ++k) values.push((packed[shOffset+i*(coefficientCount-1)*3+k]-128)/128);
      if (values.some(v => !Number.isFinite(v)))
        throw new Error(`Invalid Gaussian at ${i}`);
      values.forEach((value, k) => bytes.writeFloatLE(value, offset * stride + 4 * k));
    }
    await file.writeFile(bytes);
    await progress(Math.floor(15 + 80 * (start + size) / count));
  }
} finally { await file.close(); }
console.log(JSON.stringify({splats: count, sh_degree: degree, bytes: 28 + jsonLength + binLength, bounds: {min, max}}));
