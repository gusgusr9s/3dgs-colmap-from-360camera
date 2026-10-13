"""Check timing scope, exact run provenance and extrapolation boundaries."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_comparison import RUNS, build_report, measured_run, predict, render_report


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.jobs = Path(self.temp.name)

    def make_run(self, index, *, model_seconds=100, view_seconds=10, train_seconds=60,
                 positions=10, width=1024, steps=20000, fingerprint="scene-a"):
        job, run, label = RUNS[index]
        directory = self.jobs / job / "model_runs" / run
        directory.mkdir(parents=True)
        view_run = "20261013_120000_abcdef12"
        view_dir = self.jobs / job / "view_runs" / view_run
        view_dir.mkdir(parents=True)
        status = dict(state="done", elapsed_seconds=model_seconds, view_run_id=view_run,
                      settings=dict(width=width, height=width),
                      source_profile=dict(fingerprint=fingerprint, name="test.mp4"),
                      quality=dict(compute_device="AMD Radeon RX 6700 XT", training_steps=steps))
        (directory / "status.json").write_text(json.dumps(status))
        (directory / "3d.log").write_text(f"Done training! Took FormattedDuration({train_seconds}s).")
        views = dict(state="ready", elapsed_seconds=view_seconds, auto_model=True,
                     capture_plan=dict(positions=positions, total_views=positions*16,
                                       start_seconds=0, end_seconds=65))
        (view_dir / "status.json").write_text(json.dumps(views))
        # A newer view run must not contaminate the historical model's timing.
        (self.jobs / job / "views_status.json").write_text(json.dumps(dict(views, elapsed_seconds=999)))
        return directory

    def test_total_uses_linked_views_and_never_adds_train_twice(self):
        self.make_run(0)
        row = measured_run(self.jobs, *RUNS[0])
        self.assertEqual(row["total_seconds"], 110)
        self.assertEqual(row["other_seconds"], 40)
        self.assertTrue(row["auto_model"])

    def test_missing_log_is_unknown_not_zero_and_disables_calculator(self):
        directory = self.make_run(0)
        (directory / "3d.log").unlink()
        report = build_report(self.jobs)
        self.assertIsNone(report["baseline"]["train_seconds"])
        self.assertNotIn('name="steps"', render_report(report))

    def test_other_run_is_predicted_without_using_its_elapsed_time(self):
        self.make_run(0)
        directory = self.make_run(1, width=2048, steps=30000, positions=20, model_seconds=500)
        first = build_report(self.jobs)
        self.assertEqual(first["validation"]["estimated"]["train_seconds"], 360)
        self.assertEqual(first["validation"]["estimated"]["total_seconds"], 520)
        status = json.loads((directory / "status.json").read_text())
        status["elapsed_seconds"] = 800
        (directory / "status.json").write_text(json.dumps(status))
        second = build_report(self.jobs)
        self.assertEqual(first["validation"]["estimated"], second["validation"]["estimated"])
        self.assertNotEqual(first["validation"]["error_percent"], second["validation"]["error_percent"])

    def test_different_scene_is_not_same_scene_validation(self):
        self.make_run(0)
        self.make_run(1, fingerprint="scene-b")
        self.assertIsNone(build_report(self.jobs)["validation"])

    def test_invalid_inputs_are_rejected_and_escaped(self):
        self.make_run(0)
        report = build_report(self.jobs)
        for value in ["0", "-1", "nan", "<script>alert(1)</script>", "1000001"]:
            page = render_report(report, {"steps": [value]})
            self.assertIn('role="alert"', page)
            self.assertNotIn("<script>alert(1)</script>", page)
        with self.assertRaises(ValueError):
            predict(report["baseline"], positions=10, views=160, width=0, height=1024, steps=20000)

    def test_empty_workspace_shows_no_fabricated_measurements(self):
        report = build_report(self.jobs)
        self.assertEqual(report["measured"], [])
        self.assertIsNone(report["validation"])
        self.assertFalse(report["cloud"]["measured_same_scene"])
        self.assertIn("ยังไม่มี baseline", render_report(report))


if __name__ == "__main__":
    unittest.main()
