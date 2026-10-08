"""Timing, model versions and HTTP actions; trainer output is simulated."""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_app as app
from task_history import TaskTimer, format_duration, format_timestamp


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="history_check_", dir=app.ROOT / "web_data")
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to((app.ROOT / "web_data").resolve())
        self.jobs, self.models, self.raw, self.uploads = (self.root / name for name in ("jobs", "models", "raw", "uploads"))
        for directory in (self.jobs, self.models, self.raw, self.uploads):
            directory.mkdir()
        self.patches = [patch.object(app, "JOBS", self.jobs), patch.object(app, "MODELS", self.models),
                        patch.object(app, "RAW", self.raw), patch.object(app, "UPLOADS", self.uploads),
                        patch.object(app, "ACTIVE_TASKS", set()),
                        patch.object(app.Handler, "log_message"),
                        patch.object(app, "SOURCE_DIRECTORIES", {"raw": self.raw, "uploads": self.uploads})]
        for patched in self.patches:
            patched.start()
        self.job = "historytest"
        self.directory = self.jobs / self.job
        self.directory.mkdir()
        self.source = self.raw / "source.jpg"
        image = app.np.zeros((32, 64, 3), dtype=app.np.uint8)
        app.cv2.imwrite(str(self.source), image)
        app.save_job_manifest(self.job, {"label": "History check", "source_name": self.source.name,
            "source_key": "data/raw:" + self.source.name, "settings": app.DEFAULT_VIEW_SETTINGS})
        self.matrix = app.np.eye(4).tolist()
        transforms = {"w": 32, "h": 32, "fl_y": 16, "source_mode": "walkthrough", "frames": []}
        for index in range(4):
            name = f"frame_{index:06}_view_00.jpg"
            app.cv2.imwrite(str(self.directory / name), image)
            transforms["frames"].append({"file_path": name, "frame_index": index, "transform_matrix": self.matrix})
        app.write_json(self.directory / "transforms.json", transforms)
        app.write_json(self.directory / "brush_data" / "transforms.json", transforms)
        (self.directory / "brush_data" / "sparse_pc.ply").write_bytes(b"ply\n")
        self.preview = {"input_signature": app.pose_input_signature(self.directory),
                        "views": transforms["frames"], "quality": {}, "summary": {}, "warnings": []}
        app.write_json(self.directory / "pose_preview.json", self.preview)
        app.write_json(self.directory / "pose_status.json", {"state": "ready"})

    def tearDown(self):
        for patched in reversed(self.patches):
            patched.stop()
        self.temporary.cleanup()

    def fake_train(self, command, **kwargs):
        self.assertIn("--export-path", command)
        export = Path(command[command.index("--export-path") + 1])
        names = ["x", "y", "z", "opacity", "scale_0", "scale_1", "scale_2"]
        values = app.np.zeros(2000, dtype=[(name, "<f4") for name in names])
        positions = app.np.random.default_rng(42).uniform(-1, 1, (2000, 3))
        for index, name in enumerate(names[:3]):
            values[name] = positions[:, index]
        for name in names[4:]:
            values[name] = -5
        header = "ply\nformat binary_little_endian 1.0\nelement vertex 2000\n"
        header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
        (export / "splat.ply").write_bytes(header.encode() + values.tobytes())

    def train(self):
        with patch.object(app.subprocess, "run", side_effect=self.fake_train):
            app.generate_3d(self.job)
        status = app.model_status(self.job)
        self.assertEqual(status["state"], "done", status.get("error"))
        return status

    def test_timer_and_bangkok_display(self):
        timer = TaskTimer()
        with patch("task_history.time.monotonic", return_value=timer.start_tick + 65.25):
            finished = timer.snapshot(finished=True)
        self.assertEqual(finished["elapsed_seconds"], 65.25)
        self.assertIsNotNone(finished["finished_at"])
        self.assertEqual(timer.snapshot(), finished)
        self.assertEqual(format_timestamp("2026-10-07T16:30:00+00:00"), "07/10/2026 23:30:00")
        self.assertEqual(format_duration(3661), "1 ชม. 1 นาที 1 วินาที")

    def test_refinement_preserves_checkpoint_inputs_and_view_identity(self):
        parent = self.train()
        parent_file = self.models / parent['model']
        original_bytes = parent_file.read_bytes()
        parent_dataset = self.directory / 'model_runs' / parent['run_id'] / 'dataset'
        original_json = (parent_dataset / 'transforms.json').read_bytes()
        # Current job may now point to a different Views version.
        parent_meta = app.read_json(app.model_metadata_path(parent['model']))
        parent_meta['view_run_id'] = 'old-view-version'
        app.write_json(app.model_metadata_path(parent['model']), parent_meta)
        app.write_json(self.directory / 'views_status.json', {'run_id': 'new-view-version'})
        identifier = app.allocate_model_run(self.job)
        with patch.object(app.subprocess, 'run', side_effect=self.fake_train), patch.object(
            app, 'evaluate_brush_renders', return_value={'eval_views': 3, 'eval_psnr_mean_db': 33}):
            app.refine_model(parent['model'], identifier)
        result = app.read_json(self.directory / 'model_runs' / identifier / 'status.json')
        self.assertEqual(result['state'], 'done', result.get('error'))
        self.assertNotEqual(result['model'], parent['model'])
        self.assertEqual(result['view_run_id'], 'old-view-version')
        self.assertEqual(result['refined_from'], parent['model'])
        self.assertEqual(parent_file.read_bytes(), original_bytes)
        self.assertEqual((parent_dataset / 'transforms.json').read_bytes(), original_json)
        new_data = app.read_json(self.directory / 'model_runs' / identifier / 'dataset' / 'transforms.json')
        self.assertEqual(new_data['ply_file_path'], 'refinement_init.ply')
        self.assertEqual(result['quality']['cumulative_training_steps'], 30000)

    def test_refinement_rejects_missing_snapshot_and_unsafe_filename(self):
        with self.assertRaises(ValueError):
            app.refinement_input('../x.ply')
        parent = self.train()
        dataset = self.directory / 'model_runs' / parent['run_id'] / 'dataset'
        (dataset / 'transforms.json').unlink()
        with self.assertRaises(ValueError):
            app.refinement_input(parent['model'])

    def test_refinement_budget_adapts_to_view_count(self):
        dataset = self.directory / 'brush_data'
        small = app.refinement_plan({}, dataset)
        self.assertEqual(small['steps'], 10000)
        meta = app.read_json(dataset / 'transforms.json')
        meta['frames'] = [{} for _ in range(2400)]
        app.write_json(dataset / 'transforms.json', meta)
        self.assertEqual(app.refinement_plan({}, dataset)['steps'], 20000)

    def test_missing_refinement_checkpoint_releases_the_claimed_task(self):
        parent = self.train()
        identifier = app.allocate_model_run(self.job)
        self.assertTrue(app.claim_task(self.job))
        checkpoint = self.directory / 'model_runs' / parent['run_id'] / 'brush_export' / 'splat.ply'
        checkpoint.unlink()
        app.refine_model(parent['model'], identifier, self.job)
        self.assertFalse(app.task_is_active(self.job))
        result = app.read_json(self.directory / 'model_runs' / identifier / 'status.json')
        self.assertEqual(result['state'], 'error')
        self.assertIsNotNone(result['finished_at'])

    def test_export_validation_preserves_distant_and_thin_geometry(self):
        from plyfile import PlyData, PlyElement
        names = ['x','y','z','opacity','scale_0','scale_1','scale_2']
        values = app.np.zeros(2000,dtype=[(n,'<f4') for n in names])
        values['x'] = app.np.linspace(-1,1,len(values))
        values['scale_0'] = values['scale_1'] = -5
        values['scale_2'] = 0
        values['x'][-1] = 10000
        values['opacity'][0] = app.np.nan
        values['scale_0'][1] = 100
        source,destination = self.root/'raw.ply',self.root/'clean.ply'
        PlyData([PlyElement.describe(values,'vertex')],text=False,byte_order='<').write(source)
        quality = app.sanitize_splat_ply(source,destination)
        clean = PlyData.read(destination)['vertex'].data
        self.assertEqual(len(clean),1998)
        self.assertEqual(clean['x'][-1],10000)
        self.assertTrue(app.np.array_equal(clean,values[2:]))
        self.assertEqual(quality['removed_outlier_splats'],2)
        self.assertIn('learned geometry preserved',quality['cleanup_policy'])

    def test_export_repair_keeps_previous_version_and_inputs(self):
        from scripts.repair_model_export import repair
        parent = self.train()
        old_file = self.models/parent['model']
        previous = old_file.read_bytes()
        dataset = self.directory/'model_runs'/parent['run_id']/'dataset'
        original_json = (dataset/'transforms.json').read_bytes()
        result = repair(parent['model'])
        self.assertNotEqual(result['model'],parent['model'])
        self.assertEqual(result['operation'],'validated_reexport')
        self.assertEqual(result['view_run_id'],parent['view_run_id'])
        self.assertEqual(old_file.read_bytes(),previous)
        self.assertEqual((dataset/'transforms.json').read_bytes(),original_json)
        self.assertEqual((self.models/result['model']).read_bytes(),previous)
        self.assertFalse(app.task_is_active(self.job))

    def test_eval_reads_exact_png_input_from_transforms(self):
        import cv2
        dataset = self.directory / 'exact_png'
        export = self.directory / 'eval_export' / 'eval_10'
        export.mkdir(parents=True)
        (dataset / 'images').mkdir(parents=True)
        image = app.np.random.default_rng(2).integers(0, 255, (32, 32, 3), dtype=app.np.uint8)
        cv2.imwrite(str(dataset / 'images' / 'frame.png'), image)
        cv2.imwrite(str(export / 'frame.png'), image)
        cv2.imwrite(str(dataset / 'images' / 'frame.jpg'), app.np.zeros_like(image))
        app.write_json(dataset / 'transforms.json', {'frames': [{'file_path': 'images/frame.png'}]})
        result = app.evaluate_brush_renders(dataset, export.parent, 10)
        self.assertEqual(result['eval_views'], 1)
        self.assertEqual(result['eval_psnr_mean_db'], 99)

    def test_trainer_jpeg_eof_recovers_once_without_changing_source(self):
        import subprocess
        brush_data = self.directory / 'brush_data'
        meta = app.read_json(brush_data / 'transforms.json')
        (brush_data / 'images').mkdir()
        for frame in meta['frames']:
            name = Path(frame['file_path']).name
            (brush_data / 'images' / name).write_bytes((self.directory / name).read_bytes())
            frame['file_path'] = 'images/' + name
        app.write_json(brush_data / 'transforms.json', meta)
        original = self.source.read_bytes()
        calls = []
        def train(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                kwargs['stdout'].write('Error: Failed to load format.\nIO error while loading dataset.\nearly eof\n')
                raise subprocess.CalledProcessError(1,command)
            dataset = Path(command[1])
            self.assertTrue(all(frame['file_path'].endswith('.png') for frame in app.read_json(dataset/'transforms.json')['frames']))
            return self.fake_train(command, **kwargs)
        with patch.object(app.subprocess,'run',side_effect=train):
            app.generate_3d(self.job)
        self.assertEqual(len(calls),2)
        status = app.model_status(self.job)
        self.assertEqual(status['state'],'done',status.get('error'))
        self.assertEqual(status['quality']['reencoded_training_images'],4)
        self.assertEqual(self.source.read_bytes(),original)

    def test_other_trainer_failures_are_not_retried_as_jpeg_errors(self):
        import subprocess
        with patch.object(app.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['brush'])) as run:
            app.generate_3d(self.job)
        self.assertEqual(run.call_count,1)
        self.assertEqual(app.model_status(self.job)['state'],'error')
        self.assertNotIn('image_codec_recovery',app.model_status(self.job).get('quality',{}))

    def test_repeated_training_preserves_versions_and_camera(self):
        first = self.train()
        original = (self.models / first["model"]).read_bytes()
        second = self.train()
        self.assertNotEqual(first["model"], second["model"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual((self.models / first["model"]).read_bytes(), original)
        self.assertEqual(len(app.model_files_for_job(self.job)), 2)
        self.assertEqual(len(app.model_runs(self.job)), 2)
        self.assertEqual(app.model_file_for_job(self.job).name, second["model"])
        for status in (first, second):
            self.assertIsNotNone(status["finished_at"])
            self.assertGreaterEqual(status["elapsed_seconds"], 0)
            directory = self.directory / "model_runs" / status["run_id"]
            self.assertTrue((directory / "3d.log").is_file())
            self.assertTrue((directory / "brush_export" / "splat.ply").is_file())
        app.cleanup_job_derivatives(self.job)
        self.assertEqual(len(app.model_files_for_job(self.job)), 2)
        self.assertEqual(app.model_viewer_config(first["model"])["transform_matrix"], self.matrix)
        self.assertEqual(len(app.model_runs(self.job)), 2)

    def test_failure_keeps_previous_model_and_records_end_time(self):
        first = self.train()
        with patch.object(app.subprocess, "run", side_effect=RuntimeError("simulated trainer failure")):
            app.generate_3d(self.job)
        failed = app.model_status(self.job)
        self.assertEqual(failed["state"], "error")
        self.assertIsNotNone(failed["finished_at"])
        self.assertEqual(len(app.model_files_for_job(self.job)), 1)
        self.assertEqual(app.model_file_for_job(self.job).name, first["model"])
        self.assertEqual(len(app.model_runs(self.job)), 2)

    def test_rename_delete_isolated_version(self):
        first, second = self.train(), self.train()
        app.rename_model_file(first["model"], "renamed.ply")
        self.assertFalse(app.model_metadata_path(first["model"]).exists())
        self.assertEqual(app.model_viewer_config("renamed.ply")["transform_matrix"], self.matrix)
        first_status = app.read_json(self.directory / "model_runs" / first["run_id"] / "status.json")
        self.assertEqual(first_status["model"], "renamed.ply")
        app.delete_model_file("renamed.ply")
        self.assertTrue((self.models / second["model"]).is_file())
        self.assertFalse(app.model_metadata_path("renamed.ply").exists())
        self.assertEqual(app.model_status(self.job)["state"], "done")
        deleted = app.read_json(self.directory / "model_runs" / first["run_id"] / "status.json")
        self.assertEqual(deleted["state"], "deleted")
        self.assertEqual(deleted["finished_at"], first["finished_at"])

    def test_actual_view_conversion_timing_and_reprocess(self):
        first = self.train()
        settings = {**app.DEFAULT_VIEW_SETTINGS, "width": 160, "height": 160, "views": 2, "quality": "preview"}
        app.generate_views(self.job, "data/raw:" + self.source.name, settings, reprocess=True)
        status = app.views_status(self.job)
        self.assertEqual(status["state"], "ready", status.get("error"))
        self.assertEqual(status["frames"], 2)
        self.assertIsNotNone(status["finished_at"])
        self.assertEqual(len(list(self.directory.glob("view_runs/*/status.json"))), 1)
        self.assertTrue((self.models / first["model"]).is_file())
        self.assertEqual(app.model_viewer_config(first["model"])["transform_matrix"], self.matrix)
        app.generate_views(self.job, "data/raw:missing.jpg", settings)
        self.assertEqual(app.views_status(self.job)["state"], "error")
        self.assertIsNotNone(app.views_status(self.job)["finished_at"])

    def test_legacy_snapshot_survives_reprocess(self):
        legacy = self.models / f"{self.job}.ply"
        legacy.write_bytes(b"old model")
        app.write_json(self.directory / "3d_status.json", {"state": "done", "model": legacy.name})
        app.cleanup_job_derivatives(self.job)
        self.assertTrue(legacy.is_file())
        metadata = app.model_info(legacy)
        self.assertIn("recorded_at", metadata)
        self.assertNotIn("elapsed_seconds", metadata)
        self.assertEqual(app.model_viewer_config(legacy.name)["transform_matrix"], self.matrix)

    def test_busy_job_rejects_duplicate_start(self):
        self.assertTrue(app.claim_task(self.job))
        self.assertFalse(app.start_3d(self.job))
        self.assertFalse((self.directory / "model_runs").exists())
        app.release_task(self.job)

    def test_job_paths_stay_inside_workspace(self):
        for name in ("..", ".", "../models", "..\\models", str(self.root)):
            self.assertIsNone(app.job_directory(name))

    def test_shared_job_lock_across_server_instances(self):
        self.assertTrue(app.claim_task(self.job))
        with patch.object(app, "ACTIVE_TASKS", set()):
            self.assertTrue(app.task_is_active(self.job))
            self.assertFalse(app.claim_task(self.job))
        app.release_task(self.job)
        self.assertFalse((self.directory / ".task.lock").exists())

    def test_stale_process_lock_can_be_recovered(self):
        app.write_json(self.directory / ".task.lock", {"pid": 4294967295, "token": "old"})
        self.assertTrue(app.claim_task(self.job))
        app.release_task(self.job)

    def test_live_training_iteration_from_current_run_log(self):
        identifier = app.allocate_model_run(self.job)
        (self.directory / "model_runs" / identifier / "3d.log").write_text(
            "Refine iter 201, 1000 splats.\nRefine iter 5001, 24000 splats.\n", encoding="utf-8")
        status = {"state": "running", "stage": "model", "run_id": identifier,
                  "training_steps": 10000, "training_views": 160, "progress": 40}
        live = app.live_training_status(self.job, status)
        self.assertEqual(live["iteration"], 5001)
        self.assertEqual(live["splats"], 24000)
        self.assertEqual(live["progress"], 67)
        self.assertEqual(app.live_training_status(self.job, {**status, "state": "done"})["state"], "done")

    def test_atomic_status_updates_while_polling(self):
        path = self.directory / "concurrent_status.json"
        app.write_json(path, {"sequence": 0})
        stop = threading.Event()
        errors = []
        def poll():
            while not stop.is_set():
                status = app.read_json(path)
                if "sequence" not in status:
                    errors.append(status)
        thread = threading.Thread(target=poll)
        thread.start()
        try:
            for sequence in range(100):
                app.write_json(path, {"sequence": sequence})
        finally:
            stop.set(); thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(app.read_json(path)["sequence"], 99)

    def test_http_repeat_generation_history_and_cascade_delete(self):
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        def request(path, data=None):
            response = urlopen(base + path, data=urlencode(data).encode() if data else None)
            return response.read().decode("utf-8")
        try:
            with patch.object(app.subprocess, "run", side_effect=self.fake_train):
                for _ in range(2):
                    page = request("/actions", {"action": "generate3d", "job": self.job, "return_to": "/job/" + self.job})
                    self.assertIn("Models ทั้งหมดของงานนี้", page)
                    deadline = time.monotonic() + 10
                    while app.task_is_active(self.job) and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertFalse(app.task_is_active(self.job))
                    self.assertEqual(app.model_status(self.job)["state"], "done")
            runs = app.model_runs(self.job)
            self.assertEqual(len(runs), 2)
            payload = json.loads(request("/api/run/" + self.job + "/" + runs[0]["run_id"]))
            self.assertEqual(payload["model"], runs[0]["model"])
            job = json.loads(request("/api/job/" + self.job))
            self.assertIn("views", job)
            self.assertFalse(job["busy"])
            page = request("/job/" + self.job)
            self.assertIn("สร้าง Model ใหม่", page)
            self.assertIn(runs[0]["model"], page)
            self.assertIn(runs[1]["model"], page)
            request("/actions", {"action": "delete_job", "job": self.job, "delete_model": "1", "return_to": "/jobs"})
            self.assertFalse(self.directory.exists())
            self.assertEqual(list(self.models.glob("*.ply")), [])
            self.assertEqual(list(self.models.glob("*.meta.json")), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_selected_positions_are_passed_to_trainer(self):
        self.preview["cameras"]=[{"frame_index":index,"position":[index*.2,0,0],"views":[index]}
                                 for index in range(4)]
        app.write_json(self.directory / "pose_preview.json", self.preview)
        server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            data=urlencode({"action":"generate3d","job":self.job,"return_to":"/job/"+self.job,
                "selected_frames":"[0,1,2]","selection_signature":self.preview["input_signature"]}).encode()
            with patch.object(app,"start_3d",return_value=True) as trainer:
                with urlopen(f"http://127.0.0.1:{server.server_port}/actions",data=data) as response:
                    self.assertEqual(response.status,200)
                self.assertEqual(trainer.call_args.kwargs["selected_frames"],[0,1,2])
                self.assertEqual(trainer.call_args.kwargs["selection_signature"],self.preview["input_signature"])
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_source_cascade_removes_all_versions(self):
        self.train(); self.train()
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            data = urlencode({"action": "delete_source", "source": "data/raw:" + self.source.name,
                              "cascade": "1", "return_to": "/sources"}).encode()
            with urlopen(f"http://127.0.0.1:{server.server_port}/actions", data=data) as response:
                self.assertEqual(response.status, 200)
            self.assertFalse(self.source.exists())
            self.assertFalse(self.directory.exists())
            self.assertEqual(list(self.models.iterdir()), [])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
