"""Shared installer operations. No application imports during guard/dependency install."""
from __future__ import annotations
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def guard(root=ROOT):
    for lock in (root / 'web_data/jobs').glob('*/.task.lock'):
        try:
            record = json.loads(lock.read_text(encoding='utf8'))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'Cannot inspect task lock {lock}; installation was not started.') from exc
        if running(record.get('pid')):
            raise RuntimeError(f'Job {lock.parent.name} is running. Wait before installing; use --check for read-only verification.')


def selected_device(device):
    if device == 'cpu':
        return 'cpu'
    from app.runtime.compute_runtime import nvidia_devices
    detected = nvidia_devices()
    if device == 'cuda' and not detected:
        raise RuntimeError('CUDA requested but NVIDIA driver/GPU is unavailable. Install the vendor driver and reboot, or select CPU.')
    if detected and os.name == 'nt' and cuda_arch_compatible() is False:
        if device == 'cuda':
            raise RuntimeError('This CUDA/cuDSS bundle requires NVIDIA compute capability 7.5+. Use auto for DirectML/CPU on older GPUs.')
        return 'cpu'
    return 'cuda' if device == 'auto' and detected else 'cpu' if device == 'auto' else device


def cuda_arch_compatible():
    """Check the pinned Windows bundle's minimum architecture without initializing CUDA."""
    executable = shutil.which('nvidia-smi')
    if not executable:
        return None  # The explicit installer smoke will decide when inventory is unavailable.
    try:
        result = subprocess.run([executable, '--query-gpu=compute_cap', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, check=True, timeout=10)
        capabilities = [float(line.strip()) for line in result.stdout.splitlines() if line.strip()]
        return bool(capabilities) and max(capabilities) >= 7.5
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def installation_profile(device, selected=None):
    selected = selected or selected_device(device)
    directml = False
    adapters = []
    if selected != 'cuda' and device != 'cpu' and os.name == 'nt':
        from app.runtime.directml_devices import adapters as dxgi_adapters
        adapters = [adapter for adapter in dxgi_adapters() if not adapter['software']]
        directml = bool(adapters)
    return {'name':'nvidia-cuda' if selected == 'cuda' else 'windows-directml' if directml else 'cpu',
            'device':selected, 'colmap_device':selected, 'torch_variant':'cu128' if selected == 'cuda' else 'cpu',
            'mask_backend':'CUDA' if selected == 'cuda' else 'DirectML' if directml else 'CPU',
            'gpu_ba_required':selected == 'cuda', 'directml':directml,
            'adapters':[adapter['name'] for adapter in adapters]}


def run(*command, timeout=None):
    print('$ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, cwd=ROOT, timeout=timeout)


def install_python(device):
    guard()
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise RuntimeError('This environment must use Python 3.12 x64; choose a new venv if the existing one uses another version.')
    profile = installation_profile(device)
    variant = profile['torch_variant']
    print('Selected hardware profile: '+json.dumps(profile), flush=True)
    run(sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools<81', 'wheel')
    constraints = ROOT / 'tools/install-logs/constraints.txt'
    constraints.parent.mkdir(parents=True, exist_ok=True)
    constraints.write_text(f'torch==2.7.1+{variant}\ntorchvision==0.22.1+{variant}\ntorchaudio==2.7.1+{variant}\n', encoding='utf8')
    run(sys.executable, '-m', 'pip', 'install', f'torch==2.7.1+{variant}',
        f'torchvision==0.22.1+{variant}', f'torchaudio==2.7.1+{variant}',
        '--index-url', f'https://download.pytorch.org/whl/{variant}')
    # Use prebuilt Python packages; CUDA compilation belongs to the COLMAP installer.
    run(sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '-c', constraints,
        '-r', ROOT / 'requirements.txt', '-r', ROOT / 'requirements-gpu.txt')
    if profile['directml']:
        run(sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '-c', constraints,
            '-r', ROOT / 'requirements-directml.txt')
    run(sys.executable, '-m', 'pip', 'check')
    destination = constraints.parent/'install-profile.json'
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(profile, indent=2), encoding='utf8')
    temporary.replace(destination)


def install_assets():
    guard()
    run(sys.executable, ROOT / 'scripts/install_viewer_assets.py')
    try:
        verify_manifest(ROOT/'assets/vendor/three-0.186.1', ROOT/'assets/vendor/three-0.186.1/offline-manifest.json', 'files')
    except (OSError, ValueError, KeyError, RuntimeError):
        run(sys.executable, ROOT / 'scripts/install_export_assets.py')
    import torch
    from torchvision.models.segmentation import LRASPP_MobileNet_V3_Large_Weights
    url = LRASPP_MobileNet_V3_Large_Weights.DEFAULT.url
    destination = Path(torch.hub.get_dir()) / 'checkpoints' / url.rsplit('/', 1)[-1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = destination.stem.rsplit('-', 1)[-1]
    if not destination.is_file() or not hashlib.sha256(destination.read_bytes()).hexdigest().startswith(expected):
        torch.hub.download_url_to_file(url, str(destination), hash_prefix=expected)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)
    return 'passed'


def checked_output(command, timeout):
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding='utf8', errors='replace', check=True, timeout=timeout).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stdout + exc.stderr)[-1800:]) from exc


