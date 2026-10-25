"""Install pinned local JS assets so model viewing does not depend on a CDN."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    'three-0.180.0/three.module.js': 'https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js',
    'three-0.180.0/three.core.js': 'https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.core.js',
    'three-0.180.0/OrbitControls.js': 'https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/OrbitControls.js',
    'three-0.180.0/TransformControls.js': 'https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/TransformControls.js',
    'three-0.180.0/examples/jsm/loaders/GLTFLoader.js': 'https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/loaders/GLTFLoader.js',
    'three-0.180.0/examples/jsm/utils/BufferGeometryUtils.js': 'https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/utils/BufferGeometryUtils.js',
    'three-0.180.0/Pass.js': 'https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/postprocessing/Pass.js',
    'three-0.180.0/LICENSE': 'https://cdn.jsdelivr.net/npm/three@0.180.0/LICENSE',
    'spark-2.3.1/spark.module.js': 'https://sparkjs.dev/releases/spark/2.3.1/spark.module.js',
    'spark-2.3.1/LICENSE': 'https://raw.githubusercontent.com/sparkjsdev/spark/v2.3.1/LICENSE',
}


def main():
    root = ROOT / 'assets/vendor'
    manifest_path = root / 'manifest.json'
    recorded = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    for name, url in ASSETS.items():
        target = root / name
        expected = recorded.get(name, {}).get('sha256')
        if target.is_file() and expected and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            print('Verified', name, flush=True)
            continue
        data = urlopen(url, timeout=60).read()
        digest = hashlib.sha256(data).hexdigest()
        if expected and digest != expected:
            raise RuntimeError('Viewer asset checksum mismatch: ' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        recorded[name] = {'url': url, 'sha256': digest, 'bytes': len(data)}
        manifest_path.write_text(json.dumps(recorded, indent=2) + '\n')
        print('Installed', name, len(data), flush=True)


if __name__ == '__main__':
    main()
