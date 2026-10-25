"""Install additional official Three.js loaders pinned to the portal runtime."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
LOADERS = ('OBJLoader.js', 'MTLLoader.js', 'PLYLoader.js')


def main():
    manifest_path = ROOT/'vendor'/'loaders.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for filename in LOADERS:
        relative = 'three/examples/jsm/loaders/'+filename
        url = 'https://cdn.jsdelivr.net/npm/three@0.186.1/examples/jsm/loaders/'+filename
        path = ROOT/'vendor'/relative
        expected = manifest.get(relative, {}).get('sha256')
        if path.exists() and expected and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            print('Verified', filename)
            continue
        data = urlopen(url, timeout=30).read()
        digest = hashlib.sha256(data).hexdigest()
        if expected and digest != expected:
            raise ValueError('Checksum mismatch: '+filename)
        path.write_bytes(data)
        manifest[relative] = {'url': url, 'sha256': digest, 'bytes':len(data)}
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
        print('Installed', filename, len(data))


if __name__ == '__main__': main()
