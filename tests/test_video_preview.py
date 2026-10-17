"""Real conversion, cache isolation, serialization and retry checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import video_preview as preview


class VideoPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='video_preview_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source with spaces.mp4'
        self.cache = self.root / 'cache'
        self.source.write_bytes(b'original')

    def alive(self, path):
        return json.loads(path.read_text(encoding='utf8'))['pid'] == os.getpid()

    def wait(self):
        limit = time.monotonic() + 25
        while time.monotonic() < limit:
            record = preview.preview_status(self.source, self.cache, self.alive)
            if record['state'] in {'error', 'ready'}:
                # Wait for the worker to release the lock before cleanup/retry.
                directory = preview.preview_directory(self.source, self.cache)
                with (directory / '.lock').open('a+b') as handle:
                    if preview.try_lock(handle):
                        return record
            time.sleep(.02)
        self.fail('Preview worker did not finish')

    def test_interrupted_and_missing_outputs_are_never_ready(self):
        directory = preview.preview_directory(self.source, self.cache)
        directory.mkdir(parents=True)
        preview._write(directory, {'state':'running', 'pid':99999999})
        self.assertEqual(preview.preview_status(self.source, self.cache, lambda p:False)['state'], 'error')
        preview._write(directory, {'state':'ready'})
        self.assertEqual(preview.preview_status(self.source, self.cache, self.alive)['state'], 'error')

    def test_missing_encoder_shows_failure_and_allows_retry(self):
        with patch.object(preview.shutil, 'which', return_value=None):
            preview.start_preview(self.source, self.cache, self.alive)
            first = self.wait()
            self.assertEqual(first['state'], 'error')
            self.assertIn('FFmpeg', first['error'])
            preview.start_preview(self.source, self.cache, self.alive)
            self.assertEqual(self.wait()['state'], 'error')
        self.assertEqual(self.source.read_bytes(), b'original')
        self.assertFalse(list(self.cache.rglob('*.mp4')))

    def test_one_conversion_per_source_and_one_active_encoder(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def held_run(*args, **kwargs):
            calls.append(args[0]); entered.set()
            release.wait(10)
            raise ValueError('simulated converter failure')

        with patch.object(preview.shutil, 'which', return_value='ffmpeg'), patch.object(preview.subprocess, 'run', side_effect=held_run):
            try:
                preview.start_preview(self.source, self.cache, self.alive)
                self.assertTrue(entered.wait(5))
                preview.start_preview(self.source, self.cache, self.alive)
                other = self.root / 'other.mp4'; other.write_bytes(b'another')
                preview.start_preview(other, self.cache, self.alive)
                time.sleep(.15)
                self.assertEqual(len(calls), 1)
                self.assertEqual(preview.preview_status(other, self.cache, self.alive)['state'], 'queued')
            finally:
                release.set()
                self.wait()
                limit = time.monotonic() + 5
                while preview.preview_status(other, self.cache, self.alive)['state'] != 'error' and time.monotonic() < limit:
                    time.sleep(.03)
        self.assertEqual(len(calls), 2)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg not installed')
    def test_real_browser_format_audio_timeline_and_cache_invalidation(self):
        options = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=256x128:rate=10',
                        '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', str(self.source)], check=True, capture_output=True, **options)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        preview.start_preview(self.source, self.cache, self.alive)
        record = self.wait()
        self.assertEqual(record['state'], 'ready', record)
        self.assertEqual(record['progress'], 100)
        target = preview.preview_directory(self.source, self.cache) / 'preview.mp4'
        metadata = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                                       '-of', 'json', str(target)], **options))
        video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
        self.assertEqual((video['codec_name'], video['pix_fmt'], video['width'], video['height']), ('h264','yuv420p',256,128))
        self.assertTrue(any(s['codec_name'] == 'aac' for s in metadata['streams']))
        self.assertAlmostEqual(float(metadata['format']['duration']), 1, delta=.08)
        data = target.read_bytes()
        self.assertLess(data.index(b'moov'), data.index(b'mdat'))
        self.assertEqual(digest, hashlib.sha256(self.source.read_bytes()).hexdigest())
        modified = target.stat().st_mtime_ns
        preview.start_preview(self.source, self.cache, self.alive)
        self.assertEqual(target.stat().st_mtime_ns, modified)
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        self.assertNotEqual(preview.preview_status(self.source, self.cache, self.alive)['id'], record['id'])
        self.assertEqual(preview.preview_status(self.source, self.cache, self.alive)['state'], 'idle')
        # Changing the source during conversion must not publish a stale copy.
        conversion_directory = preview.preview_directory(self.source, self.cache)
        original_write = preview._write
        def change_source(directory, status):
            original_write(directory, status)
            if status['state'] == 'running' and not status.get('progress'):
                current = self.source.stat()
                os.utime(self.source, ns=(current.st_atime_ns, current.st_mtime_ns + 1000000))
        with patch.object(preview, '_write', side_effect=change_source):
            preview.start_preview(self.source, self.cache, self.alive)
            limit = time.monotonic() + 10
            while time.monotonic() < limit:
                # Match production readers: don't open status.json during atomic replacement
                # on Windows. This directory is fixed because the source changed mid-run.
                with preview._records_lock:
                    status = json.loads((conversion_directory/'status.json').read_text(encoding='utf8'))
                if status['state'] == 'error':
                    with (conversion_directory/'.lock').open('a+b') as handle:
                        if preview.try_lock(handle):
                            break
                time.sleep(.02)
            self.assertEqual(status['state'], 'error', status)
        self.assertFalse((conversion_directory/'preview.mp4').exists())
        self.assertTrue(target.exists(), 'previous completed cache is preserved')


if __name__ == '__main__':
    unittest.main()
