// Regenerate the small browser-test fixture from the repository's Gaussian PLY.
import { registerHooks } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';

registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier === 'three') return { url: new URL('./vendor/three/build/three.module.js', import.meta.url).href, shortCircuit: true };
  if (specifier === 'three/addons/postprocessing/Pass.js') return { url: new URL('./vendor/three/examples/jsm/postprocessing/Pass.js', import.meta.url).href, shortCircuit: true };
  return nextResolve(specifier, context);
} });

const { transcodeSpz } = await import('./vendor/spark/spark.module.js');
const source = new URL('../desktop/tests/fixtures/splat.ply', import.meta.url);
const result = await transcodeSpz({
  inputs: [{ fileBytes: new Uint8Array(await readFile(source)), fileType: 'ply' }],
  maxSh: 3, fractionalBits: 12, version: 3,
});
await writeFile(new URL('./test_splat.spz', import.meta.url), result.fileBytes);
console.log(`SPZ fixture: ${result.fileBytes.length} bytes`);
