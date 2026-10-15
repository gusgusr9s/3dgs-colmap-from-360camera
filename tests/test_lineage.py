"""Real HTTP navigation and lineage with isolated local artifacts, no trainer."""
import html
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError
import json
import re

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import web_app as app
import model_variants as variants


class Links(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.urls = []
        self.feed(page)
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.urls.extend(value for key,value in attrs if key == 'href')


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lineage_check_',dir=app.ROOT/'web_data')
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to((app.ROOT/'web_data').resolve())
        self.raw, self.jobs, self.models, self.uploads, self.experiments = (self.root/name for name in ('raw','jobs','models','uploads','experiments'))
        for path in (self.raw,self.jobs,self.models,self.uploads,self.experiments):
            path.mkdir()
        self.patches = [patch.object(app,key,value) for key,value in {
            'RAW':self.raw,'JOBS':self.jobs,'MODELS':self.models,'UPLOADS':self.uploads,'EXPERIMENTS':self.experiments,
            'SOURCE_DIRECTORIES':{'raw':self.raw,'uploads':self.uploads},'ACTIVE_TASKS':set()}.items()]
        self.patches.append(patch.object(app.Handler,'log_message'))
        self.patches.append(patch.object(variants, 'VARIANTS', self.root/'variants'))
        for item in self.patches:
            item.start()
        for source,job in [('360 & space.jpg','jobA'),('other.jpg','jobB')]:
            app.cv2.imwrite(str(self.raw/source),app.np.zeros((32,64,3),app.np.uint8))
            (self.jobs/job).mkdir()
            app.save_job_manifest(job,{'label':job,'source_name':source,'source_key':'data/raw:'+source,'settings':app.DEFAULT_VIEW_SETTINGS})
            app.write_json(self.jobs/job/'transforms.json',{'frames':[]})
            app.write_json(self.jobs/job/'views_status.json',{'run_id':'v2','state':'ready'})
        self.source_key = 'data/raw:360 & space.jpg'
        self.run = '20261008_030000_1234abcd'
        for name,job,version,run in [('old.ply','jobA','v1',self.run),('new.ply','jobA','v2','20261008_030001_abcd1234'),('other.ply','jobB','v2','20261008_030002_abcdef01')]:
            (self.models/name).write_bytes(b'ply\n')
            record = {'job':job,'view_run_id':version,'run_id':run,'state':'done','model':name,'source_key':app.job_manifest(job)['source_key']}
            app.write_json(app.model_metadata_path(name),record)
            app.write_json(self.jobs/job/'model_runs'/run/'status.json',record)
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()
    def get(self,path,data=None):
        with urlopen(self.base+path,data=urlencode(data).encode() if data else None) as response:
            return response.read().decode('utf-8')
    def test_source_hub_and_filtered_libraries(self):
        url = app.source_route('raw',self.raw/'360 & space.jpg',preview=True)
        page = self.get(url)
        self.assertIn('source-jobs',page);self.assertIn('source-models',page)
        self.assertIn('old.ply',page);self.assertIn('new.ply',page);self.assertNotIn('other.ply',page)
        self.assertIn('source-create-form',page)
        self.assertIn('Source resolution: 64×32 px',page)
        self.assertIn('Source resolution: 64×32 px',self.get('/sources'))
        self.assertIn('data-template-select',page)
        jobs = self.get('/jobs?'+urlencode({'source':self.source_key}))
        self.assertIn('data-job="jobA"',jobs);self.assertNotIn('data-job="jobB"',jobs)
        for version,name,missing in [('v1','old.ply','new.ply'),('v2','new.ply','old.ply')]:
            filtered = self.get('/models?'+urlencode({'job':'jobA','view_run':version}))
            self.assertIn(name,filtered);self.assertNotIn(missing,filtered);self.assertNotIn('other.ply',filtered)
        all_models = self.get('/models?'+urlencode({'source':self.source_key}))
        self.assertIn('old.ply',all_models);self.assertIn('new.ply',all_models);self.assertNotIn('other.ply',all_models)

    def test_library_controls_are_scoped_to_collections(self):
        for url in ['/sources','/jobs','/models']:
            self.assertIn('id="library-search"',self.get(url))
        source = app.source_route('raw',self.raw/'360 & space.jpg',preview=True)
        for url in [source,'/job/jobA','/model-inputs/old.ply']:
            self.assertNotIn('id="library-search"',self.get(url))

    def test_model_library_job_config_requires_existing_job_and_shares_dialog(self):
        self.make_compression_record()
        for name in ['orphan.ply', 'uploaded.ply']:
            (self.models/name).write_bytes(b'ply\n')
        app.write_json(app.model_metadata_path('orphan.ply'), {'job':'missing'})
        page = self.get('/models')
        targets = re.findall(r'data-modal="(models-job-config-\d+)"', page)
        self.assertEqual(len(targets), 4, 'Three PLY rows and one SPZ row link to existing jobs')
        self.assertEqual(len(set(targets)), 2, 'Share one config dialog for each linked job')
        for target in set(targets):
            self.assertEqual(page.count('id="'+target+'"'), 1)
        for name in ['old.ply', 'new.ply', 'other.ply', 'orphan.ply', 'uploaded.ply']:
            row = next(row for row in re.findall(r'<article class="resource"[^>]*>.*?</article>', page, re.S)
                       if '<div class="resource-title">'+name+'</div>' in row)
            self.assertEqual('Check config จาก Job' in row, name not in {'orphan.ply','uploaded.ply'})
        self.assertIn('/api/job-config/jobA', page)
        self.assertIn('/api/job-config/jobB', page)

    def test_job_gallery_pages_preserve_total_and_reach_all_images(self):
        for index in range(97):
            (self.jobs/'jobA'/f'frame_{index:05}.jpg').write_bytes(b'sample')
        first = self.get('/job/jobA')
        second = self.get('/job/jobA?page=2')
        last = self.get('/job/jobA?page=999999')
        self.assertEqual(first.count('class="gallery-card"'),48)
        self.assertEqual(second.count('class="gallery-card"'),48)
        self.assertEqual(last.count('class="gallery-card"'),1)
        self.assertIn('data-view-count="97"',second)
        self.assertIn('frame_00047.jpg',first); self.assertNotIn('frame_00048.jpg',first)
        self.assertIn('frame_00048.jpg',second); self.assertIn('ภาพที่ 49',second)
        self.assertIn('frame_00096.jpg',last)
        self.assertIn('/job/jobA?page=2#views',Links(first).urls)
        self.assertEqual(first,self.get('/job/jobA?page=bad'))
        self.assertEqual(first,self.get('/job/jobA?page=-1'))

    def test_model_input_gallery_pages_use_frozen_dataset(self):
        dataset = self.jobs/'jobA'/'model_runs'/self.run/'dataset'
        dataset.mkdir()
        frames = []
        for index in range(49):
            filename = f'original_{index:05}.jpg'
            (dataset/filename).write_bytes(b'sample')
            frames.append({'file_path':filename,'frame_index':index})
        frames.extend([{'file_path':'../../../../raw/other.jpg'},{'file_path':'missing.jpg'}])
        app.write_json(dataset/'transforms.json',{'frames':frames})
        first = self.get('/model-inputs/old.ply')
        last = self.get('/model-inputs/old.ply?page=2')
        self.assertEqual(first.count('class="gallery-card"'),48)
        self.assertEqual(last.count('class="gallery-card"'),1)
        self.assertIn('original_00048.jpg',last); self.assertNotIn('original_00000.jpg',last)
        self.assertIn('49 ภาพ',first); self.assertNotIn('other.jpg',last)
        self.assertIn('/model-inputs/old.ply?page=2#input-gallery',Links(first).urls)
        app.write_json(self.jobs/'jobA'/'transforms.json',{'frames':[]})
        self.assertEqual(last,self.get('/model-inputs/old.ply?page=2'))

    def test_check_config_reports_generated_not_edited_settings(self):
        generated={**app.DEFAULT_VIEW_SETTINGS,'width':448,'height':448,'sample_every':8}
        plan={'effective_sample_every':8,'requested_sample_every':30,'start_seconds':1,'end_seconds':2,
              'sample_gap_seconds':8/30,'views_per_frame':16,'directions':[{'yaw':0,'pitch':0}]}
        app.write_json(self.jobs/'jobA'/'transforms.json',{'settings':generated,'capture_plan':plan,
                       'frames':[{'frame_index':30},{'frame_index':30},{'frame_index':38}]})
        app.write_json(self.jobs/'jobA'/'views_status.json',{'run_id':'v2','state':'ready',
                       'source_profile':{'width':1920,'height':960,'fps':30,'duration_seconds':10}})
        app.save_job_manifest('jobA',{'settings':{**generated,'width':1024}})
        result=json.loads(self.get('/api/job-config/jobA'))
        self.assertEqual(result['generated_settings']['width'],448)
        self.assertEqual(result['configured_settings']['width'],1024)
        self.assertEqual(result['requested_sample_every'],30)
        self.assertEqual(result['skipped_between_samples'],7)
        self.assertEqual(result['actual_positions'],2)
        self.assertEqual(result['actual_views'],3)
        for url in ['/jobs','/job/jobA']:
            page=self.get(url)
            self.assertIn('Check config',page)
            self.assertIn('เก็บ 1 ทุก 8 เฟรม · ข้าม 7',page)
            self.assertIn('1920×960 px',page)
        with self.assertRaises(HTTPError) as caught:self.get('/api/job-config/missing')
        self.assertEqual(caught.exception.code,404)

    def test_viewer_resolution_uses_frozen_model_source(self):
        record=app.read_json(app.model_metadata_path('old.ply'))
        record['source_profile']={'width':6144,'height':3072,'fps':2,'kind':'video',
                                  'duration_seconds':123,'frame_count':246}
        app.write_json(app.model_metadata_path('old.ply'),record)
        page=self.get('/model/old.ply')
        self.assertIn('Source resolution: 6144×3072 px',page)
        self.assertIn('2.00 FPS',page)
        self.assertNotIn('Source resolution: 64×32 px',page)
        self.assertIn('id="model-config-open"',page)
        self.assertIn('id="model-config-dialog"',page)
        result=json.loads(self.get('/api/model-config/old.ply'))
        self.assertEqual(result['source_profile']['width'],6144)

    def test_model_config_uses_frozen_dataset_after_views_change(self):
        directory=self.jobs/'jobA'/'model_runs'/self.run/'dataset'
        directory.mkdir()
        settings={**app.DEFAULT_VIEW_SETTINGS,'width':448,'height':448,'sample_every':2}
        app.write_json(directory/'transforms.json',{'settings':settings,
            'capture_plan':{'effective_sample_every':2,'requested_sample_every':8,'views_per_frame':16},
            'frames':[{'frame_index':0},{'frame_index':2}]})
        app.save_job_manifest('jobA',{'settings':{**settings,'width':1536,'sample_every':1}})
        result=json.loads(self.get('/api/model-config/old.ply'))
        self.assertEqual(result['generated_settings']['width'],448)
        self.assertEqual(result['capture_plan']['requested_sample_every'],8)
        self.assertEqual(result['capture_plan']['effective_sample_every'],2)
        self.assertEqual(result['actual_views'],2)
        with self.assertRaises(HTTPError): self.get('/api/model-config/missing.ply')

    def test_ambiguous_legacy_filename_is_not_assigned_to_two_sources(self):
        name = '360 & space.jpg'
        (self.uploads/name).write_bytes((self.raw/name).read_bytes())
        stored = app.read_json(self.jobs/'jobA'/'job.json')
        stored.pop('source_key')
        app.write_json(self.jobs/'jobA'/'job.json',stored)
        self.assertEqual(app.jobs_for_source(self.source_key,name),[])
        self.assertEqual(app.jobs_for_source('uploads:'+name,name),[])
        self.assertIn('ยังระบุ Source ต้นทางไม่ได้',app.source_link(app.job_manifest('jobA')))
        # A captured model key remains authoritative if the job input is changed later.
        stored['source_key'] = 'uploads:'+name
        app.write_json(self.jobs/'jobA'/'job.json',stored)
        self.assertEqual(app.lineage_source(app.model_info(self.models/'old.ply'))[0],self.source_key)
    def test_context_links_on_job_poses_viewer(self):
        page = self.get('/job/jobA')
        self.assertIn('ชุด Views ปัจจุบัน',page);self.assertIn('ชุด Views ก่อนหน้า',page)
        for path in ['/job/jobA','/poses/jobA','/model/new.ply','/model/old.ply']:
            page = self.get(path)
            self.assertNotIn('__LINEAGE__',page)
            links = Links(page).urls
            self.assertIn(app.source_route('raw',self.raw/'360 & space.jpg',preview=True),links)
            self.assertIn('/job/jobA',links)
        new = self.get('/model/new.ply');old = self.get('/model/old.ply')
        self.assertIn('id="sibling-model"',new)
        self.assertNotIn('value="/model/old.ply"',new)
        self.assertNotIn('/poses/jobA',Links(old).urls)
        return_paths = []
        class Forms(HTMLParser):
            def handle_starttag(inner,tag,attrs):
                values = dict(attrs)
                if tag == 'input' and values.get('name') == 'return_to':return_paths.append(values.get('value'))
        Forms().feed(self.get('/models?job=jobA&view_run=v2'))
        self.assertIn('/models?job=jobA&view_run=v2',return_paths)
    def test_immutable_model_inputs_after_new_views(self):
        dataset = self.jobs/'jobA'/'model_runs'/self.run/'dataset'
        app.cv2.imwrite(str(self.jobs/'jobA'/'old.jpg'),app.np.zeros((16,16,3),app.np.uint8))
        (dataset/'images').mkdir(parents=True)
        (dataset/'images'/'original.jpg').write_bytes((self.jobs/'jobA'/'old.jpg').read_bytes())
        app.write_json(dataset/'transforms.json',{'frames':[{'file_path':'images/original.jpg','frame_index':42}]})
        page = self.get('/model-inputs/old.ply')
        self.assertIn('original.jpg',page);self.assertIn('เฟรม 42',page)
        image_url = next(url for url in Links(page).urls if url.endswith('original.jpg'))
        with urlopen(self.base+image_url) as response:
            self.assertTrue(response.headers['Content-Type'].startswith('image/'))
        app.write_json(self.jobs/'jobA'/'views_status.json',{'run_id':'v3','state':'ready'})
        self.assertIn('original.jpg',self.get('/model-inputs/old.ply'))
        self.assertIn('ไม่ได้เก็บสำเนา',self.get('/model-inputs/new.ply'))
        self.assertIsNone(app.model_quality_page('jobA','20261008_030001_abcd1234'))
        self.assertFalse(app.quality_available('jobA',self.run+'../../'))

    def test_saved_model_cameras_and_images_use_immutable_dataset(self):
        dataset = self.jobs/'jobA'/'model_runs'/self.run/'dataset'
        (dataset/'images').mkdir(parents=True)
        pixels = app.np.zeros((16,16,3),app.np.uint8)
        app.cv2.imwrite(str(dataset/'images'/'front.jpg'),pixels)
        app.cv2.imwrite(str(dataset/'images'/'side.jpg'),pixels)
        matrix = app.np.eye(4).tolist()
        frames = [{'file_path':'images/'+name,'frame_index':42,'yaw_deg':yaw,
                   'transform_matrix':matrix} for name,yaw in [('side.jpg',90),('front.jpg',0)]]
        app.write_json(dataset/'transforms.json',{'w':16,'h':16,'fl_x':8,'frames':frames})
        app.write_json(self.jobs/'jobA'/'brush_data'/'transforms.json',{'frames':[]})
        data = json.loads(self.get('/api/model-cameras/old.ply'))
        self.assertEqual(data['view_run_id'],'v1')
        self.assertEqual(len(data['cameras']),1)
        camera = data['cameras'][0]
        self.assertEqual(camera['frame_index'],42)
        self.assertEqual(camera['fov_x'],90)
        self.assertEqual(camera['transform_matrix'],matrix)
        self.assertIn('/model_runs/'+self.run+'/dataset/images/front.jpg',camera['image_url'])
        with urlopen(self.base+camera['image_url']) as response:
            self.assertEqual(response.read(),(dataset/'images'/'front.jpg').read_bytes())
        self.assertEqual(json.loads(self.get('/api/model-cameras/new.ply'))['cameras'],[])
        # Never publish files outside the snapshot through a camera image link.
        frames[1]['file_path']='../../status.json'
        app.write_json(dataset/'transforms.json',{'w':16,'h':16,'fl_x':8,'frames':frames})
        self.assertEqual(json.loads(self.get('/api/model-cameras/old.ply'))['cameras'],[])

    def test_spark_viewer_assets_and_native_comparison(self):
        page = self.get('/model/old.ply')
        self.assertIn('"renderer": "spark"',page)
        self.assertIn('/assets/vendor/spark-2.3.1/spark.module.js',page)
        self.assertIn('id="capture-position"',page)
        for control in ['walk-start', 'free-view', 'pointer-lock', 'walk-height', 'movement-speed', 'gamepad-toggle', 'gamepad-status', 'mini-map-toggle', 'mini-map-svg']:
            self.assertIn(f'id="{control}"',page)
        self.assertIn('value="walk"',page)
        self.assertIn('value="free"',page)
        self.assertNotIn('__CONFIG__',page)
        self.assertNotIn('__REFINE_DIALOG__',page)
        self.assertNotRegex(page,r'__[A-Z_]+__')
        native = self.get('/model/old.ply?renderer=native')
        self.assertIn('"renderer": "native"',native)
        self.assertIn('three@0.186.1',native)
        for asset in ['/assets/model_viewer.mjs','/assets/model_navigation.mjs','/assets/model_gamepad.mjs','/assets/model_minimap.mjs',
                      '/assets/vendor/three-0.180.0/three.module.js',
                      '/assets/vendor/three-0.180.0/three.core.js',
                      '/assets/vendor/three-0.180.0/OrbitControls.js',
                      '/assets/vendor/three-0.180.0/Pass.js',
                      '/assets/vendor/spark-2.3.1/spark.module.js']:
            with urlopen(self.base+asset) as response:
                self.assertIn('javascript',response.headers['Content-Type'])
                self.assertGreater(len(response.read()),100)

    def make_compression_record(self, model='old.ply'):
        source = self.models/model
        directory = variants.variant_directory(model)
        directory.mkdir(parents=True)
        data = b'compressed-test-file'
        (directory/'fast.spz').write_bytes(data)
        metadata = {'state':'ready','source':variants.source_signature(source),'bytes':len(data),
                    'model':model,'splats':123,'sh_degree':3,'sha256':'a'*64,'source_sha256':'b'*64}
        variants.write_record_json(directory/'fast.json',metadata)
        return variants.register_variant(source,metadata)['record_id'], data

    def test_compressed_records_downloads_lineage_and_renderer(self):
        identifier, data = self.make_compression_record()
        page = self.get('/model/old.ply')
        self.assertIn('"variant": "fast"',page)
        self.assertIn('/compressed-download/'+identifier+'.spz',page)
        self.assertIn('ดาวน์โหลด PLY เต็ม',page)
        self.assertIn('ดาวน์โหลด SPZ บีบอัด',page)
        self.assertIn('value="compress_model"',page)
        full = self.get('/model/old.ply?variant=full')
        self.assertIn('"variant": "full"',full)
        native = self.get('/model/old.ply?renderer=native&variant=fast')
        self.assertIn('"variant": "full"',native)
        record = self.get('/compression/'+identifier)
        self.assertIn('/model/old.ply?variant=full',record)
        self.assertIn('/job/jobA',record)
        self.assertIn(self.run,json.loads(self.get('/api/compression/'+identifier))['parent_info']['run_id'])
        with urlopen(self.base+'/compressed-download/'+identifier+'.spz') as response:
            self.assertEqual(response.read(),data)
            self.assertIn('immutable',response.headers['Cache-Control'])
        library = self.get('/models?'+urlencode({'job':'jobA','view_run':'v1'}))
        self.assertIn('data-compression-id="'+identifier+'"',library)
        self.assertNotIn('data-compression-id="'+identifier+'"',self.get('/models?'+urlencode({'job':'jobA','view_run':'v2'})))
        with self.assertRaises(HTTPError) as mismatch:
            self.get('/model/new.ply?compression='+identifier)
        self.assertEqual(mismatch.exception.code,404)
        with patch.object(app,'start_fast_variant',return_value=identifier) as start:
            destination=self.get('/actions',{'action':'compress_model','model':'old.ply'})
            self.assertIn('Model บีบอัด',destination)
            start.assert_called_once_with(self.models/'old.ply')

    def test_compression_record_survives_parent_rename_and_records_delete(self):
        identifier, data = self.make_compression_record()
        app.rename_model_file('old.ply','renamed.ply')
        record = variants.compression_record(identifier)
        self.assertEqual(record['parent_model'],'renamed.ply')
        self.assertEqual(record['parent_info']['model'],'old.ply','keep the original snapshot')
        self.assertIn('/model/renamed.ply?variant=full',self.get('/compression/'+identifier))
        self.assertEqual(variants.available_variant(self.models/'renamed.ply')['record_id'],identifier)
        self.assertIn('"variant": "fast"',self.get('/model/renamed.ply?compression='+identifier))
        app.delete_model_file('renamed.ply')
        self.assertEqual(variants.compression_record(identifier)['state'],'deleted')
        self.assertIn('ไฟล์ถูกลบแล้ว',self.get('/compression/'+identifier))
        with self.assertRaises(HTTPError): self.get('/compressed-download/'+identifier+'.spz')

    def test_repaired_model_link(self):
        with self.assertRaises(HTTPError) as error:
            self.get('/assets/vendor/../web_app.py')
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(HTTPError) as error:
            self.get('/api/model-cameras/../old.ply')
        self.assertEqual(error.exception.code,422)
        info = app.read_json(app.model_metadata_path('new.ply'))
        info['reexported_from'] = 'old.ply'
        app.write_json(app.model_metadata_path('new.ply'),info)
        self.assertIn('เปิดรุ่นแก้รายละเอียดแล้ว',self.get('/model/old.ply'))

    def test_refinement_action_starts_one_model_version(self):
        dataset = self.jobs/'jobA'/'model_runs'/self.run/'dataset'
        app.write_json(dataset/'transforms.json',{'frames':[]})
        checkpoint = dataset.parent/'brush_export'/'splat.ply'
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(b'checkpoint')
        with patch.object(app,'start_refinement',return_value=True) as start:
            page = self.get('/actions',{'action':'refine_model','model':'old.ply'})
        start.assert_called_once_with('old.ply')
        self.assertIn('/model/old.ply',Links(page).urls)
        self.assertIn('id="refine-dialog"',self.get('/model/old.ply'))
        self.assertEqual(checkpoint.read_bytes(),b'checkpoint')

    def test_quality_page_handles_legacy_null_selection(self):
        directory = self.jobs/'jobA'/'model_runs'/self.run
        dataset = directory/'dataset'
        (dataset/'images').mkdir(parents=True)
        (dataset/'masks').mkdir()
        export = directory/'brush_export'/'eval_1'
        export.mkdir(parents=True)
        pixels = app.np.zeros((16,16,3),app.np.uint8)
        app.cv2.imwrite(str(dataset/'images'/'frame.png'),pixels)
        app.cv2.imwrite(str(dataset/'masks'/'frame.png'),app.np.full((16,16),255,app.np.uint8))
        app.cv2.imwrite(str(export/'frame.png'),pixels)
        app.write_json(dataset/'transforms.json',{'frames':[{'file_path':'images/frame.png'}]})
        record = app.read_json(directory/'status.json')
        record.update(selection=None,quality={'training_steps':1,'eval_psnr_mean_db':99})
        app.write_json(directory/'status.json',record)
        import quality_metrics
        with patch.object(app.cv2, 'imread', side_effect=AssertionError('Page decoded an image')), patch.object(quality_metrics, 'decode', side_effect=AssertionError('Page calculated every view')):
            page = self.get('/quality/jobA/'+self.run)
        self.assertIn('99.00 dB',page)
        self.assertIn('/dataset/images/frame.png',page)
        self.assertIn('/assets/quality_viewer.mjs',page)
        endpoint = '/api/quality-metrics/jobA/'+self.run+'?frame=frame'
        self.assertEqual(json.loads(self.get(endpoint))['psnr'],99)
        with patch.object(quality_metrics, 'decode', side_effect=AssertionError('Cached metric decoded again')):
            self.assertEqual(json.loads(self.get(endpoint))['psnr'],99)
        app.cv2.imwrite(str(export/'frame.png'),app.np.full((16,16,3),64,app.np.uint8))
        self.assertLess(json.loads(self.get(endpoint))['psnr'],20)
        with self.assertRaises(HTTPError) as error:
            self.get('/api/quality-metrics/jobA/'+self.run+'?frame=../../other')
        self.assertEqual(error.exception.code,404)
    def test_rename_source_preserves_all_model_links(self):
        self.get('/actions',{'action':'rename_source','source':self.source_key,'new_name':'renamed.jpg','return_to':'/sources'})
        for name in ['old.ply','new.ply']:
            info = app.model_info(self.models/name)
            self.assertEqual(info['source_key'],'data/raw:renamed.jpg')
            self.assertIn('/preview/raw/renamed.jpg',Links(self.get('/model/'+name)).urls)
        self.assertIn('old.ply',self.get('/preview/raw/renamed.jpg'))
    def test_deleted_job_keeps_source_and_model_provenance(self):
        app.write_json(self.jobs/'jobA'/'job.json',{})
        # Simulate a deleted job without deleting the model or source.
        with patch.object(app,'job_directory',return_value=None):
            page = app.lineage_markup(app.model_info(self.models/'old.ply'),'model')
            self.assertIn('ถูกลบแล้ว',page)
            self.assertIn('/preview/raw/360%20%26%20space.jpg',Links(page).urls)

    def test_legacy_model_retains_source_link_when_job_is_deleted(self):
        legacy = self.models/'jobA.ply'
        legacy.write_bytes(b'ply\n')
        app.write_json(app.model_metadata_path(legacy.name),{'job':'jobA','model':legacy.name})
        self.get('/actions',{'action':'delete_job','job':'jobA','return_to':'/models'})
        self.assertIsNone(app.job_directory('jobA'))
        self.assertTrue(legacy.is_file())
        info = app.model_info(legacy)
        self.assertEqual(info['source_key'],self.source_key)
        self.assertIn(app.source_route('raw',self.raw/'360 & space.jpg',preview=True),Links(self.get('/model/jobA.ply')).urls)
        app.write_json(self.experiments/'archived.json',{'batch':'archived','cases':[{
            'id':'saved','source_key':self.source_key,'job':'jobA','state':'done','model':'jobA.ply',
            'status':{'state':'done','model':'jobA.ply'}}]})
        self.get('/actions',{'action':'rename_source','source':self.source_key,'new_name':'archived-source.jpg','return_to':'/sources'})
        self.assertIn('/preview/raw/archived-source.jpg',Links(self.get('/experiments?batch=archived')).urls)
    def test_results_show_failure_and_real_render_provenance(self):
        app.write_json(self.experiments/'batch.json',{'batch':'batch','cases':[{'id':'missing','source':'missing.mp4','state':'error','error':'not enough parallax'}]})
        page = self.get('/experiments?batch=batch')
        self.assertIn('สร้างไม่ได้',page);self.assertIn('not enough parallax',page)
        self.assertIn('ไม่ใช่ภาพหน้าจอ Three.js',page)
        self.assertIn('<strong>0</strong><span>สร้าง Model สำเร็จ</span>',page)
        self.assertIn('<strong>1</strong><span>สร้างไม่ได้ / ต้องแก้ข้อมูล</span>',page)

    def test_completed_experiment_keeps_its_original_model_version(self):
        app.write_json(self.jobs/'jobA'/'3d_status.json',{'state':'done','model':'new.ply'})
        app.write_json(self.experiments/'versions.json',{'batch':'versions','cases':[{
            'id':'saved','source_key':self.source_key,'job':'jobA','state':'done','model':'old.ply',
            'status':{'state':'done','model':'old.ply','run_id':self.run,'quality':{}}}]})
        page = self.get('/experiments?batch=versions')
        self.assertIn('/model/old.ply',Links(page).urls)
        self.assertNotIn('/model/new.ply',Links(page).urls)

    def test_jpeg_recovery_preserves_pixels_and_source(self):
        source = self.raw/'360 & space.jpg'
        original = source.read_bytes()
        dataset = self.root/'codec'
        (dataset/'images').mkdir(parents=True)
        target = dataset/'images'/'frame.jpg'
        target.write_bytes(original)
        app.write_json(dataset/'transforms.json',{'frames':[{'file_path':'images/frame.jpg'}]})
        self.assertEqual(app.recover_training_jpegs(dataset),1)
        self.assertEqual(app.read_json(dataset/'transforms.json')['frames'][0]['file_path'],'images/frame.png')
        self.assertTrue(app.np.array_equal(app.cv2.imread(str(target)),app.cv2.imread(str(target.with_suffix('.png')))))
        self.assertEqual(source.read_bytes(),original)
        self.assertEqual(app.recover_training_jpegs(dataset),0)

    def test_batch_lock_prevents_competing_report_writers(self):
        import scripts.test_real_videos as runner
        lock = self.root/'report.lock'
        with runner.batch_lock(lock):
            with self.assertRaises(RuntimeError):
                with runner.batch_lock(lock):
                    self.fail('Second writer acquired a live lock')
        self.assertFalse(lock.exists())

    def test_colmap_matching_progress_is_visible_before_training(self):
        log = self.jobs/'jobA'/'model_runs'/self.run/'3d.log'
        log.write_text('Processing image [42/152]\n',encoding='utf-8')
        status = app.live_training_status('jobA',{'state':'running','stage':'model','run_id':self.run,'progress':21,'step':'COLMAP: sequential matcher on CPU'})
        self.assertEqual(status['matching_image'],42)
        self.assertIn('42/152',status['step'])
        self.assertGreater(status['progress'],21)
        self.assertLess(status['progress'],29)
        log.write_text('Registering image #116 (num_reg_frames=113)\n',encoding='utf-8')
        status = app.live_training_status('jobA',{'state':'running','stage':'model','run_id':self.run,'progress':29,'step':'COLMAP: estimating one pose per panorama'})
        self.assertEqual(status['registered_positions_so_far'],113)
        self.assertIn('113 ตำแหน่ง',status['step'])
        app.write_json(self.jobs/'jobA'/'brush_data'/'transforms.json',{'frames':[{},{}]})
        (self.jobs/'jobA'/'brush_data'/'masks').mkdir()
        (self.jobs/'jobA'/'brush_data'/'masks'/'frame.png').write_bytes(b'png')
        status = app.live_training_status('jobA',{'state':'running','stage':'model','run_id':self.run,'progress':39,'step':'Masking people and the camera operator'})
        self.assertIn('1/2 ภาพ',status['step'])

    def test_views_can_be_adjusted_directly_on_job_without_losing_models(self):
        page = self.get('/job/jobA')
        self.assertIn('id="adjust-job-views-form"',page)
        self.assertIn('data-settings-mode="edit"',page)
        with patch.object(app,'start_views') as start:
            self.get('/actions',{'action':'reprocess_job','job':'jobA','label':'Adjusted',
                'return_to':'/job/jobA',**app.DEFAULT_VIEW_SETTINGS})
            self.assertEqual(start.call_args.args[0],'jobA')
            self.assertTrue(start.call_args.kwargs['reprocess'])
        self.assertEqual(len(app.model_files_for_job('jobA')),2)


if __name__ == '__main__':
    unittest.main()
