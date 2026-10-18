"""Installer contracts: use owned tools/venvs, never install into the working app."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_manage', ROOT/'scripts/install/manage.py')
manage = importlib.util.module_from_spec(spec); spec.loader.exec_module(manage)


class SharedInstallerTests(unittest.TestCase):
    def test_nvidia_amd_and_cpu_choose_separate_libraries(self):
        with patch('app.runtime.compute_runtime.nvidia_devices', return_value=['RTX 5070']), \
             patch.object(manage, 'cuda_arch_compatible', return_value=True):
            cuda = manage.installation_profile('auto')
        self.assertEqual(cuda['name'], 'nvidia-cuda')
        self.assertEqual(cuda['torch_variant'], 'cu128')
        self.assertTrue(cuda['gpu_ba_required'])
        self.assertFalse(cuda['directml'])
        with patch('app.runtime.compute_runtime.nvidia_devices', return_value=[]), \
             patch('app.runtime.directml_devices.adapters', return_value=[{'name':'AMD Radeon RX 6700 XT','software':False}]), \
             patch.object(manage.os, 'name', 'nt'):
            amd = manage.installation_profile('auto')
        self.assertEqual(amd['name'], 'windows-directml')
        self.assertEqual(amd['torch_variant'], 'cpu')
        self.assertTrue(amd['directml'])
        self.assertEqual(amd['colmap_device'], 'cpu')
        self.assertFalse(manage.installation_profile('cpu')['directml'])

    def test_old_nvidia_architecture_uses_non_cuda_profile(self):
        with patch('app.runtime.compute_runtime.nvidia_devices', return_value=['old NVIDIA']), \
             patch.object(manage, 'cuda_arch_compatible', return_value=False), patch.object(manage.os, 'name', 'nt'):
            self.assertEqual(manage.selected_device('auto'), 'cpu')
            with self.assertRaisesRegex(RuntimeError, '7.5'):
                manage.selected_device('cuda')

    def test_guard_blocks_live_task_without_mutating_its_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lock=root/'web_data/jobs/running/.task.lock';lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({'pid':os.getpid()}));before=lock.stat()
            with self.assertRaisesRegex(RuntimeError,'running'):manage.guard(root)
            self.assertEqual(before.st_mtime_ns,lock.stat().st_mtime_ns)
            with patch.object(manage,'running',return_value=False):manage.guard(root)

    def test_auto_cuda_and_explicit_cpu_or_missing_cuda(self):
        with patch('app.runtime.compute_runtime.nvidia_devices',return_value=['NVIDIA RTX']):
            self.assertEqual(manage.selected_device('auto'),'cuda')
            self.assertEqual(manage.selected_device('cpu'),'cpu')
        with patch('app.runtime.compute_runtime.nvidia_devices',return_value=[]):
            self.assertEqual(manage.selected_device('auto'),'cpu')
            with self.assertRaises(RuntimeError):manage.selected_device('cuda')

    def test_python_install_keeps_torch_pair_and_propagates_failures(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(manage,'ROOT',Path(temp)), \
             patch.object(manage,'guard') as guard, patch.object(manage,'selected_device',return_value='cpu'), \
             patch.object(manage,'run') as run:
            manage.install_python('auto')
            guard.assert_called_once()
            commands=[list(call.args) for call in run.call_args_list]
            self.assertIn('torch==2.7.1+cpu',commands[1])
            self.assertIn('torchvision==0.22.1+cpu',commands[1])
            self.assertIn('--only-binary=:all:',commands[2])
            self.assertEqual(commands[-1][-2:],['pip','check'])
            self.assertIn('torch==2.7.1+cpu',(Path(temp)/'tools/install-logs/constraints.txt').read_text())
            run.reset_mock();run.side_effect=RuntimeError('pip failure')
            with self.assertRaisesRegex(RuntimeError,'pip failure'):manage.install_python('auto')
            self.assertEqual(run.call_count,1)

    def test_manifest_checks_content_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);asset=root/'asset.js';asset.write_bytes(b'test')
            manifest=root/'manifest.json';entry={'sha256':hashlib.sha256(b'test').hexdigest()}
            manifest.write_text(json.dumps({'asset.js':entry}))
            self.assertIn('1 checksums',manage.verify_manifest(root,manifest))
            asset.write_bytes(b'corrupt')
            with self.assertRaises(RuntimeError):manage.verify_manifest(root,manifest)
            manifest.write_text(json.dumps({'../escape':entry}))
            with self.assertRaises(RuntimeError):manage.verify_manifest(root,manifest)

    def test_shell_scripts_parse_and_plan_never_calls_package_managers(self):
        bash=shutil.which('bash') if os.name!='nt' else r'C:\Program Files\Git\bin\bash.exe'
        if not bash or not Path(bash).is_file():self.skipTest('Bash unavailable')
        for script in [*ROOT.glob('app_*.sh'),*(ROOT/'scripts/install').glob('*.sh')]:
            subprocess.run([bash,'-n',script.as_posix()],check=True,capture_output=True)
        result=subprocess.run([bash,(ROOT/'app_1_install.sh').as_posix(),'--plan'],capture_output=True,text=True,check=True)
        self.assertIn('PLAN ONLY',result.stdout)
        self.assertIn('Node.js',result.stdout)
        bad=subprocess.run([bash,(ROOT/'app_1_install.sh').as_posix(),'--unknown'],capture_output=True,text=True)
        self.assertEqual(bad.returncode,2)


@unittest.skipUnless(os.name=='nt' and shutil.which('powershell'),'Windows PowerShell required')
class WindowsInstallerTests(unittest.TestCase):
    def fixture(self, root):
        helper=root/'scripts/install';helper.mkdir(parents=True)
        for name in ('app_1_install.ps1','app_2_run.ps1'):shutil.copy2(ROOT/name,root/name)
        shutil.copy2(ROOT/'scripts/install/windows.ps1',helper/'windows.ps1')
        (helper/'manage.py').write_text('''import json,os,sys
from pathlib import Path
with Path(os.environ['INSTALL_TEST_LOG']).open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
raise SystemExit(7 if os.environ.get('INSTALL_TEST_FAIL')==sys.argv[1] else 0)
''')
        for name in ('colmap','brush'):
            (helper/(name+'.ps1')).write_text("param([string]$Device)\nAdd-Content -LiteralPath $env:INSTALL_TEST_LOG -Value ('"+name+":'+$Device)\nexit 0\n")
        (root/'web_app.py').write_text("import os,sys,json\nfrom pathlib import Path\nPath(os.environ['INSTALL_TEST_RUN']).write_text(json.dumps({'args':sys.argv[1:],'path':os.environ['PATH']}))\n")
        (root/'fake-node.ps1').write_text("Write-Output 'v24.22.0'\nexit 0\n")
        (root/'fake-winget.ps1').write_text('''$item = [Array]::IndexOf($args,'--id')
$id = $args[$item+1]
$marker = Join-Path $env:INSTALL_TEST_ROOT $id
Add-Content -LiteralPath $env:INSTALL_TEST_LOG -Value ('winget:'+($args -join ' '))
if ($args[0] -eq 'list') { if (Test-Path -LiteralPath $marker) { exit 0 } else { exit 1 } }
New-Item -ItemType File -Force -Path $marker | Out-Null
exit 0
''')
        driver=root/'driver.ps1'
        driver.write_text('''param([switch]$Check,[switch]$Run)
$ErrorActionPreference='Stop'
function Get-Command {
 [CmdletBinding()]param([string]$Name)
 switch ($Name) {
  'python' { return [pscustomobject]@{Source=$env:INSTALL_TEST_PYTHON} }
  'py' { return $null }
  'nvidia-smi' { return $null }
  'winget' { return [pscustomobject]@{Source=(Join-Path $env:INSTALL_TEST_ROOT 'fake-winget.ps1')} }
  'node' { if(Test-Path (Join-Path $env:INSTALL_TEST_ROOT 'OpenJS.NodeJS.LTS')) { return [pscustomobject]@{Source=(Join-Path $env:INSTALL_TEST_ROOT 'fake-node.ps1')} };return $null }
  default { if(Test-Path (Join-Path $env:INSTALL_TEST_ROOT 'Gyan.FFmpeg.Essentials')) { return [pscustomobject]@{Source=(Join-Path $env:INSTALL_TEST_ROOT 'fake-node.ps1')} };return $null }
 }
}
if($Run) { & (Join-Path $env:INSTALL_TEST_ROOT 'app_2_run.ps1') -Port 8123 -CpuMode balanced -CpuThreads 2 }
elseif($Check) { & (Join-Path $env:INSTALL_TEST_ROOT 'app_1_install.ps1') -Check }
else { & (Join-Path $env:INSTALL_TEST_ROOT 'app_1_install.ps1') -Device cpu }
''')
        env={**os.environ,'INSTALL_TEST_ROOT':str(root),'INSTALL_TEST_LOG':str(root/'calls.log'),
             'INSTALL_TEST_RUN':str(root/'run.json'),'INSTALL_TEST_PYTHON':sys.executable,
             'LOCALAPPDATA':str(root/'empty-local')}
        return driver,env

    def invoke(self, driver, env, *args):
        return subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(driver),*args],
                              env=env,capture_output=True,text=True,timeout=90)

    def test_clean_install_order_retry_check_and_run_with_space_in_path(self):
        with tempfile.TemporaryDirectory(prefix='install fixture ') as temp:
            root=Path(temp);driver,env=self.fixture(root)
            first=self.invoke(driver,env);self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            calls=(root/'calls.log').read_text(encoding='utf-8-sig')
            self.assertIn('Microsoft.VCRedist.2015+.x64',calls)
            self.assertIn('Gyan.FFmpeg.Essentials',calls);self.assertIn('OpenJS.NodeJS.LTS',calls)
            self.assertLess(calls.index('["python"'),calls.index('colmap:cpu'))
            self.assertLess(calls.index('brush:'),calls.index('["assets"'))
            self.assertIn('"--smoke"',calls);self.assertIn('[7/7]',first.stdout)
            again=self.invoke(driver,env);self.assertEqual(again.returncode,0,again.stdout+again.stderr)
            before=(root/'calls.log').read_text(encoding='utf-8-sig')
            checked=self.invoke(driver,env,'-Check');self.assertEqual(checked.returncode,0,checked.stderr)
            added=(root/'calls.log').read_text(encoding='utf-8-sig')[len(before):]
            self.assertIn('["verify"',added);self.assertNotIn('winget:',added);self.assertNotIn('"--smoke"',added)
            run=self.invoke(driver,env,'-Run');self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            data=json.loads((root/'run.json').read_text())
            self.assertEqual(data['args'],['--port','8123','--cpu-mode','balanced','--cpu-threads','2'])
            self.assertIn(str(root/'tools/brush').replace('/','\\'),data['path'])

    def test_install_stops_on_failed_python_step(self):
        with tempfile.TemporaryDirectory(prefix='install failed ') as temp:
            root=Path(temp);driver,env=self.fixture(root);env['INSTALL_TEST_FAIL']='python'
            result=self.invoke(driver,env);self.assertNotEqual(result.returncode,0)
            calls=(root/'calls.log').read_text(encoding='utf-8-sig')
            self.assertNotIn('colmap:',calls);self.assertNotIn('[7/7]',result.stdout)

    def test_running_job_blocks_before_any_installs(self):
        with tempfile.TemporaryDirectory(prefix='install active ') as temp:
            root=Path(temp);driver,env=self.fixture(root)
            lock=root/'web_data/jobs/active/.task.lock';lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({'pid':os.getpid()}))
            result=self.invoke(driver,env);self.assertNotEqual(result.returncode,0)
            self.assertIn('Job active is running',result.stderr)
            self.assertFalse((root/'calls.log').exists())
            self.assertFalse((root/'tools/install-logs').exists())


if __name__=='__main__':unittest.main()