def verify_manifest(directory, manifest, member=None):
    data = json.loads(manifest.read_text(encoding='utf8'))
    files = data[member] if member else data
    if not files:
        raise RuntimeError('Empty asset manifest')
    for name, entry in files.items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise RuntimeError('Missing/changed asset: '+name)
    return f'{len(files)} checksums verified'


def verify(device='auto', smoke=False, output=None, cuda_smoke=False, execute=False):
    from app.runtime.runtime_verification import verify_environment, command_banner, executable
    previous = {key: os.environ.get(key) for key in ('COLMAP_DEVICE','MASK_DEVICE')}
    try:
        if device != 'auto':
            os.environ.update(COLMAP_DEVICE=device, MASK_DEVICE=device)
        report = verify_environment(ROOT / 'web_data')
    finally:
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
    checks = []
    def check(name, operation):
        try:
            result = operation()
            checks.append({'name': name, 'ok': True, 'detail': str(result)})
        except Exception as exc:
            checks.append({'name': name, 'ok': False, 'detail': str(exc)})
    check('Python 3.12 x64', lambda: require(sys.version_info[:2] == (3,12) and sys.maxsize > 2**32, sys.version))
    check('pip dependency consistency', lambda: checked_output([sys.executable,'-m','pip','check'], 60))
    check('ffprobe', lambda: command_banner(executable('ffprobe'), ['-version']).splitlines()[0])
    check('Node.js >=20', lambda: require(int(command_banner(executable('node'), ['--version']).strip().lstrip('v').split('.')[0]) >= 20, 'Node.js >=20 is required for exports'))
    check('ns-process-data', lambda: command_banner(executable('ns-process-data'), ['--help'])[:200])
    for module in ('numpy','cv2','PIL','plyfile','torch','torchvision','pycolmap'):
        check('import '+module, lambda module=module: __import__(module).__name__)
    check('viewer assets', lambda: verify_manifest(ROOT/'assets/vendor', ROOT/'assets/vendor/manifest.json'))
    check('export assets', lambda: verify_manifest(ROOT/'assets/vendor/three-0.186.1', ROOT/'assets/vendor/three-0.186.1/offline-manifest.json', 'files'))
    def weights():
        import torch
        from torchvision.models.segmentation import LRASPP_MobileNet_V3_Large_Weights
        name = LRASPP_MobileNet_V3_Large_Weights.DEFAULT.url.rsplit('/',1)[-1]
        path = Path(torch.hub.get_dir())/'checkpoints'/name
        expected = path.stem.rsplit('-',1)[-1]
        return require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest().startswith(expected), 'Mask weights missing/corrupt; rerun app_1_install')
    check('cached mask weights', weights)
    if device == 'cuda' or device == 'auto' and selected_device('auto') == 'cuda':
        check('requested CUDA backend', lambda: require(all(s['backend'].startswith('CUDA') for s in report['stages'] if s['key'] in {'colmap','masks'}), 'Requested CUDA is unavailable'))
        if os.name == 'nt':
            from app.runtime.colmap_ba import receipt_for
            from app.runtime.runtime_verification import colmap_executable
            check('verified Ceres CUDA/cuDSS BA', lambda: require(
                bool(colmap_executable()) and bool(receipt_for(colmap_executable())),
                'CUDA BA bundle is missing/unverified; rerun app_1_install.ps1 -Device cuda'))
    if cuda_smoke:
        if device != 'cuda':
            check('Torch CUDA execution smoke', lambda: require(False, '--cuda-smoke requires --device cuda'))
        else:
            def torch_cuda_smoke():
                import torch
                if not torch.cuda.is_available():
                    raise RuntimeError(f'Torch CUDA is unavailable (torch={torch.__version__}, build CUDA={torch.version.cuda})')
                # Exercise a small CUDA allocation and kernel so driver/runtime
                # incompatibilities are reported during installation, not first Train.
                value = torch.ones((16,), device='cuda')
                torch.cuda.synchronize()
                return f'{torch.cuda.get_device_name(0)}; Torch {torch.__version__}; CUDA {torch.version.cuda}; smoke={value.sum().item():.0f}'
            check('Torch CUDA execution smoke', torch_cuda_smoke)
    if smoke:
        check('local HTTP app startup', lambda: checked_output([sys.executable, str(Path(__file__).with_name('smoke_app.py'))], 45))
    if execute:
        # Real bounded work per stage, plus the global-BA solver benchmark that staged mapping reads.
        from app.runtime.execution_verification import run_checks
        report.update(run_checks(report, ROOT/'web_data', device))
        for row in report['execution_checks']:
            if row['state'] == 'failed':
                checks.append({'name': 'execution: '+row['label'], 'ok': False, 'detail': row['detail']})
        if report.get('execution_state') == 'blocked':
            checks.append({'name': 'execution checks', 'ok': False, 'detail': report['execution_reason']})
    report.update(installation_checks=checks, installation_ready=report['ready'] and all(c['ok'] for c in checks),
                  installation_checked_at=datetime.now(timezone.utc).isoformat())
    if cuda_smoke:
        report['scope'] += '; ran one small Torch CUDA allocation/kernel; no segmentation inference, Brush training or VRAM benchmark'
    for stage in report['stages']:
        print(f"[{stage['state']}] {stage['label']}: {stage['backend']} - {stage['reason']}")
    for row in report.get('execution_checks', []):
        print(f"[{row['state']}] {row['label']}: {row['requested_backend']} -> {row['actual_backend']} ({row['seconds']}s) - {row['detail']}")
    for item in checks:
        print(f"[{'ok' if item['ok'] else 'FAIL'}] {item['name']}: {item['detail']}")
    print(report['scope'])
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    if not report['installation_ready']:
        print('Installation is NOT fully ready. See failures above. CPU can prepare Views/poses/masks; Brush training needs a hardware GPU/driver.')
    return 0 if report['installation_ready'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('guard','python','assets','verify'))
    parser.add_argument('--device', choices=('auto','cpu','cuda'), default='auto')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--cuda-smoke', action='store_true', help='run a small CUDA allocation/kernel; requires --device cuda')
    parser.add_argument('--execute', action='store_true',
                        help='run bounded CPU/GPU work per stage and record the global-BA solver benchmark')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'guard': guard()
    elif args.action == 'python': install_python(args.device)
    elif args.action == 'assets': install_assets()
    else: return verify(args.device, args.smoke, args.output, args.cuda_smoke, args.execute)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print('INSTALL ERROR: '+str(exc), file=sys.stderr)
        raise SystemExit(1)
