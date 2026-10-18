"""Real export packaging, local dependency integrity, downloads and recovery."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
import zipfile

from app.models import model_exports as exports
from app.models import model_package as packages
from app.models import model_variants as variants
from app.web import server as app
import test_model_exports as fixtures


@unittest.skipUnless(shutil.which('node'),'Node.js required')
class PackageTests(unittest.TestCase):
    setUp=fixtures.ExportTests.setUp
    compressed=fixtures.ExportTests.compressed
    wait_export=fixtures.ExportTests.wait

    def ready(self):
        identifier,_=self.compressed()
        exports.start_export(identifier,app.task_lock_alive)
        self.assertEqual(self.wait_export(identifier)['state'],'ready')
        return identifier

    def wait(self,identifier):
        limit=time.monotonic()+20
        while time.monotonic()<limit:
            state=packages.package_status(identifier,app.task_lock_alive)
            if state['state'] in {'ready','error'}:
                with (packages.package_directory(identifier,state['key'])/'.lock').open('a+b') as handle:
                    if packages.try_lock(handle):return state
            time.sleep(.02)
        self.fail('Package worker timeout')

    def test_real_package_manifest_no_cdn_cache_and_http(self):
        identifier=self.ready();glb=exports.export_directory(identifier)/'model.glb'
        before=variants.sha256_file(glb)
        # Hold queue so duplicate requests are guaranteed to overlap.
        with variants._lock:
            first=packages.start_package(identifier,app.task_lock_alive)
            again=packages.start_package(identifier,app.task_lock_alive)
            self.assertEqual(first['created_at'],again['created_at'])
        state=self.wait(identifier);self.assertEqual(state['state'],'ready',state)
        target=packages.package_directory(identifier,state['key'])/'bundle.zip'
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['model_sha256'],before)
            for name,info in manifest['files'].items():
                data=archive.read(name)
                self.assertEqual(len(data),info['bytes']);self.assertEqual(hashlib.sha256(data).hexdigest(),info['sha256'])
                self.assertFalse(Path(name).is_absolute());self.assertNotIn('..',Path(name).parts)
            self.assertEqual(hashlib.sha256(archive.read('model.glb')).hexdigest(),before)
            html=archive.read('index.html').decode()
            self.assertNotIn('https://cdn.jsdelivr.net',html)
            self.assertIn('./vendor/three/build/three.webgpu.js',html)
            self.assertIn('GLTFGaussianSplatLoaderExtension',html)
            self.assertTrue((archive.getinfo('start_ubuntu.sh').external_attr>>16)&0o111)
            self.assertIn('LICENSE',archive.read('README.md').decode())
        stamp=target.stat().st_mtime_ns
        self.assertEqual(packages.start_package(identifier,app.task_lock_alive)['state'],'ready')
        self.assertEqual(target.stat().st_mtime_ns,stamp)
        self.assertEqual(variants.sha256_file(glb),before)
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            with urlopen(base+'/api/glb-package/'+identifier) as response:self.assertEqual(json.load(response)['state'],'ready')
            with urlopen(Request(base+f"/glb-package-download/{identifier}/{state['key']}.zip",headers={'Range':'bytes=0-3'})) as response:
                self.assertEqual(response.status,206);self.assertEqual(response.read(),b'PK\x03\x04')
        finally:server.shutdown();server.server_close()
        variants.delete_parent_variants('input3.ply')
        self.assertFalse(target.exists(),'deleting model removes its portable ZIP payload')

    def test_missing_glb_interruption_retry_and_corrupt_vendor(self):
        for identifier in ['../outside','g'*32,'a'*32]:
            self.assertEqual(packages.package_status(identifier,app.task_lock_alive)['state'],'unavailable')
            with self.assertRaises(ValueError):packages.start_package(identifier,app.task_lock_alive)
        identifier=self.ready();source=exports.export_status(identifier,app.task_lock_alive)
        key=packages.build_key(source);directory=packages.package_directory(identifier,key);directory.mkdir(parents=True)
        exports._write(directory,{'state':'running','pid':os.getpid()})
        self.assertEqual(packages.package_status(identifier,app.task_lock_alive)['state'],'error')
        files,manifest=packages.package_files()
        broken=json.loads(json.dumps(manifest));broken['files']['LICENSE']['sha256']='0'*64
        with patch.object(packages,'package_files',return_value=(files,broken)):
            packages.start_package(identifier,app.task_lock_alive)
            self.assertEqual(self.wait(identifier)['state'],'error')
        self.assertFalse(list(directory.glob('*.partial')))
        packages.start_package(identifier,app.task_lock_alive)
        self.assertEqual(self.wait(identifier)['state'],'ready')
        (directory/'bundle.zip').write_bytes(b'incomplete')
        self.assertEqual(packages.package_status(identifier,app.task_lock_alive)['state'],'error')
        packages.start_package(identifier,app.task_lock_alive)
        self.assertEqual(self.wait(identifier)['state'],'ready')
