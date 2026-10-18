import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from app.pipeline import dataset_preparation as prepare


class ImagePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        for index in range(12):
            image=np.random.default_rng(index).integers(0,256,(128,128,3),dtype=np.uint8)
            cv2.imwrite(str(self.source/f'{index}.jpg'),image,[cv2.IMWRITE_JPEG_QUALITY,92])

    def build(self,name,workers):
        output=self.root/name;output.mkdir()
        pairs=[(p,output/p.name) for p in sorted(self.source.glob('*.jpg'))]
        with patch.dict(os.environ,{'DATASET_WORKERS':str(workers)}):
            records,stats=prepare.prepare_images(pairs,self.root/'cache',128,128)
        return output,records,stats

    def test_parallel_matches_legacy_pixels_and_repeat_does_not_encode(self):
        output,records,stats=self.build('first',4)
        self.assertLessEqual(stats['max_pending'],8)
        for source in self.source.glob('*.jpg'):
            # 128x128 test JPEGs are below Brush's minimum file size, so they take the re-encode path.
            expected=prepare.pad_for_brush(cv2.imencode('.jpg',cv2.imread(str(source)),[cv2.IMWRITE_JPEG_QUALITY,95])[1].tobytes())
            self.assertEqual((output/source.name).read_bytes(),expected)
        with patch.object(cv2,'imencode',side_effect=AssertionError('cache must skip encoding')):
            second,_,stats=self.build('second',2)
        self.assertEqual(stats['cache_hits'],12)
        self.assertEqual(stats['encoded'],0)
        self.assertEqual({p.name:p.read_bytes() for p in output.glob('*')},{p.name:p.read_bytes() for p in second.glob('*')})

    def test_changed_source_and_corrupt_cache_rebuild_only_affected_images(self):
        _,records,_=self.build('first',1)
        cv2.imwrite(str(self.source/'0.jpg'),np.zeros((128,128,3),dtype=np.uint8))
        # Corrupt a different cache entry; the checksum must reject it.
        receipt=next(p for p in (self.root/'cache').glob('*.json') if prepare.read(p)['source_sha256']==records[1]['source_sha256'])
        receipt.with_suffix('.jpg').write_bytes(b'broken')
        _,_,stats=self.build('second',2)
        self.assertEqual(stats['encoded'],2)
        self.assertEqual(stats['cache_hits'],10)

    def test_worker_budget_is_bounded_and_rejects_bad_override(self):
        self.assertLessEqual(prepare.worker_count(2048,2048,{'DATASET_WORKERS':'100','CPU_THREADS':'28'}),5)
        with self.assertRaises(ValueError):prepare.worker_count(1536,1536,{'DATASET_WORKERS':'0'})


if __name__=='__main__':unittest.main()
