"""Exercise the real SPZ codec, GLB binary contract, recovery and HTTP downloads."""
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen, Request

import numpy as np
from plyfile import PlyData, PlyElement
import model_variants as variants
import model_exports as exports
import web_app as app


@unittest.skipUnless(shutil.which('node'), 'Node.js required for real SPZ decoding')
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='glb_export_')
        self.root = Path(self.temp.name)
        self.patched = patch.object(variants, 'VARIANTS', self.root / 'variants')
        self.patched.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.patched.stop)

    def compressed(self, degree=3):
        names = ['x','y','z'] + [f'f_dc_{i}' for i in range(3)] + [f'f_rest_{i}' for i in range(3*((degree+1)**2-1))]
        names += ['opacity', 'scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
        vertices = np.zeros(64, dtype=[(n, '<f4') for n in names])
        vertices['x'] = np.linspace(-2,2,64); vertices['y'] = 1; vertices['z'] = -3
        vertices['rot_0'] = np.cos(.3); vertices['rot_2'] = np.sin(.3)
        vertices['scale_0'] = -2; vertices['scale_1'] = -1; vertices['scale_2'] = -.5
        vertices['opacity'] = .7
        for i in range(3): vertices[f'f_dc_{i}'] = (i-1) * .3
        # Use the SPZ higher-band quantization grid so this tests band/channel order,
        # without confusing source compression loss with GLB export errors.
        for i in range(3*((degree+1)**2-1)): vertices[f'f_rest_{i}'] = ((i % 9)-4)*.125
        source = self.root / f'input{degree}.ply'
        PlyData([PlyElement.describe(vertices, 'vertex')], text=False).write(source)
        result = variants.build_fast_variant(source)
        source.unlink()  # Export must work independently of a deleted original PLY.
        return result['record_id'], vertices

    def wait(self, identifier):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = exports.export_status(identifier, app.task_lock_alive)
            if state['state'] in {'ready','error'}:
                with (exports.export_directory(identifier) / '.lock').open('a+b') as handle:
                    if exports.try_lock(handle): return state
            time.sleep(.02)
        self.fail('Export did not finish')

    def test_real_glb_all_sh_degrees_values_and_cache(self):
        for degree in range(4):
            with self.subTest(degree=degree):
                identifier, original = self.compressed(degree)
                spz = variants.compression_directory(identifier) / 'model.spz'
                before = variants.sha256_file(spz)
                exports.start_export(identifier, app.task_lock_alive)
                state = self.wait(identifier)
                self.assertEqual(state['state'], 'ready', state)
                target = exports.export_directory(identifier) / 'model.glb'
                payload = target.read_bytes()
                self.assertEqual(struct.unpack('<III', payload[:12]), (0x46546c67, 2, len(payload)))
                json_len, kind = struct.unpack_from('<II', payload, 12)
                self.assertEqual(kind, 0x4e4f534a)
                gltf = json.loads(payload[20:20+json_len])
                binary_len, kind = struct.unpack_from('<II', payload, 20+json_len)
                self.assertEqual(kind, 0x004e4942)
                self.assertEqual(binary_len, len(payload)-json_len-28)
                self.assertEqual(gltf['extensionsRequired'], ['KHR_gaussian_splatting'])
                primitive = gltf['meshes'][0]['primitives'][0]
                self.assertEqual(primitive['mode'], 0)
                self.assertEqual(len(primitive['attributes']), 4+(degree+1)**2)
                stride = gltf['bufferViews'][0]['byteStride']//4
                values = np.frombuffer(payload, dtype='<f4', offset=28+json_len).reshape(64, stride)
                self.assertTrue(np.isfinite(values).all())
                np.testing.assert_allclose(values[:,0], original['x'], atol=.0005)
                np.testing.assert_allclose(values[:,1:3], np.tile([1,-3], (64,1)), atol=.0005)
                np.testing.assert_allclose(np.linalg.norm(values[:,3:7], axis=1), 1, atol=1e-6)
                np.testing.assert_allclose(values[0,3:7], [0,np.sin(.3),0,np.cos(.3)], atol=.004)
                np.testing.assert_allclose(values[0,7:10], np.exp([-2,-1,-.5]), rtol=.03)
                self.assertAlmostEqual(values[0,10], 1/(1+np.exp(-.7)), delta=.01)
                np.testing.assert_allclose(values[0,11:14], [-.3,0,.3], atol=.04)
                # Original PLY stores SH channel-major; GLB must store coefficient-major RGB.
                coefficients = (degree+1)**2-1
                for k in range(coefficients):
                    expected = [original[f'f_rest_{c*coefficients+k}'][0] for c in range(3)]
                    np.testing.assert_allclose(values[0,14+3*k:17+3*k], expected, atol=.025)
                self.assertEqual(variants.sha256_file(spz), before)
                stamp = target.stat().st_mtime_ns
                self.assertEqual(exports.start_export(identifier, app.task_lock_alive)['state'], 'ready')
                self.assertEqual(target.stat().st_mtime_ns, stamp)
                variants.delete_parent_variants(f'input{degree}.ply')
                self.assertFalse(target.exists(), 'deleting a model also removes its derived GLB')
                self.assertIsNone(exports.export_status(identifier, app.task_lock_alive))

    def test_duplicate_requests_failure_retry_and_stale_cache(self):
        identifier, _ = self.compressed()
        with variants._lock:
            exports.start_export(identifier, app.task_lock_alive)
            first = exports.export_status(identifier, app.task_lock_alive)
            for _ in range(3):
                self.assertEqual(exports.start_export(identifier, app.task_lock_alive)['created_at'], first['created_at'])
        self.assertEqual(self.wait(identifier)['state'], 'ready')
        target = exports.export_directory(identifier) / 'model.glb'
        target.unlink()
        self.assertEqual(exports.export_status(identifier, app.task_lock_alive)['state'], 'error')
        with patch.object(exports.shutil, 'which', return_value=None):
            exports.start_export(identifier, app.task_lock_alive)
            self.assertEqual(self.wait(identifier)['state'], 'error')
        self.assertFalse(target.exists())
        exports.start_export(identifier, app.task_lock_alive)
        self.assertEqual(self.wait(identifier)['state'], 'ready')
        spz = variants.compression_directory(identifier) / 'model.spz'
        spz.write_bytes(spz.read_bytes() + b'changed')
        self.assertEqual(exports.export_status(identifier, app.task_lock_alive)['state'], 'error')
        exports.start_export(identifier, app.task_lock_alive)
        self.assertEqual(self.wait(identifier)['state'], 'error')

    def test_interruption_bad_ids_and_http(self):
        for identifier in ['../outside', 'a'*31, 'g'*32, 'x\\y']:
            self.assertIsNone(exports.export_status(identifier, app.task_lock_alive))
            with self.assertRaises(ValueError): exports.start_export(identifier, app.task_lock_alive)
        identifier, _ = self.compressed()
        directory = exports.export_directory(identifier); directory.mkdir()
        exports._write(directory, {'state':'running', 'pid':os.getpid()})
        self.assertEqual(exports.export_status(identifier, app.task_lock_alive)['state'], 'error')
        exports.start_export(identifier, app.task_lock_alive)
        self.assertEqual(self.wait(identifier)['state'], 'ready')
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base+'/exports/glb/'+identifier) as response:
                page = response.read().decode(); self.assertIn('glb-ready', page)
            with urlopen(Request(base+'/glb-download/'+identifier+'.glb', headers={'Range':'bytes=0-11'})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read()[:4], b'glTF')
            with urlopen(base+'/glb-viewer/'+identifier) as response:
                self.assertIn('/glb-download/'+identifier+'.glb', response.read().decode())
            with urlopen(base+'/glb-example') as response:
                self.assertIn("'./model.glb'", response.read().decode())
        finally:
            server.shutdown(); server.server_close()
