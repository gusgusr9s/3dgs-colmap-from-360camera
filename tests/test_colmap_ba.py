"""GPU BA receipts, exact solver flags, read-only selection and CPU compatibility."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.runtime import colmap_ba as ba
from app.runtime.compute_runtime import ColmapCompute


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root/'tools'/ba.BUNDLE_NAME/'bin'/('colmap.exe' if os.name == 'nt' else 'colmap')
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b'fixture exe')
        (self.exe.parent/'ceres.dll').write_bytes(b'fixture ceres')

    def ready(self):
        ba.write_receipt(self.exe, {'schema':1, 'ready':True})

    def test_only_verified_and_unchanged_solver_bundle_is_selected(self):
        self.assertIsNone(ba.bundle_executable(self.root, {}))
        self.ready()
        with patch.object(ba, 'nvidia_visible', return_value=True):
            self.assertEqual(ba.bundle_executable(self.root, {}), self.exe)
        with patch.object(ba, 'nvidia_visible', return_value=False):
            self.assertIsNone(ba.bundle_executable(self.root, {}))
        self.assertIsNone(ba.bundle_executable(self.root, {'COLMAP_DEVICE':'cpu'}))
        (self.exe.parent/'ceres.dll').write_bytes(b'changed solver')
        self.assertIsNone(ba.bundle_executable(self.root, {}))

    def test_failed_probe_cannot_enable_a_bundle(self):
        with self.assertRaisesRegex(RuntimeError, 'fallback'):
            ba.write_receipt(self.exe, {'schema':1, 'ready':False})

    def test_gpu_flags_require_real_receipt_and_cuda_compute(self):
        cuda = ColmapCompute('CUDA', '0', True, 'test')
        cpu = ColmapCompute('CPU', '-1', True, 'test')
        self.assertEqual(ba.mapper_gpu_options(self.exe, cuda, {})[0], [])
        with self.assertRaisesRegex(RuntimeError, 'unverified|verified'):
            ba.mapper_gpu_options(self.exe, cuda, {'COLMAP_BA_DEVICE':'cuda'})
        self.ready()
        options, metadata = ba.mapper_gpu_options(self.exe, cuda, {})
        self.assertEqual(options, ['--Mapper.ba_use_gpu','1','--Mapper.ba_gpu_index','0'])
        self.assertIn('cuDSS', metadata['colmap_ba_backend'])
        self.assertEqual(ba.mapper_gpu_options(self.exe, cpu, {})[0], [])
        self.assertEqual(ba.mapper_gpu_options(self.exe, cuda, {'COLMAP_BA_DEVICE':'cpu'})[0], [])
        with self.assertRaises(ValueError):
            ba.mapper_gpu_options(self.exe, cuda, {'COLMAP_BA_DEVICE':'wrong'})

    def test_receipt_rejects_changed_executable_and_escaping_paths(self):
        self.ready()
        receipt = self.exe.parent.parent/ba.RECEIPT_NAME
        record = json.loads(receipt.read_text())
        record['files']['../../elsewhere.dll'] = 'invalid'
        receipt.write_text(json.dumps(record))
        self.assertIsNone(ba.receipt_for(self.exe))
        self.ready()
        self.exe.write_bytes(b'new executable')
        self.assertIsNone(ba.receipt_for(self.exe))


if __name__ == '__main__':
    unittest.main()
