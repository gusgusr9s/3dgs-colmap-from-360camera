import re
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from app.web import server as app
from app.web.tutorial import tutorial_content
from app.pipeline.view_suggestions import PRESETS


class TutorialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def test_tutorial_has_complete_flow_and_dynamic_profiles(self):
        with urlopen(self.base+'/tutorial') as response:
            text=response.read().decode('utf-8')
        self.assertIn('aria-current="page"',text)
        for chapter in ['record','upload','profile','views','poses','model']:
            self.assertIn('id="'+chapter+'"',text)
        self.assertIn('Tutorial · คู่มือ',text)
        self.assertIn('WASD',text);self.assertIn('Esc',text)
        for preset in PRESETS.values():self.assertIn(str(preset['resolution'])+'×'+str(preset['resolution']),text)
        for value in app.QUALITY_PROFILES.values():self.assertIn(f'{value["steps"]:,}',text)
        self.assertIn('ไม่ใช่ภาพหน้าจอ',text)
    def test_assets_are_portable_without_original_job(self):
        for name,content_type in [('dji-view.jpg','image/jpeg'),('dji-poses.svg','image/svg+xml')]:
            with urlopen(self.base+'/assets/tutorial/'+name) as response:
                self.assertIn(content_type,response.headers['Content-Type'])
                self.assertGreater(len(response.read()),1000)
        with urlopen(self.base+'/assets/tutorial.mjs') as response:self.assertIn(b'selectRoute',response.read())
    def test_tutorial_asset_path_is_allowlisted(self):
        with self.assertRaises(HTTPError) as caught:urlopen(self.base+'/assets/tutorial/README.md')
        self.assertEqual(caught.exception.code,404)
    def test_reading_demo_does_not_offer_training_action(self):
        text=tutorial_content(PRESETS,app.QUALITY_PROFILES)
        self.assertNotIn('method="post"',text);self.assertNotIn('action="generate3d"',text)
        ids=re.findall(r'\bid="([^"]+)"',text);self.assertEqual(len(ids),len(set(ids)))


if __name__=='__main__':unittest.main()
