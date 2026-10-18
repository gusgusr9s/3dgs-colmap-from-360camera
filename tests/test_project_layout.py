"""Exercise package/asset/CLI boundaries after moving files out of the root."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

from app.paths import ROOT, STATIC, TEMPLATES
from app.web.static_assets import PUBLIC_ASSETS, static_asset
from app.runtime.view_compute import cpu_worker_views


class ProjectLayoutTests(unittest.TestCase):
    def test_shared_paths_keep_installed_tools_and_existing_data_at_repository_root(self):
        self.assertIs(importlib.import_module('web_app'),importlib.import_module('app.web.server'))
        self.assertIs(importlib.import_module('pano_to_views'),importlib.import_module('app.pipeline.pano_to_views'))
        for name in ('app.web.server','app.runtime.runtime_verification',
                     'app.models.model_exports','app.models.model_package','app.models.model_variants',
                     'app.models.model_crop','app.jobs.task_control','app.runtime.mask_compute'):
            self.assertEqual(importlib.import_module(name).ROOT,ROOT,name)
        self.assertEqual(ROOT,Path(__file__).resolve().parents[1])
        self.assertTrue((TEMPLATES/'model_viewer.html').is_file())

    def test_asset_allowlist_preserves_urls_without_exposing_source_or_traversal(self):
        self.assertEqual(PUBLIC_ASSETS,{path.name for path in STATIC.iterdir() if path.is_file()})
        for name in PUBLIC_ASSETS:
            self.assertEqual(static_asset('/assets/'+name),STATIC/name)
        for path in ('/assets/../LICENSE','/assets/app/model_viewer.mjs','/assets/server.py',
                     '/assets/vendor/../server.py','/model_viewer.mjs'):
            self.assertIsNone(static_asset(path),path)

    def test_legacy_cli_entrypoints_work_outside_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ('web_app.py','pano_to_views.py'):
                result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/name),'--help'],
                                      cwd=temp,capture_output=True,text=True,encoding='utf8',timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('usage:',result.stdout)

    def test_packaged_cpu_worker_runs_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=base/'source.jpg';output=base/'views';output.mkdir()
            cv2.imwrite(str(source),np.full((32,64,3),120,np.uint8))
            settings=dict(width=32,height=32,fov=90,views=2,sample_every=1,
                          projection='panorama',quality='preview',start_seconds=0,end_seconds=0)
            original=Path.cwd()
            try:
                os.chdir(base)
                count=cpu_worker_views(source,output,settings)
            finally:
                os.chdir(original)
            metadata=json.loads((output/'transforms.json').read_text(encoding='utf8'))
            self.assertEqual(count,2)
            self.assertEqual(metadata['view_compute']['backend'],'CPU')


if __name__=='__main__':unittest.main()
