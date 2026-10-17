"""Search across gallery pages, preserved immutable inputs, and edited history isolation."""
import html
from pathlib import Path
import unittest
from unittest.mock import patch

import collection_ui
import web_app as app
import test_lineage as fixtures


class CollectionMathTests(unittest.TestCase):
    def test_natural_frame_order_search_and_metadata(self):
        images=[Path('frame_10.jpg'),Path('frame_2.jpg'),Path('frame_1.jpg')]
        self.assertEqual([p.name for p in collection_ui.gallery_items(images)],['frame_1.jpg','frame_2.jpg','frame_10.jpg'])
        self.assertEqual(collection_ui.gallery_items(images,'frame_2'),[images[1]])
        self.assertEqual(collection_ui.gallery_items(images,'90','frame-asc',{'frame_10.jpg':{'yaw_deg':90}}),[images[0]])
        items=[(images[0],{'frame_index':1}),(images[1],{'frame_index':9})]
        self.assertEqual(collection_ui.gallery_items(items,order='frame-desc')[0][1]['frame_index'],9)


class CollectionHTTPTests(unittest.TestCase):
    setUp=fixtures.LineageTests.setUp
    tearDown=fixtures.LineageTests.tearDown
    get=fixtures.LineageTests.get

    def test_job_search_precedes_pagination_and_keeps_live_total(self):
        for i in range(97):(self.jobs/'jobA'/f'frame_{i:05}.jpg').write_bytes(b'test')
        page=self.get('/job/jobA?q=00096')
        self.assertEqual(page.count('class="gallery-card"'),1)
        self.assertIn('frame_00096.jpg',page);self.assertIn('data-view-count="97"',page)
        self.assertNotIn('frame_00000.jpg',page)
        page=self.get('/job/jobA?q=frame_&sort=frame-desc')
        self.assertIn('frame_00096.jpg',page);self.assertNotIn('frame_00000.jpg',page)
        self.assertIn('/job/jobA?q=frame_&sort=frame-desc&page=2#views',fixtures.Links(page).urls)
        malicious='<script>alert(1)</script>'
        page=self.get('/job/jobA?'+app.urlencode({'q':malicious}))
        self.assertIn(html.escape(malicious,quote=True),page);self.assertNotIn(malicious,page)

    def test_frozen_gallery_search_and_capability_document(self):
        dataset=self.jobs/'jobA'/'model_runs'/self.run/'dataset';dataset.mkdir()
        frames=[]
        for i in range(97):
            name=f'original_{i:05}.jpg';(dataset/name).write_bytes(b'input')
            frames.append({'file_path':name,'frame_index':i})
        app.write_json(dataset/'transforms.json',{'frames':frames})
        page=self.get('/model-inputs/old.ply?q=00096&sort=frame-desc')
        self.assertEqual(page.count('class="gallery-card"'),1)
        self.assertIn('original_00096.jpg',page)
        self.assertIn('ค้นหาทั้งชุด',page)
        self.assertIn('/assets/ui_controls.mjs',self.get('/sources'))
        for route in ['/project-overview','/project-overview/download']:
            overview=self.get(route)
            self.assertIn('45 ความสามารถ',overview)
            self.assertIn('ยังไม่รองรับ',overview)
            self.assertIn('GLB',overview)
            self.assertNotIn('https://cdn.',overview)

    def test_edited_model_never_polls_or_links_parent_evaluation(self):
        parent=app.model_info(self.models/'old.ply')
        (self.models/'edited.ply').write_bytes(b'ply\n')
        edited={**parent,'model':'edited.ply','stage':'model_edit','edit':{'parent_model':'old.ply'},
                'quality':{'published_splats':4},'evaluation_note':'ยังไม่ได้ประเมิน'}
        app.write_json(app.model_metadata_path('edited.ply'),edited)
        with patch.object(app,'quality_available',return_value=True):
            row=app.model_history_row('jobA',edited)
            self.assertNotIn('data-model-run',row);self.assertNotIn('data-job=',row)
            self.assertNotIn('/quality/',row);self.assertIn('ยังไม่ได้ประเมิน Render หลังแก้ไข',row)
            original=app.model_history_row('jobA',parent)
            self.assertEqual(original.count('>ตรวจภาพ Render</a>'),1)
