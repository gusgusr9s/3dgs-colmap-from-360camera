"""Install the pinned Three.js dependency closure used by offline GLB packages."""
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.186.1'
DEST=ROOT/'assets/vendor'/('three-'+VERSION)
ENTRY_POINTS=['build/three.module.js','build/three.webgpu.js','build/three.tsl.js',
              'examples/jsm/loaders/GLTFLoader.js','examples/jsm/loaders/GLTFGaussianSplatLoaderExtension.js',
              'examples/jsm/controls/OrbitControls.js']
BARE={'three':'build/three.module.js','three/webgpu':'build/three.webgpu.js','three/tsl':'build/three.tsl.js'}
IMPORTS=re.compile(r'''(?:^\s*import\s+(?:[^;'"`]*?\s+from\s*)?|^\s*export\s+[^;'"`]*?\s+from\s*|\bimport\s*\(\s*)['"]([A-Za-z0-9_@./-]+)['"]''', re.MULTILINE)


def normalize_relative(current, dependency):
    if dependency in BARE:return BARE[dependency]
    if dependency.startswith('three/addons/'):return 'examples/jsm/'+dependency.removeprefix('three/addons/')
    if not dependency.startswith('.'):
        raise ValueError('Unexpected dependency: '+dependency)
    parts=[]
    for part in (PurePosixPath(current).parent/PurePosixPath(dependency)).parts:
        if part=='..':
            if not parts:raise ValueError('Asset outside package')
            parts.pop()
        elif part not in ('','.'):parts.append(part)
    return '/'.join(parts)


def install():
    package=json.loads(urlopen('https://registry.npmjs.org/three/'+VERSION,timeout=60).read())
    archive=urlopen(package['dist']['tarball'],timeout=60).read()
    algorithm,expected=package['dist']['integrity'].split('-',1)
    if algorithm!='sha512' or base64.b64encode(hashlib.sha512(archive).digest()).decode()!=expected:
        raise ValueError('npm archive integrity mismatch')
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        pending=list(ENTRY_POINTS); files={}
        while pending:
            name=pending.pop()
            if name in files:continue
            stream=tar.extractfile('package/'+name)
            if stream is None:raise ValueError('Missing dependency: '+name)
            data=stream.read();files[name]=data
            for dependency in IMPORTS.findall(data.decode('utf8')):
                if dependency.startswith(('http:','https:','data:')):
                    raise ValueError('External dependency in offline bundle: '+dependency)
                pending.append(normalize_relative(name,dependency))
        files['LICENSE']=tar.extractfile('package/LICENSE').read()
    manifest={'package':'three','version':VERSION,'npm_integrity':package['dist']['integrity'],
              'files':{name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)} for name,data in sorted(files.items())}}
    for name,data in files.items():
        target=DEST/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    (DEST/'offline-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
    print(f'Installed Three.js {VERSION}: {len(files)} files, {sum(map(len,files.values())):,} bytes')


if __name__=='__main__':install()
