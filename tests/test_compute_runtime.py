"""Device selection and CPU/CUDA command dispatch for both input pipelines."""
import io
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compute_runtime as runtime
import web_app as app
import torch

CPU_BANNER = "COLMAP 4.2.1 (Commit abc without GPU support)"
CUDA_BANNER = "COLMAP 4.2.1 (Commit abc with CUDA, std hash maps)"


class DeviceSelectionTests(unittest.TestCase):
    def test_auto_cpu_does_not_depend_on_torch_or_query_driver_for_cpu_build(self):
        with patch.object(runtime, "nvidia_devices") as driver:
            compute = runtime.select_colmap_compute(CPU_BANNER, {})
            self.assertEqual(compute.backend, "CPU")
            self.assertEqual(compute.options("matching"), ["--FeatureMatching.use_gpu", "0"])
            driver.assert_not_called()

    def test_auto_cuda_and_gpu_index(self):
        with patch.object(runtime, "nvidia_devices", return_value=["NVIDIA RTX 4090"]):
            compute = runtime.select_colmap_compute(CUDA_BANNER, {"COLMAP_GPU_INDEX": "0"})
        self.assertEqual(compute.backend, "CUDA")
        self.assertEqual(compute.options("extraction"), ["--FeatureExtraction.use_gpu", "1", "--FeatureExtraction.gpu_index", "0"])

    def test_auto_missing_driver_falls_back_but_forced_cuda_fails(self):
        with patch.object(runtime, "nvidia_devices", return_value=[]):
            self.assertEqual(runtime.select_colmap_compute(CUDA_BANNER, {}).backend, "CPU")
            with self.assertRaisesRegex(RuntimeError, "NVIDIA"):
                runtime.select_colmap_compute(CUDA_BANNER, {"COLMAP_DEVICE": "cuda"})
        with self.assertRaisesRegex(RuntimeError, "not built with CUDA"):
            runtime.select_colmap_compute(CPU_BANNER, {"COLMAP_DEVICE": "cuda"})

    def test_explicit_cpu_and_legacy_flags(self):
        with patch.object(runtime, "nvidia_devices") as driver:
            compute = runtime.select_colmap_compute("COLMAP 3.12.4 with CUDA", {"COLMAP_DEVICE": "cpu"})
            driver.assert_not_called()
        self.assertEqual(compute.options("extraction"), ["--SiftExtraction.use_gpu", "0"])
        self.assertEqual(compute.options("matching"), ["--SiftMatching.use_gpu", "0"])
        self.assertTrue(runtime.select_colmap_compute("COLMAP 3.13.0", {"COLMAP_DEVICE": "cpu"}).modern)

    def test_invalid_configuration(self):
        for environment in ({"COLMAP_DEVICE": "gpu"}, {"COLMAP_GPU_INDEX": "-2"}, {"COLMAP_GPU_INDEX": "0,1"}):
            with self.assertRaises(ValueError):
                runtime.select_colmap_compute(CPU_BANNER, environment)
        self.assertEqual(runtime.colmap_version("COLMAP 4.1"), (4, 1, 0))
        self.assertEqual(runtime.colmap_version("bad banner"), (0, 0, 0))

    def test_mask_selection_is_independent_of_colmap(self):
        with patch.object(torch.cuda, "is_available", return_value=False):
            self.assertEqual(str(runtime.select_mask_device(torch, "auto")), "cpu")
            with self.assertRaisesRegex(RuntimeError, "PyTorch CUDA"):
                runtime.select_mask_device(torch, "cuda")
        with patch.object(torch.cuda, "is_available", return_value=True), patch.object(torch.cuda, "device_count", return_value=2):
            self.assertEqual(str(runtime.select_mask_device(torch, "auto")), "cuda")
            self.assertEqual(str(runtime.select_mask_device(torch, "cuda:1")), "cuda:1")
            self.assertEqual(str(runtime.select_mask_device(torch, "cpu")), "cpu")
            with self.assertRaises(RuntimeError):
                runtime.select_mask_device(torch, "cuda:2")
        with self.assertRaises(ValueError):
            runtime.select_mask_device(torch, "gpu")

    def test_nvidia_probe_timeout_and_nonzero_exit_are_unavailable(self):
        with patch.object(runtime.shutil, "which", return_value="nvidia-smi"), patch.object(runtime.subprocess, "run", side_effect=app.subprocess.TimeoutExpired("nvidia-smi", 10)):
            self.assertEqual(runtime.nvidia_devices({}), [])
        with patch.object(runtime.shutil, "which", return_value="nvidia-smi"), patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="driver error")):
            self.assertEqual(runtime.nvidia_devices({}), [])


class ColmapDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.commands = []
        self.steps = []
        self.log = io.StringIO()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, state, **kwargs):
        self.steps.append(kwargs.get("step", ""))

    def runner(self, command, **kwargs):
        self.commands.append(command)
        if command[1] == "-h":
            return SimpleNamespace(stdout=CUDA_BANNER, stderr="", returncode=0)
        if command[1] == "mapper":
            sparse = Path(command[command.index("--output_path") + 1]) / "0"
            sparse.mkdir(parents=True, exist_ok=True)
            (sparse / "images.bin").write_bytes(struct.pack("<Q", 6))
        return SimpleNamespace(returncode=0)

    def test_perspective_cuda_retains_fixed_intrinsics_and_records_backend(self):
        quality = {}
        with patch.object(app.subprocess, "run", side_effect=self.runner), patch.object(runtime, "nvidia_devices", return_value=["RTX"]):
            count = app.run_colmap_with_known_camera(Path("colmap"), Path("ns-process-data"), self.directory / "images", self.directory / "processed",
                dict(fl_x=100, fl_y=100, cx=50, cy=50), "exhaustive", self.log, {"COLMAP_DEVICE": "cuda"}, self.save, quality)
        self.assertEqual(count, 6)
        extraction = next(command for command in self.commands if command[1] == "feature_extractor")
        self.assertEqual(extraction[extraction.index("--FeatureExtraction.use_gpu") + 1], "1")
        mapper = next(command for command in self.commands if command[1] == "mapper")
        self.assertEqual(mapper[mapper.index("--Mapper.ba_refine_focal_length") + 1], "0")
        self.assertEqual(quality["colmap_backend"], "CUDA")
        self.assertIn("CUDA", self.log.getvalue())

    def test_spherical_cuda_includes_expanded_matcher(self):
        def extract(source, metadata, output):
            output.mkdir(parents=True)
            app.cv2.imwrite(str(output / "pano_000000.jpg"), app.np.zeros((32, 64, 3), app.np.uint8))
            return 60

        calls = 0
        def run(command, **kwargs):
            nonlocal calls
            result = self.runner(command, **kwargs)
            if command[1] == "mapper":
                calls += 1
                sparse = Path(command[command.index("--output_path") + 1]) / "0"
                (sparse / "images.bin").write_bytes(struct.pack("<Q", 6 if calls == 1 else 60))
            return result

        dataset_quality = dict(perspective_views=480, max_same_frame_center_spread=0,
                               sparse_points=1500, path_to_scene_ratio=.2, mean_reprojection_error=1)
        with patch.object(app.subprocess, "run", side_effect=run), patch.object(runtime, "nvidia_devices", return_value=["RTX"]), patch.object(app, "extract_spherical_keyframes", side_effect=extract), patch.object(app, "build_brush_dataset", return_value=dataset_quality):
            result = app.run_spherical_colmap(Path("colmap"), self.directory / "source.mp4", self.directory / "views", self.directory / "spherical", self.directory / "dataset", {}, self.log, {"COLMAP_DEVICE": "cuda"}, self.save)
        matchers = [command for command in self.commands if command[1].endswith("_matcher")]
        self.assertEqual([command[1] for command in matchers], ["sequential_matcher", "exhaustive_matcher"])
        for command in matchers:
            self.assertEqual(command[command.index("--FeatureMatching.use_gpu") + 1], "1")
        extraction = next(command for command in self.commands if command[1] == "feature_extractor")
        self.assertEqual(extraction[extraction.index("--ImageReader.camera_model") + 1], "EQUIRECTANGULAR")
        self.assertEqual(result["colmap_backend"], "CUDA")
        self.assertEqual(calls, 2)

    def test_spherical_4_0_rejected_before_work_directory_is_touched(self):
        work = self.directory / "spherical"
        work.mkdir()
        sentinel = work / "keep.txt"
        sentinel.write_text("keep")
        with patch.object(app.subprocess, "run", return_value=SimpleNamespace(stdout="COLMAP 4.0.0 with CUDA", stderr="")):
            with self.assertRaisesRegex(RuntimeError, "4.1"):
                app.run_spherical_colmap(Path("colmap"), self.directory, self.directory, work, self.directory, {}, self.log, {}, self.save)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_unavailable_cuda_preserves_existing_person_masks(self):
        (self.directory / "images").mkdir()
        (self.directory / "masks").mkdir()
        app.cv2.imwrite(str(self.directory / "images/frame.jpg"), app.np.zeros((32, 32, 3), app.np.uint8))
        app.write_json(self.directory / "transforms.json", dict(w=32, fl_x=16, frames=[]))
        sentinel = self.directory / "masks/frame.png"
        sentinel.write_bytes(b"existing mask")
        with patch.dict(runtime.os.environ, {"MASK_DEVICE": "cuda"}), patch.object(torch.cuda, "is_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "PyTorch CUDA"):
                app.create_dynamic_person_masks(self.directory)
        self.assertEqual(sentinel.read_bytes(), b"existing mask")

    def test_matching_progress_preserves_cuda_label(self):
        log = self.directory / "job/model_runs/run/3d.log"
        log.parent.mkdir(parents=True)
        log.write_text("Processing image [42/152]\n", encoding="utf-8")
        with patch.object(app, "JOBS", self.directory):
            status = app.live_training_status("job", dict(state="running", stage="model", run_id="run", progress=21, step="COLMAP: sequential matcher on CUDA"))
        self.assertIn("CUDA", status["step"])
        self.assertEqual(status["matching_image"], 42)


if __name__ == "__main__":
    unittest.main()
