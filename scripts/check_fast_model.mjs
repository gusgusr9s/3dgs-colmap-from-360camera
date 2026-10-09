// CPU codec check, using exactly the pinned viewer's numeric representation.
// SPARK_CODEC_DIAGNOSTICS=1 node --loader ./scripts/spark_node_loader.mjs ...
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import { decode_to_gsplatarray, decodeExtSplat, decodeExtRgb, initialization } from '../assets/vendor/spark-2.3.1/spark.module.js';

await initialization;
const [source, fast, report, decodedPly] = process.argv.slice(2);
async function decode(path, type) {
  const decoder = decode_to_gsplatarray(type);
  const file = await fs.open(path);
  try {
    for await (const chunk of file.createReadStream({ highWaterMark: 1024*1024 })) decoder.push(new Uint8Array(chunk));
  } finally { await file.close(); }
  const data = decoder.finish();
  const ext = data.to_extsplats();
  data.free();
  return ext;
}
const before = await decode(source, 'ply');
const after = await decode(fast, 'spz');
assert.equal(after.numSplats, before.numSplats);
assert.equal(after.maxShDegree, before.maxShDegree);
const first = new Float32Array(before.ext0.buffer), second = new Float32Array(after.ext0.buffer);
let maxPositionError = 0, positionSquared = 0;
for (let i=0; i<before.numSplats; ++i) for (let k=0; k<3; ++k) {
  const delta = Math.abs(first[4*i+k]-second[4*i+k]);
  assert.ok(Number.isFinite(delta));
  maxPositionError = Math.max(maxPositionError, delta); positionSquared += delta*delta;
}
// Coordinate-axis regressions would move the models by scene-scale distances.
assert.ok(maxPositionError <= .0005, `Position/coordinate error: ${maxPositionError}`);
function splat(ext,i) {
  const s = decodeExtSplat([ext.ext0,ext.ext1], i);
  return [...s.center.toArray(),...s.scales.toArray(),s.opacity,s.color.r,s.color.g,s.color.b,...s.quaternion.toArray()];
}
function sh(ext,i) {
  const base = 4*i;
  const words = [...ext.sh1.slice(base,base+4), ...ext.sh2.slice(base,base+4),
    ...ext.sh3a.slice(base,base+4), ...ext.sh3b.slice(base,base+3)];
  return words.flatMap(word => { const c=decodeExtRgb(word); return [c.r,c.g,c.b]; });
}
let colorMax=0, opacityMax=0, scaleLogMax=0, quaternionAngleMax=0, shSquared=0, shMax=0, samples=0;
const stride = Math.max(1,Math.floor(before.numSplats/65536));
for (let i=0;i<before.numSplats;i+=stride) {
  const a=splat(before,i), b=splat(after,i);
  for (let k=3;k<6;++k) scaleLogMax=Math.max(scaleLogMax,Math.abs(Math.log(a[k])-Math.log(b[k])));
  for (let k=7;k<10;++k) colorMax=Math.max(colorMax,Math.abs(a[k]-b[k]));
  opacityMax=Math.max(opacityMax,Math.abs(a[6]-b[6]));
  const dot=a.slice(10).reduce((sum,v,k)=>sum+v*b[10+k],0);
  quaternionAngleMax=Math.max(quaternionAngleMax,2*Math.acos(Math.min(1,Math.abs(dot)))*180/Math.PI);
  if (before.maxShDegree===3) {
    const sa=sh(before,i), sb=sh(after,i);
    sa.forEach((v,k)=>{const delta=Math.abs(v-sb[k]);shSquared+=delta*delta;shMax=Math.max(shMax,delta);});
  }
  ++samples;
}
const result={splats:after.numSplats,sh_degree:after.maxShDegree,position_samples:before.numSplats,
  max_position_error:maxPositionError,position_rmse:Math.sqrt(positionSquared/(3*before.numSplats)),
  appearance_samples:samples,max_rgb_error:colorMax,max_opacity_error:opacityMax,
  max_log_scale_error:scaleLogMax,max_rotation_error_degrees:quaternionAngleMax,
  max_sh_error:shMax,sh_rmse:Math.sqrt(shSquared/(samples*45)),
  limits:'CPU comparison of Spark decoded splats; does not measure browser FPS or image PSNR.'};
await fs.writeFile(report,JSON.stringify(result,null,2));
console.log(JSON.stringify(result));
if (decodedPly) {
  assert.equal(after.maxShDegree,3,'PLY diagnostic export currently requires SH degree 3');
  const names=['x','y','z',...Array.from({length:3},(_,i)=>'f_dc_'+i),...Array.from({length:45},(_,i)=>'f_rest_'+i),
    'opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3'];
  const header=`ply\nformat binary_little_endian 1.0\nelement vertex ${after.numSplats}\n`+
    names.map(n=>'property float '+n+'\n').join('')+'end_header\n';
  const file=await fs.open(decodedPly,'w');
  try {
    await file.write(header);
    const chunkSize=8192;
    for (let start=0;start<after.numSplats;start+=chunkSize) {
      const size=Math.min(chunkSize,after.numSplats-start), values=new Float32Array(size*59);
      for (let offset=0;offset<size;++offset) {
        const i=start+offset,s=splat(after,i),coeffs=sh(after,i), base=offset*59;
        values.set(s.slice(0,3),base);
        for (let c=0;c<3;++c) {
          values[base+3+c]=(s[7+c]-.5)/.28209479177387814;
          for (let k=0;k<15;++k) values[base+6+15*c+k]=coeffs[3*k+c];
        }
        const alpha=Math.max(1e-7,Math.min(1-1e-7,s[6]));
        values[base+51]=Math.log(alpha/(1-alpha));
        for (let k=0;k<3;++k) values[base+52+k]=Math.log(s[3+k]);
        values.set([s[13],s[10],s[11],s[12]],base+55);
      }
      await file.write(new Uint8Array(values.buffer));
    }
  } finally { await file.close(); }
}
