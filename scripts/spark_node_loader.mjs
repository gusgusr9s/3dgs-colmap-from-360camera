// Resolve the viewer's pinned, local Spark/Three modules in Node for conversion.
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';

const vendor = new URL('../assets/vendor/', import.meta.url);
export async function resolve(specifier, context, nextResolve) {
  const paths = {
    three: 'three-0.180.0/three.module.js',
    'three/addons/postprocessing/Pass.js': 'three-0.180.0/Pass.js',
  };
  if (paths[specifier]) return { url: new URL(paths[specifier], vendor).href, shortCircuit: true };
  return nextResolve(specifier, context);
}
export async function load(url, context, nextLoad) {
  if (url.startsWith(vendor.href) && url.endsWith('.js')) {
    let source = await readFile(fileURLToPath(url), 'utf8');
    // Diagnostics use the same pinned decoder, without a browser or GPU.
    if (process.env.SPARK_CODEC_DIAGNOSTICS === '1' && url.endsWith('/spark.module.js')) {
      source += '\nexport { decode_to_gsplatarray, decodeExtSplat, decodeExtRgb, initialization };\n';
    }
    return { format: 'module', source, shortCircuit: true };
  }
  return nextLoad(url, context);
}
