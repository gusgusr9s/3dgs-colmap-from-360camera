// Use Spark's own encoder so file axes, quaternion order and SH match the viewer.
import fs from 'node:fs/promises';
import { transcodeSpz } from '../assets/vendor/spark-2.3.1/spark.module.js';

const [source, target, bits = '12'] = process.argv.slice(2);
if (!source || !target) throw new Error('Usage: encode_model_spz.mjs input.ply output.spz [fractional-bits]');
const result = await transcodeSpz({
  inputs: [{ fileBytes: new Uint8Array(await fs.readFile(source)), fileType: 'ply' }],
  maxSh: 3, fractionalBits: Number(bits), version: 3,
});
await fs.writeFile(target, result.fileBytes);
console.log(JSON.stringify({ bytes: result.fileBytes.length, spark: '2.3.1', version: 3, fractionalBits: Number(bits) }));
