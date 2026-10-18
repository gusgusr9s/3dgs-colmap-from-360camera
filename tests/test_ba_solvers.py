"""Global BA solver flags, measured ranking, VRAM limits and staged mapping orchestration."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.jobs.task_progress import progress_details
from app.pipeline import staged_mapping as staged
from app.runtime import ba_solvers as solvers
from app.runtime.colmap_ba import digest


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root/'colmap.exe'
        self.exe.write_bytes(b'fixture colmap')

    def record(self, ranking, executable=None):
        path = solvers.benchmark_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema': 1, 'ranking': ranking,
                                    'executable_sha256': digest(executable or self.exe)}))

    def test_flags_force_the_requested_ceres_path(self):
        self.assertEqual(solvers.solver_flags('cpu_iterative')[:2], ['--BundleAdjustmentCeres.use_gpu', '0'])
        self.assertIn('--BundleAdjustmentCeres.max_num_images_direct_sparse_cpu_solver', solvers.solver_flags('cpu_iterative'))
        dense = solvers.solver_flags('cuda_dense', '1')
        self.assertEqual(dense[:4], ['--BundleAdjustmentCeres.use_gpu', '1', '--BundleAdjustmentCeres.gpu_index', '1'])
        # COLMAP rejects a dense threshold that is not below the sparse GPU threshold (4000).
        self.assertLess(int(dense[dense.index('--BundleAdjustmentCeres.max_num_images_direct_dense_gpu_solver')+1]), 4000)

    def test_cpu_machine_never_selects_a_gpu_solver(self):
        self.record(['cuda_dense', 'cuda_sparse', 'cpu_iterative'])
        self.assertEqual(solvers.select(self.exe, False, {}, 1000, root=self.root)[0], 'cpu_iterative')
        with self.assertRaisesRegex(RuntimeError, 'CUDA'):
            solvers.select(self.exe, False, {'COLMAP_BA_SOLVER': 'cuda_dense'}, 1000, root=self.root)
        with self.assertRaises(ValueError):
            solvers.select(self.exe, False, {'COLMAP_BA_SOLVER': 'fast'}, 1000, root=self.root)

    def test_measured_ranking_needs_the_same_executable(self):
        self.record(['cpu_sparse', 'cpu_iterative'])
        self.assertEqual(solvers.select(self.exe, False, {}, 500, root=self.root),
                         ('cpu_sparse', 'fastest measured by the installer/Verify benchmark'))
        self.exe.write_bytes(b'replaced colmap')
        name, reason = solvers.select(self.exe, False, {}, 500, root=self.root)
        self.assertEqual(name, 'cpu_iterative')
        self.assertIn('no benchmark', reason)

    def test_dense_cuda_respects_vram_and_frame_limits(self):
        self.record(['cuda_dense', 'cpu_iterative'])
        with patch.object(solvers, 'free_vram_mib', return_value=8000):
            self.assertEqual(solvers.select(self.exe, True, {}, 1230, root=self.root)[0], 'cuda_dense')
            name, reason = solvers.select(self.exe, True, {}, solvers.DENSE_MAX_FRAMES+1, root=self.root)
            self.assertEqual(name, 'cpu_iterative')
            self.assertIn('frames', reason)
        with patch.object(solvers, 'free_vram_mib', return_value=900):
            name, reason = solvers.select(self.exe, True, {}, 1230, root=self.root)
            self.assertEqual(name, 'cpu_iterative')
            self.assertIn('MiB', reason)
        with patch.object(solvers, 'free_vram_mib', return_value=None):
            self.assertEqual(solvers.select(self.exe, True, {}, 1230, root=self.root)[0], 'cpu_iterative')

    def test_ceres_report_parsing(self):
        log = ('Bundle adjustment report\n    Residuals : 6876186\n   Iterations : 5\n         Time : 39.0954 [s]\n'
               ' Initial cost : 0.496049 [px]\n   Final cost : 0.477172 [px]\n')
        self.assertEqual(solvers.parse_report(log), dict(seconds=39.0954, final_cost=0.477172, initial_cost=0.496049, iterations=5))


class StagedPlanTests(unittest.TestCase):
    def test_enabled_only_for_long_balanced_jobs_unless_forced(self):
        self.assertTrue(staged.enabled(1230, 'balanced', {}))
        self.assertFalse(staged.enabled(1230, 'full', {}))
        self.assertFalse(staged.enabled(200, 'balanced', {}))
        self.assertTrue(staged.enabled(100, 'full', {'COLMAP_STAGED_MAPPING': 'on'}))
        self.assertFalse(staged.enabled(1230, 'balanced', {'COLMAP_STAGED_MAPPING': 'off'}))
        with self.assertRaises(ValueError):
            staged.enabled(1230, 'balanced', {'COLMAP_STAGED_MAPPING': 'yes'})

    def test_chunks_grow_by_ratio_and_retry_failures_first(self):
        names = [f'pano_{index:06d}.jpg' for index in range(1000)]
        images, chunk, fresh = staged.plan_chunk(names, set(), set())
        self.assertEqual(len(chunk), staged.START_FRAMES)
        self.assertEqual(chunk[0], names[0])
        registered = set(names[:500])
        images, chunk, fresh = staged.plan_chunk(names, registered, set())
        self.assertEqual(len(chunk), 100)
        self.assertEqual(chunk[0], names[500])
        self.assertEqual(len(images), 600)
        failed = {names[500], names[501]}
        _, chunk, _ = staged.plan_chunk(names, registered, failed)
        self.assertEqual(chunk[:2], sorted(failed))
        self.assertEqual(len(chunk), 100)
        self.assertNotIn(names[500], chunk[2:])
        _, chunk, fresh = staged.plan_chunk(names, set(names[:990]), set(names[990:]))
        self.assertFalse(fresh)
        self.assertEqual(len(chunk), 10)


class FakeColmap:
    """Simulates mapper/bundle_adjuster/point_filtering with JSON 'models'."""
    def __init__(self, names, unregistrable=()):
        self.names, self.unregistrable, self.commands = names, set(unregistrable), []

    @staticmethod
    def write(directory, registered):
        directory.mkdir(parents=True, exist_ok=True)
        (directory/'model.json').write_text(json.dumps(sorted(registered)))

    def __call__(self, command):
        self.commands.append(command)
        option = lambda name: Path(command[command.index(name)+1]) if name in command else None
        if command[1] == 'mapper':
            listed = option('--Mapper.image_list_path').read_text(encoding='utf8').split()
            previous = set(json.loads((option('--input_path')/'model.json').read_text())) if option('--input_path') else set()
            output = option('--output_path') if option('--input_path') else option('--output_path')/'0'
            self.write(output, previous | {name for name in listed if name not in self.unregistrable})
        else:
            self.write(option('--output_path'), json.loads((option('--input_path')/'model.json').read_text()))


def fake_count(directory):
    try:
        return len(json.loads((Path(directory)/'model.json').read_text()))
    except OSError:
        return 0


class StagedRunTests(unittest.TestCase):
    def run_staged(self, total, unregistrable=()):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        names = [f'pano_{index:06d}.jpg' for index in range(total)]
        colmap = FakeColmap(names, unregistrable)
        reports = []
        log = (root/'3d.log').open('w', encoding='utf8')
        self.addCleanup(log.close)
        with patch.object(staged, 'database_names', return_value=names), \
             patch.object(staged, 'registered_names', side_effect=lambda model: set(json.loads((Path(model)/'model.json').read_text()))), \
             patch.object(staged, 'model_count', side_effect=fake_count), \
             patch.object(staged, 'replace_model', side_effect=lambda source, target: FakeColmap.write(target, json.loads((source/'model.json').read_text()))):
            result = staged.run('colmap', database=root/'database.db', image_dir=root/'panoramas', output_root=root/'sparse',
                                checkpoints=root/'snapshots', input_model=None, bootstrap_options=['--boot'],
                                segment_options=['--segment'], choose_solver=lambda frames: ('cpu_iterative', ['--solver'], 'test'),
                                global_tolerance='0.000001', run_command=colmap, report=lambda *args, **kwargs: reports.append((args, kwargs)), log=log)
        return root, result, colmap, reports

    def test_full_sequence_registers_everything_with_external_ba(self):
        root, result, colmap, reports = self.run_staged(600)
        self.assertEqual(fake_count(result), 600)
        mappers = [command for command in colmap.commands if command[1] == 'mapper']
        solves = [command for command in colmap.commands if command[1] == 'bundle_adjuster']
        self.assertIn('--boot', mappers[0])
        self.assertNotIn('--input_path', mappers[0])
        self.assertTrue(all('--segment' in command and '--input_path' in command for command in mappers[1:]))
        # One converging solve per staged segment, plus the closing solve after filtering.
        self.assertEqual(len(solves), len(mappers)-1+1)
        self.assertEqual(colmap.commands[-2][1], 'point_filtering')
        self.assertTrue(all('--solver' in command for command in solves))
        # Only the final model remains; intermediate checkpoints and lists are cleaned up.
        self.assertEqual(list((root/'snapshots').iterdir()), [])
        self.assertTrue(any(kwargs.get('segment_start') == 200 for _, kwargs in reports))

    def test_unregistrable_frames_end_the_loop(self):
        root, result, colmap, _ = self.run_staged(400, unregistrable={'pano_000390.jpg', 'pano_000391.jpg'})
        self.assertEqual(fake_count(result), 398)
        self.assertLess(len(colmap.commands), 20)

    def test_short_sequence_stays_on_the_bootstrap_path(self):
        root, result, colmap, _ = self.run_staged(150)
        self.assertEqual(fake_count(result), 150)
        self.assertEqual([command[1] for command in colmap.commands], ['mapper'])


class StagedProgressTests(unittest.TestCase):
    def test_external_solve_keeps_count_and_reports_ba(self):
        source = dict(state='running', stage='model', progress=29, pose_total_positions=1230,
                      pose_initial_registered_positions=785, pose_segment_registered_positions=942)
        log = ('$ colmap mapper --input_path a\nRegistering image #950 (num_reg_frames=960)\n'
               '$ colmap bundle_adjuster --input_path b\n')
        status = progress_details(source, log)
        self.assertEqual(status['phase_done'], 942)
        self.assertEqual(status['pose_activity'], 'global_bundle_adjustment')
        status = progress_details(source, '$ colmap mapper --input_path c\n')
        self.assertEqual(status['phase_done'], 942)


if __name__ == '__main__':
    unittest.main()
