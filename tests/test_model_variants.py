"""Real pinned-codec conversion and immutable compression history."""
import gzip
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import model_variants as variants
import numpy as np
from plyfile import PlyData, PlyElement


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=variants.ROOT/'web_data', prefix='compression_test_')
        self.root = Path(self.temp.name)
        self.patched = patch.object(variants,'VARIANTS',self.root/'variants')
        self.patched.start()
        self.source = self.root/'test.ply'
        names=['x','y','z']+[f'f_dc_{i}' for i in range(3)]+[f'f_rest_{i}' for i in range(45)]+['opacity']+[f'scale_{i}' for i in range(3)]+[f'rot_{i}' for i in range(4)]
        self.vertices = np.zeros(256,dtype=[(n,'f4') for n in names])
        self.vertices['x']=np.linspace(-12,12,256)
        self.vertices['y']=np.sin(self.vertices['x'])
        self.vertices['z']=3
        self.vertices['rot_0']=1
        self.vertices['opacity']=1
        self.write_source()
        self.source.with_suffix('.meta.json').write_text(json.dumps({'model':'test.ply','job':'parent_job','view_run_id':'original_views','run_id':'training_run'}))
    def write_source(self):
        PlyData([PlyElement.describe(self.vertices,'vertex')],text=False).write(self.source)
    def tearDown(self):
        self.patched.stop();self.temp.cleanup()

    @unittest.skipUnless(shutil.which('node'),'Node.js required for pinned SPZ encoder')
    def test_real_compression_cache_history_and_stale_source(self):
        original = variants.sha256_file(self.source)
        result = variants.build_fast_variant(self.source)
        self.assertEqual(variants.sha256_file(self.source),original)
        self.assertEqual(result['splats'],256)
        self.assertEqual(result['sh_degree'],3)
        self.assertLess(result['bytes'],self.source.stat().st_size)
        identifier=result['record_id']
        record=variants.compression_record(identifier)
        self.assertEqual(record['parent_info']['view_run_id'],'original_views')
        self.assertEqual(record['parent_info']['run_id'],'training_run')
        self.assertEqual(variants.start_fast_variant(self.source),identifier,'reuse completed compression')
        payload=variants.compression_directory(identifier)/'model.spz'
        old_hash=variants.sha256_file(payload)
        self.vertices['x']+=.1;self.write_source()
        self.assertIsNone(variants.available_variant(self.source))
        second=variants.build_fast_variant(self.source)
        self.assertNotEqual(second['record_id'],identifier)
        self.assertEqual(variants.sha256_file(payload),old_hash,'previous record payload stays immutable')
        self.assertEqual(len(variants.compression_records()),2)

    def test_range_guard_and_invalid_artifact(self):
        self.vertices['x'][0]=9e6;self.write_source()
        with self.assertRaises(ValueError): variants.ply_info(self.source)
        for name in ['../outside.ply','../x','x\\y.ply','/absolute.ply','...ply','..ply']:
            with self.assertRaises(ValueError): variants.variant_directory(name)
        self.assertIsNone(variants.compression_record('../outside'))
        malformed=self.root/'bad.spz'
        with gzip.open(malformed,'wb') as f: f.write(struct.pack('<IIIBBBB',0x5053474E,3,2,3,12,1,0))
        with self.assertRaises(ValueError): variants.spz_header(malformed)

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_failed_encode_keeps_original_and_error_record(self):
        original=variants.sha256_file(self.source)
        with patch.object(variants.subprocess,'run',return_value=type('Run',(),{'returncode':1,'stderr':'test failure'})()):
            with self.assertRaises(RuntimeError): variants.build_fast_variant(self.source)
        self.assertEqual(variants.sha256_file(self.source),original)
        self.assertIsNone(variants.available_variant(self.source))
        records=variants.compression_records()
        self.assertEqual(records[0]['state'],'error')
        self.assertFalse((variants.variant_directory(self.source.name)/'fast.spz').exists())
