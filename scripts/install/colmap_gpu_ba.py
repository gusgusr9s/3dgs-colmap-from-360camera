"""Install a pinned CUDA/cuDSS COLMAP bundle and verify real spherical GPU solves."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.runtime.colmap_ba import BUNDLE_NAME, receipt_for, probe, write_receipt
from scripts.install.manage import guard

VERSION = '4.2.1'
ARCHIVE = 'COLMAP-4.2.1-windows-2022-CUDA-cuDSS-Caspar.zip'
URL = 'https://github.com/lyehe/build_gpu_colmap/releases/download/v4.2.1/'+ARCHIVE
SHA256 = 'a36bc01763b3ecf50a43ebf541a3727e209998479a1821fd7900b33b75e2b4aa'
UPSTREAM_COMMIT = 'bd1fcf654d2dd8fefa1466999c190a246f83f4b9'


def install(root=ROOT):
    root = Path(root).resolve()
    guard(root)
    destination = root/'tools'/BUNDLE_NAME
    installed = destination/'bin/colmap.exe'
    receipt = receipt_for(installed) if installed.is_file() else None
    if receipt and receipt.get('source', {}).get('archive_sha256') == SHA256:
        print('Verified Ceres CUDA/cuDSS COLMAP already installed: '+str(installed), flush=True)
        return installed
    archive = root/'tools/downloads'/ARCHIVE
    archive.parent.mkdir(parents=True, exist_ok=True)
    print('GPU BA bundle: COLMAP '+VERSION+'; publisher lyehe/build_gpu_colmap; pinned SHA-256 '+SHA256, flush=True)
    if not archive.is_file():
        subprocess.run(['curl.exe', '--location', '--fail', '--retry', '3', '--output', str(archive), URL], check=True)
    with archive.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != SHA256:
        raise RuntimeError('GPU BA archive SHA-256 mismatch; existing installation preserved. Remove the incomplete download and retry: '+str(archive))
    staging = root/'tools'/('.colmap-cuda-ba-stage-'+uuid.uuid4().hex[:8])
    staging.mkdir()
    print('Extracting GPU bundle into '+str(staging), flush=True)
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            if not (staging/entry.filename).resolve().is_relative_to(staging.resolve()):
                raise RuntimeError('Archive contains an unsafe path')
        package.extractall(staging)
    candidates = [path for path in staging.rglob('colmap.exe') if path.parent.name == 'bin']
    if len(candidates) != 1:
        raise RuntimeError('Expected one bin/colmap.exe in the GPU bundle')
    executable = candidates[0]
    banner = subprocess.run([str(executable), '-h'], capture_output=True, text=True, timeout=30,
                            encoding='utf8', errors='replace', check=True)
    if 'COLMAP 4.2.1' not in banner.stdout+banner.stderr:
        raise RuntimeError('Unexpected COLMAP version in GPU bundle')
    provenance = list(staging.rglob('build_info.json'))
    if not provenance or UPSTREAM_COMMIT not in provenance[0].read_text(encoding='utf8'):
        raise RuntimeError('GPU bundle has no matching upstream source provenance')
    print('Checking spherical dense CUDA and sparse cuDSS solvers...', flush=True)
    report = probe(executable, root/'tools/install-logs'/('ba-install-'+uuid.uuid4().hex[:8]))
    report['source'] = dict(url=URL, archive_sha256=SHA256, upstream_commit=UPSTREAM_COMMIT,
                            build_info=json.loads(provenance[0].read_text(encoding='utf8')))
    if not report['ready']:
        evidence = root/'tools/install-logs/colmap-ba-install-failed.json'
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        raise RuntimeError('GPU BA verification failed. Existing COLMAP preserved. See '+str(evidence))
    guard(root)
    source = executable.parent.parent.resolve()
    tools_root = (root/'tools').resolve()
    if not all(path.resolve().is_relative_to(tools_root) for path in (source, destination)):
        raise RuntimeError('Refusing to move a bundle outside tools/')
    if destination.exists():
        backup = destination.with_name(destination.name+'-backup-'+uuid.uuid4().hex[:8])
        if not backup.resolve().is_relative_to(tools_root):
            raise RuntimeError('Unsafe backup path')
        destination.rename(backup)
    source.rename(destination)
    report['executable'] = str(installed)
    write_receipt(installed, report)
    print('GPU BA READY: '+str(installed), flush=True)
    return installed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if os.name != 'nt':
        parser.error('This bundle installer is for Windows x64')
    install()
