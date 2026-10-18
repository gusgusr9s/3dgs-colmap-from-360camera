"""Cache behavior with real file changes, isolated from published artifacts."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from app.pipeline import view_suggestions as suggestions


class PageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='page_cache_',dir=app.ROOT/'web_data')
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to((app.ROOT/'web_data').resolve())

    def tearDown(self):
        self.temporary.cleanup()

    def test_json_cache_preserves_copy_and_updates_after_write_or_delete(self):
        file = self.root/'status.json'
        app.write_json(file, {'state':'ready','nested':{'n':1}})
        first = app.read_json(file)
        first['nested']['n'] = 99
        self.assertEqual(app.read_json(file)['nested']['n'],1)
        app.write_json(file, {'state':'error','nested':{'n':2}})
        self.assertEqual(app.read_json(file)['state'],'error')
        file.unlink()
        self.assertEqual(app.read_json(file, {'missing':True}),{'missing':True})

    def test_view_summary_keeps_frame_count_without_returning_camera_arrays(self):
        file = self.root/'transforms.json'
        app.write_json(file, {'source_key':'raw:one','frames':[{},{}],'settings':{'views':8}})
        summary = app.view_summary(file)
        self.assertEqual(summary['frame_count'],2)
        self.assertNotIn('frames',summary)
        summary['settings']['views'] = 99
        self.assertEqual(app.view_summary(file)['settings']['views'],8)
        app.write_json(file, {'frames':[{}]})
        self.assertEqual(app.view_summary(file)['frame_count'],1)

    def test_source_profile_survives_restart_and_invalidates_changed_media(self):
        file = self.root/'source.png'
        with patch.object(suggestions,'PROFILE_CACHE',self.root/'profiles'):
            app.cv2.imwrite(str(file),app.np.zeros((16,32,3),app.np.uint8))
            first = suggestions.probe_source(file)
            suggestions._probe_cached.cache_clear()
            with patch.object(suggestions.cv2,'imread',side_effect=AssertionError('Persistent metadata decoded again')):
                self.assertEqual(suggestions.probe_source(file),first)
            app.cv2.imwrite(str(file),app.np.full((24,32,3),64,app.np.uint8))
            stat=file.stat();os.utime(file,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000000))
            self.assertEqual(suggestions.probe_source(file)['height'],24)


if __name__ == '__main__':
    unittest.main()
