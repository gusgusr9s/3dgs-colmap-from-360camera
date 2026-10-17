"""Frozen CPU contracts from before CUDA support; no GPU or downloads needed."""
from contextlib import ExitStack
import hashlib
import io
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compute_runtime as runtime
import spherical_to_brush as spherical
import web_app as app
import torch
import torchvision.models.segmentation as segmentation


def without_thread_budget(command):
    # Quality/camera commands stay frozen; thread budgets are intentionally configurable.
    result=list(command)
    for flag in ('--FeatureExtraction.num_threads','--FeatureMatching.num_threads',
                 '--SiftExtraction.num_threads','--SiftMatching.num_threads','--Mapper.num_threads'):
        if flag in result:
            index=result.index(flag)
            assert int(result[index+1])>0
            del result[index:index+2]
    # Durable snapshots add observability/recovery without changing reconstruction options.
    for flag in ('--Mapper.snapshot_path','--Mapper.snapshot_frames_freq'):
        if flag in result:
            index=result.index(flag);del result[index:index+2]
    return result


class DeterministicPersonModel(torch.nn.Module):
    """Fixed segmentation logits isolate the pre-existing mask processing."""
    def forward(self, inputs):
        logits = torch.zeros((len(inputs), 21, *inputs.shape[2:]), device=inputs.device)
        person = (inputs[:, 0] > .5) & (inputs[:, 1] > .5)
        logits[:, 0] = (~person).float() * 20
        logits[:, 15] = person.float() * 20
        return {"out": logits}


def mask_weights():
    def transform(image):
        pixels = app.np.asarray(image).copy()
        return torch.from_numpy(pixels).permute(2, 0, 1).float() / 255
    return SimpleNamespace(DEFAULT=SimpleNamespace(transforms=lambda: transform))


def make_mask_fixture(directory):
    (directory / "images").mkdir()
    for name, person in (("empty", False), ("person", True)):
        pixels = app.np.zeros((96, 96, 3), dtype=app.np.uint8)
        if person:
            pixels[50:86, 52:77] = (0, 255, 255)  # BGR -> yellow in RGB
        app.cv2.imwrite(str(directory / "images" / (name + ".png")), pixels)
    app.write_json(directory / "transforms.json", dict(w=96, h=96, fl_x=48, frames=[
        dict(file_path="images/empty.png", pitch_deg=-40),
        dict(file_path="images/person.png", pitch_deg=0),
    ]))


class CpuPipelineRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cpu_regression_", dir=app.ROOT / "web_data")
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to((app.ROOT / "web_data").resolve())

    def tearDown(self):
        self.temporary.cleanup()

    def test_perspective_cpu_preserves_entire_legacy_command_sequence(self):
        for version, environment in (("3.12.4", {}), ("4.2.1", {}), ("4.2.1", {"COLMAP_DEVICE": "cpu"})):
            with self.subTest(version=version, environment=environment):
                processed = self.root / (version + ("-explicit" if environment else "-auto"))
                database = str(processed / "colmap/database.db")
                sparse = processed / "colmap/sparse"
                inputs = str(self.root / "images")
                commands = []
                def run(command, **kwargs):
                    commands.append(command)
                    if command[1] == "-h":
                        return SimpleNamespace(stdout=f"COLMAP {version} (without GPU support)", stderr="")
                    if command[1] == "mapper":
                        (sparse / "0").mkdir()
                        (sparse / "0/images.bin").write_bytes(struct.pack("<Q", 18))
                    return SimpleNamespace(returncode=0)
                quality = {}
                with patch.object(app.subprocess, "run", side_effect=run), patch.object(runtime, "nvidia_devices", side_effect=AssertionError("CPU path queried NVIDIA")):
                    count = app.run_colmap_with_known_camera(Path("colmap"), Path("ns-process-data"), Path(inputs), processed,
                        dict(fl_x=100, fl_y=100, cx=50, cy=50), "exhaustive", io.StringIO(), environment, lambda *a, **k: None, quality)
                modern = version == "4.2.1"
                log = ["--log_target", "stdout"] if modern else []
                extraction = "--FeatureExtraction.use_gpu" if modern else "--SiftExtraction.use_gpu"
                matching = "--FeatureMatching.use_gpu" if modern else "--SiftMatching.use_gpu"
                # Frozen baseline commands, including intrinsics, mapper settings and conversion.
                self.assertEqual([without_thread_budget(command) for command in commands], [
                    ["colmap", "-h"],
                    ["colmap", "feature_extractor", "--database_path", database, "--image_path", inputs,
                     "--ImageReader.camera_model", "PINHOLE", "--ImageReader.single_camera", "1",
                     "--ImageReader.camera_params", "100,100,50,50", extraction, "0", *log],
                    ["colmap", "exhaustive_matcher", "--database_path", database, matching, "0", *log],
                    ["colmap", "mapper", "--database_path", database, "--image_path", inputs, "--output_path", str(sparse),
                     "--Mapper.ba_refine_focal_length", "0", "--Mapper.ba_refine_principal_point", "0",
                     "--Mapper.ba_refine_extra_params", "0", "--Mapper.min_model_size", "3", *log],
                    ["ns-process-data", "images", "--data", inputs, "--output-dir", str(processed),
                     "--skip-colmap", "--colmap-model-path", str(Path("colmap/sparse/0")),
                     "--camera-type", "pinhole", "--no-gpu"],
                ])
                self.assertEqual(count, 18)
                self.assertEqual(quality["colmap_backend"], "CPU")

    def test_spherical_cpu_keeps_native_camera_masks_and_matcher_retry(self):
        for panorama_count in (6, 60):
            with self.subTest(panorama_count=panorama_count):
                work = self.root / str(panorama_count)
                sparse = work / "sparse"
                commands = []
                mapper_calls = 0
                def extract(source, metadata, output):
                    output.mkdir(parents=True)
                    app.cv2.imwrite(str(output / "pano_000000.jpg"), app.np.zeros((100, 200, 3), app.np.uint8))
                    return panorama_count
                def run(command, **kwargs):
                    nonlocal mapper_calls
                    commands.append(command)
                    if command[1] == "-h":
                        return SimpleNamespace(stdout="COLMAP 4.2.1 (without GPU support)", stderr="")
                    if command[1] == "mapper":
                        mapper_calls += 1
                        if panorama_count == 60 and mapper_calls == 1:
                            raise app.subprocess.CalledProcessError(1, command)
                        (sparse / "0").mkdir()
                        (sparse / "0/images.bin").write_bytes(struct.pack("<Q", panorama_count))
                    return SimpleNamespace(returncode=0)
                original_quality = dict(perspective_views=panorama_count * 8, max_same_frame_center_spread=0,
                    sparse_points=1500, path_to_scene_ratio=.2, mean_reprojection_error=1)
                with patch.object(app.subprocess, "run", side_effect=run), patch.object(app, "extract_spherical_keyframes", side_effect=extract), patch.object(app, "build_brush_dataset", return_value=dict(original_quality)), patch.object(app.os, "cpu_count", return_value=16), patch.object(runtime, "nvidia_devices", side_effect=AssertionError("CPU path queried NVIDIA")):
                    result = app.run_spherical_colmap(Path("colmap"), self.root, self.root, work, self.root / "dataset",
                        {}, io.StringIO(), {}, lambda *a, **k: None)
                database = str(work / "database.db")
                log = ["--log_target", "stdout"]
                mapper = ["colmap", "mapper", "--database_path", database, "--image_path", str(work / "panoramas"),
                    "--output_path", str(sparse), "--Mapper.min_model_size", "3", *log]
                matcher = ["colmap", "sequential_matcher" if panorama_count == 60 else "exhaustive_matcher",
                    "--database_path", database, "--FeatureMatching.use_gpu", "0"]
                if panorama_count == 60:
                    matcher += ["--SequentialMatching.overlap", "10"]
                expected = [
                    ["colmap", "-h"],
                    ["colmap", "feature_extractor", "--database_path", database, "--image_path", str(work / "panoramas"),
                     "--ImageReader.camera_model", "EQUIRECTANGULAR", "--ImageReader.single_camera", "1",
                     "--ImageReader.camera_mask_path", str(work / "camera_mask.png"), "--FeatureExtraction.use_gpu", "0",
                     "--FeatureExtraction.num_threads", "8", "--SiftExtraction.max_num_features", "12000", *log],
                    [*matcher, *log], mapper,
                ]
                if panorama_count == 60:
                    expected += [["colmap", "exhaustive_matcher", "--database_path", database, "--FeatureMatching.use_gpu", "0", *log], mapper]
                self.assertEqual([without_thread_budget(command) for command in commands],
                                 [without_thread_budget(command) for command in expected])
                for command in commands:
                    for flag in ('--FeatureExtraction.num_threads','--FeatureMatching.num_threads','--Mapper.num_threads'):
                        if flag in command:self.assertEqual(command[command.index(flag)+1],'16')
                mask = app.cv2.imread(str(work / "camera_mask.png"), app.cv2.IMREAD_GRAYSCALE)
                self.assertTrue(app.np.all(mask[:72] == 255))
                self.assertTrue(app.np.all(mask[72:] == 0))
                self.assertEqual({key: result[key] for key in original_quality}, original_quality)
                self.assertEqual(result["colmap_backend"], "CPU")

    def test_cpu_person_mask_pixels_and_statistics_match_pre_cuda_baseline(self):
        make_mask_fixture(self.root)
        originals = {file.name: file.read_bytes() for file in (self.root / "images").iterdir()}
        for mode in ("cpu", "auto"):
            with self.subTest(mode=mode), patch.dict(runtime.os.environ, {"MASK_DEVICE": mode}), patch.object(torch.cuda, "is_available", return_value=False), patch.object(segmentation, "LRASPP_MobileNet_V3_Large_Weights", mask_weights()), patch.object(segmentation, "lraspp_mobilenet_v3_large", return_value=DeterministicPersonModel()):
                # Isolate the frozen CPU contract from installed GPU providers and subprocesses.
                with patch('mask_compute.worker_masks', return_value=None), patch('mask_compute.importlib.util.find_spec', return_value=None):
                    result = spherical.create_dynamic_person_masks(self.root)
                hashes = {name: hashlib.sha256(app.cv2.imread(str(self.root / "masks" / (name + ".png")), app.cv2.IMREAD_GRAYSCALE).tobytes()).hexdigest() for name in ("empty", "person")}
                self.assertEqual(hashes, MASK_PIXEL_BASELINE)
                self.assertEqual({key: result[key] for key in MASK_STAT_BASELINE}, MASK_STAT_BASELINE)
                self.assertEqual(result["mask_device"], "cpu")
                self.assertEqual({file.name: file.read_bytes() for file in (self.root / "images").iterdir()}, originals)

    def test_existing_pose_dataset_still_trains_brush_without_cuda(self):
        jobs, models = self.root / "jobs", self.root / "models"
        models.mkdir()
        job = jobs / "legacy"
        dataset = job / "brush_data"
        (dataset / "images").mkdir(parents=True)
        frames = []
        for index in range(6):
            name = f"frame_{index:06}.jpg"
            app.cv2.imwrite(str(job / name), app.np.zeros((32, 32, 3), app.np.uint8))
            shutil.copy2(job / name, dataset / "images" / name)
            frames.append(dict(file_path="images/" + name, frame_index=index))
        app.write_json(job / "job.json", dict(settings=dict(quality="preview"), source_mode="perspective"))
        app.write_json(job / "transforms.json", dict(frames=frames, settings=dict(quality="preview")))
        app.write_json(dataset / "transforms.json", dict(frames=frames))
        (dataset / "sparse_pc.ply").write_bytes(b"legacy sparse seed")
        app.write_json(job / "pose_status.json", dict(state="ready"))
        app.write_json(job / "pose_preview.json", dict(input_signature=app.pose_input_signature(job),
            views=frames, quality=dict(registered_views=3), summary={}, warnings=[]))
        brush = self.root / "brush_app"
        brush.touch()
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            (Path(command[command.index("--export-path") + 1]) / "splat.ply").write_bytes(b"brush export")
            kwargs["stdout"].write('Using adapter AdapterInfo { name: "AMD Radeon RX 6700 XT", device_type: DiscreteGpu, backend: Vulkan }\n')
        def sanitize(source, destination):
            shutil.copy2(source, destination)
            return dict(splats=100)
        with ExitStack() as stack:
            for name, value in dict(JOBS=jobs, MODELS=models, ACTIVE_TASKS=set()).items():
                stack.enter_context(patch.object(app, name, value))
            stack.enter_context(patch.object(app, "brush_cli", return_value=brush))
            stack.enter_context(patch.object(app, "colmap_bin_dir", return_value=None))
            stack.enter_context(patch.object(app, "select_colmap_compute", side_effect=AssertionError("Cached poses should bypass COLMAP")))
            stack.enter_context(patch.object(torch.cuda, "is_available", side_effect=AssertionError("Brush must not require Torch CUDA")))
            stack.enter_context(patch.object(app.subprocess, "run", side_effect=run))
            stack.enter_context(patch.object(app, "evaluate_brush_renders", return_value=dict(eval_views=3, eval_psnr_mean_db=25)))
            stack.enter_context(patch.object(app, "sanitize_splat_ply", side_effect=sanitize))
            stack.enter_context(patch.object(app, "dataset_viewer_config", return_value={}))
            compression = stack.enter_context(patch.object(app, "start_fast_variant", return_value="compressed"))
            app.generate_3d("legacy", identifier="regression")
        status = app.read_json(job / "3d_status.json")
        self.assertEqual(status["state"], "done", status)
        self.assertEqual(status["quality"]["compute_device"], "AMD Radeon RX 6700 XT")
        self.assertEqual(status["quality"]["compute_backend"], "Vulkan")
        export = job / "model_runs/regression/brush_export"
        self.assertEqual(commands, [[str(brush), str(job / "model_runs/regression/dataset/training_input"),
            "--total-steps", "10000", "--max-resolution", "960", "--max-splats", "1000000",
            "--growth-stop-iter", "7500", "--eval-split-every", "17", "--eval-every", "10000",
            "--eval-save-to-disk", "--export-every", "10000", "--export-path", str(export), "--export-name", "splat.ply"]])
        self.assertEqual((models / "legacy_regression.ply").read_bytes(), b"brush export")
        compression.assert_called_once_with(models / "legacy_regression.ply")
        self.assertEqual((dataset / "sparse_pc.ply").read_bytes(), b"legacy sparse seed")


# Recorded from commit f99038b's CPU mask function using the deterministic logits above.
# Compare decoded pixels rather than PNG encoding, which varies by codec version.
MASK_PIXEL_BASELINE = {
    "empty": "a27a50c72e45c4f5a44249d2913d9c56b43815d800757303c49dd643c5f40818",
    "person": "3127f98d05765fad1059f8e7ebabcaec8c1cabf640a1a51022578495c5181eb8",
}
MASK_STAT_BASELINE = {
    "masked_images": 1,
    "mean_ignored_pixels_percent": 18.522,
    "mean_nadir_mask_percent": 1.91,
    "mask_model": "LRASPP MobileNetV3 person segmentation",
}


if __name__ == "__main__":
    unittest.main()
