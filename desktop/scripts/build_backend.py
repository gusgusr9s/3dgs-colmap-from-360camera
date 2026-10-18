"""Build in an isolated tool environment; never pip-install into the working app."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parents[2]
BUILD=ROOT/'desktop/build'


def copy_tree(source,target):
    shutil.copytree(source,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('*.zip','*.7z','__pycache__'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-ready',action='store_true');args=parser.parse_args()
    BUILD.mkdir(parents=True,exist_ok=True)
    if not args.runtime_ready:
        env=ROOT/'tools/desktop-build-env'
        python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.is_file():venv.create(env,with_pip=True)
        packages=Path(importlib.metadata.distribution('torch').locate_file('')).resolve()
        site=env/('Lib/site-packages' if os.name=='nt' else f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
        (site/'app-build-source.pth').write_text(str(packages)+'\n',encoding='utf8')
        probe=subprocess.run([str(python),'-c','import PyInstaller'],capture_output=True)
        if probe.returncode:subprocess.run([str(python),'-m','pip','install','pyinstaller==6.22.0'],check=True)
        subprocess.run([str(python),str(Path(__file__).resolve()),'--runtime-ready'],check=True,cwd=ROOT)
        return
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(BUILD),
                    '--workpath',str(ROOT/'tools/desktop-pyinstaller'),str(ROOT/'desktop/backend.spec')],check=True,cwd=ROOT)
    internal=BUILD/'backend/_internal';tools=internal/'tools';tools.mkdir(exist_ok=True)
    for name in ('colmap','colmap-3.12.4'):
        if (ROOT/'tools'/name).is_dir():copy_tree(ROOT/'tools'/name,tools/name)
    (tools/'brush').mkdir(exist_ok=True)
    for name in ('brush_app.exe','brush','LICENSE','README.md'):
        source=ROOT/'tools/brush'/name
        if source.is_file():shutil.copy2(source,tools/'brush'/name)
    for name,folder in [('ffmpeg','ffmpeg'),('ffprobe','ffmpeg'),('node','node')]:
        executable=shutil.which(name)
        if not executable:raise RuntimeError('Required engine is missing: '+name)
        destination=tools/folder/'bin'/Path(executable).name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(executable,destination)
    import torch
    weights=Path(torch.hub.get_dir())/'checkpoints/lraspp_mobilenet_v3_large-d234d4ea.pth'
    if not weights.is_file():raise RuntimeError('Mask weights missing; run app_1_install first')
    (tools/'mask_weights').mkdir(exist_ok=True);shutil.copy2(weights,tools/'mask_weights'/weights.name)
    engines={}
    for path in tools.rglob('*'):
        if path.is_file():
            engines[path.relative_to(internal).as_posix()]={'bytes':path.stat().st_size,
                'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()}
    manifest={'schema':1,'backend_version':'0.1.0','workspace_schema':1,'platform':sys.platform,
              'python':sys.version.split()[0],'engines':engines}
    (BUILD/'backend/runtime-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print('Packaged backend ready:',BUILD/'backend',flush=True)


if __name__=='__main__':main()
