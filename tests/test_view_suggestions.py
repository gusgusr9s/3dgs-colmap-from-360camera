"""Profile suggestions, history evidence and immutable camera subsets."""

import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import server as app
from app.pipeline.view_suggestions import probe_source, suggest_view_settings, view_estimate
from app.pipeline.training_selection import build_training_subset, validate_selection
from plyfile import PlyData, PlyElement


def profile(width=4096, height=2048, fps=5, count=77, kind="video"):
    return {"name": "sample.mp4", "width": width, "height": height, "fps": fps,
            "frame_count": count, "duration_seconds": count/fps if fps else None,
            "kind": kind, "aspect_ratio": width/height, "detected_projection": "panorama"}


class SuggestionsTests(unittest.TestCase):
    def test_low_fps_and_actual_history(self):
        settings={"width":768,"height":768,"fov":90,"views":8,"sample_every":1,"quality":"high","projection":"panorama"}
        result=suggest_view_settings(profile(fps=2), [{"job":"previous","level":2,"settings":settings}])
        self.assertEqual(result["settings"],{**settings, 'sampling_guard':'manual'})
        self.assertEqual(result["estimate"]["total_views"],1232)
        self.assertEqual(result["history"]["selected"]["job"],"previous")
        self.assertIn("previous",result["reasons"]["width"])

    def test_presets_respect_source_resolution(self):
        for quality,side in (("preview",640),("high",1024),("ultra",1536)):
            result=suggest_view_settings(profile(width=8192,height=4096), quality=quality)
            self.assertEqual(result["settings"]["width"],side)
        result=suggest_view_settings(profile(width=2048,height=1024), quality="ultra")
        self.assertEqual(result["settings"]["width"],512)

    def test_successful_ultra_source_is_the_default_but_manual_quality_wins(self):
        settings={'width':1536,'height':1536,'fov':90,'views':8,'sample_every':1,
                  'quality':'ultra','projection':'panorama'}
        history=[{'job':'concourse','level':2,'coverage':1,'psnr':23,'settings':settings}]
        source=profile(width=6144,height=3072,fps=2,count=246)
        self.assertEqual(suggest_view_settings(source,history)['settings']['quality'],'ultra')
        self.assertEqual(suggest_view_settings(source,history)['settings']['width'],1536)
        self.assertEqual(suggest_view_settings(source,history,quality='high')['settings']['quality'],'high')
        history[0]['level']=0
        self.assertEqual(suggest_view_settings(source,history)['settings']['quality'],'high')

    def test_unproven_history_does_not_override_overlap_or_sampling(self):
        history=[{"job":"views","level":0,"settings":{"quality":"high","projection":"panorama",
            "width":512,"height":512,"fov":30,"views":1,"sample_every":50}}]
        result=suggest_view_settings(profile(),history)
        self.assertEqual(result["settings"]["width"],512)
        self.assertEqual(result["settings"]["fov"],90)
        self.assertEqual(result["settings"]["views"],8)
        self.assertEqual(result["settings"]["sample_every"],2)

    def test_unsafe_historical_values_are_constrained(self):
        history=[{"job":"model","level":2,"settings":{"quality":"high","projection":"panorama",
            "width":8192,"height":8192,"fov":90,"views":1,"sample_every":100}}]
        result=suggest_view_settings(profile(),history)
        self.assertEqual(result["settings"]["width"],1024)
        self.assertEqual(result["settings"]["views"],8)
        self.assertEqual(result["settings"]["sample_every"],2)

    def test_walkthrough_keeps_aspect_and_one_view(self):
        source={**profile(1920,1080,30,300),"detected_projection":"walkthrough"}
        result=suggest_view_settings(source)
        self.assertEqual(result["settings"]["views"],1)
        self.assertEqual(result["settings"]["width"],1536)
        self.assertEqual(result["settings"]["height"],864)
        self.assertEqual(result["estimate"]["views_per_frame"],1)
        self.assertEqual(result["settings"]["sample_every"],10)

    def test_image_and_unknown_fps(self):
        self.assertTrue(suggest_view_settings(profile(fps=None,count=1,kind="image"))["warnings"])
        result=suggest_view_settings(profile(fps=None))
        self.assertEqual(result["settings"]["sample_every"],1)
        self.assertIsNone(result["estimate"]["sample_gap_seconds"])

    def test_manual_sampling_estimate_matches_runtime_limit(self):
        settings=suggest_view_settings(profile())["settings"]
        settings["sample_every"]=10
        self.assertEqual(view_estimate(profile(),settings)["effective_sample_every"],10)
        settings['sampling_guard']='auto'
        self.assertEqual(view_estimate(profile(),settings)["effective_sample_every"],1)

    def test_source_defaults_scale_ten_frames_at_thirty_fps(self):
        from app.pipeline.capture_preview import sampling_plan
        for fps, interval in ((2,1),(5,2),(24,8),(25,8),(29.97,10),(30,10),(50,17),(60,20),(120,40)):
            with self.subTest(fps=fps):
                source=profile(fps=fps,count=300)
                result=suggest_view_settings(source)
                settings=result['settings']
                self.assertEqual(settings['sample_every'],interval)
                self.assertEqual(settings['sampling_guard'],'manual')
                plan=sampling_plan(source,settings)
                self.assertEqual(plan['effective_sample_every'],interval)
                self.assertEqual(plan['positions'],result['estimate']['positions'])
                self.assertAlmostEqual(plan['sample_gap_seconds'],interval/fps)
                self.assertIn(f'ทุก {interval} เฟรม',result['reasons']['sample_every'])

    def test_old_thirty_frame_history_does_not_restore_sparse_default(self):
        history=[{'job':'old','level':2,'settings':{**app.DEFAULT_VIEW_SETTINGS,'sample_every':30}}]
        for fps, interval in ((30,10),(60,20)):
            self.assertEqual(suggest_view_settings(profile(fps=fps),history)['settings']['sample_every'],interval)

    def test_fov_override_updates_coverage(self):
        result=suggest_view_settings(profile(),fov=60)
        self.assertEqual(result["settings"]["views"],12)
        self.assertEqual(result["settings"]["width"],640)

    def test_file_probe_invalidates_when_source_changes(self):
        with tempfile.TemporaryDirectory(dir=app.ROOT/"web_data",prefix="probe_check_") as temporary:
            root=Path(temporary).resolve(); assert root.is_relative_to((app.ROOT/"web_data").resolve())
            file=root/"source.png"
            app.cv2.imwrite(str(file),app.np.zeros((100,200,3),dtype=app.np.uint8))
            first=probe_source(file)
            app.cv2.imwrite(str(file),app.np.ones((200,300,3),dtype=app.np.uint8)*200)
            second=probe_source(file)
            self.assertNotEqual(first["fingerprint"],second["fingerprint"])
            self.assertEqual(second["detected_projection"],"walkthrough")


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=app.ROOT/"web_data",prefix="selection_check_")
        self.root=Path(self.temporary.name).resolve()
        assert self.root.is_relative_to((app.ROOT/"web_data").resolve())
        self.job=self.root/"job"; self.dataset=self.job/"brush_data"; self.dataset.mkdir(parents=True)
        frames=[]; cameras=[]
        for index in range(4):
            matrix=app.np.eye(4);matrix[0,3]=index*.2
            name=f"images/frame_{index:06}_view_00.jpg"
            target=self.dataset/name;target.parent.mkdir(exist_ok=True)
            app.cv2.imwrite(str(target),app.np.zeros((32,32,3),dtype=app.np.uint8))
            frames.append({"frame_index":index,"file_path":name,"transform_matrix":matrix.tolist()})
            cameras.append({"frame_index":index,"position":matrix[:3,3].tolist(),"views":[index]})
        self.transforms={"frames":frames,"w":32,"h":32,"fl_x":16,"fl_y":16,"cx":16,"cy":16}
        app.write_json(self.dataset/"transforms.json",self.transforms)
        app.write_json(self.job/"transforms.json",{"source_mode":"walkthrough_video"})
        vertices=app.np.zeros(1000,dtype=[(key,"<f4") for key in ("x","y","z")])
        vertices["x"]=app.np.linspace(-1,1,1000);vertices["z"]=-4
        PlyData([PlyElement.describe(vertices,"vertex")],text=True).write(self.dataset/"sparse_pc.ply")
        self.preview={"input_signature":"signature","cameras":cameras,"segments":[]}

    def tearDown(self):
        self.temporary.cleanup()

    def test_three_positions_create_immutable_subset(self):
        before=(self.dataset/"transforms.json").read_bytes()
        result=build_training_subset(self.job,self.root/"subset",[0,1,2],self.preview)
        self.assertEqual(result["positions"],3)
        self.assertEqual(result["views"],3)
        self.assertEqual(result["sparse_points"],1000)
        subset=app.read_json(self.root/"subset"/"transforms.json")
        self.assertEqual(subset["frames"],self.transforms["frames"][:3])
        self.assertEqual((self.dataset/"transforms.json").read_bytes(),before)

    def test_rejects_stale_unknown_or_too_few(self):
        for frames,signature in (([0,1],"signature"),([0,1,99],"signature"),([0,1,2],"old"),([True,1,2],"signature")):
            with self.assertRaises(ValueError):validate_selection(self.preview,frames,signature)

    def test_rejects_stationary_positions(self):
        for camera in self.preview["cameras"]:camera["position"]=[0,0,0]
        with self.assertRaises(ValueError):validate_selection(self.preview,[0,1,2],"signature")


if __name__ == "__main__":
    unittest.main(verbosity=2)
