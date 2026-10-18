"""CPU budgets and byte-identical parallel projection, with tiny owned fixtures."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.runtime import cpu_runtime as cpu
from app.web import server as app
from app.jobs.process_logging import ProcessLog, command_timing


class CPUBudgetTests(unittest.TestCase):
    def test_full_balanced_and_limit_respect_available(self):
        with patch.object(cpu,'available_cpus',return_value=28):
            self.assertEqual(cpu.cpu_budget({}).threads,28)
            self.assertEqual(cpu.cpu_budget({'CPU_MODE':'balanced'}).threads,21)
            self.assertEqual(cpu.cpu_budget({'CPU_THREADS':'6'}).threads,6)
            self.assertEqual(cpu.cpu_budget({'CPU_THREADS':'500'}).threads,28)
            for env in ({'CPU_MODE':'bad'},{'CPU_THREADS':'0'},{'CPU_THREADS':'-1'}):
                with self.assertRaises(ValueError):cpu.cpu_budget(env)

    def test_cpu_threads_flags_follow_colmap_version_without_changing_quality(self):
        from app.runtime.compute_runtime import ColmapCompute
        with patch.object(cpu,'available_cpus',return_value=28):
            for modern,prefix in ((True,'FeatureExtraction'),(False,'SiftExtraction')):
                compute=ColmapCompute('CPU','-1',modern,'CPU')
                command=['colmap','feature_extractor','--SiftExtraction.max_num_features','12000']
                result=cpu.colmap_command(command,compute,{})
                self.assertEqual(result,command+[f'--{prefix}.num_threads','28'])
            cuda=ColmapCompute('CUDA','0',True,'GPU')
            self.assertEqual(cpu.colmap_command(['colmap','feature_extractor'],cuda,{}),['colmap','feature_extractor'])
            self.assertEqual(cpu.colmap_command(['colmap','mapper'],cuda,{}),['colmap','mapper','--Mapper.num_threads','28'])

    def test_worker_env_is_copy_and_native_threads_do_not_multiply_pool(self):
        budget=cpu.CPUBudget('full',28,28);original={'PATH':'keep','OMP_NUM_THREADS':'3'}
        result=budget.environment(original,native_threads=1)
        self.assertEqual(result['OMP_NUM_THREADS'],'1')
        self.assertEqual(original['OMP_NUM_THREADS'],'3')
        self.assertEqual(result['PATH'],'keep')


class CPUViewsTests(unittest.TestCase):
    def test_cpu_worker_preserves_jpeg_pixels_metadata_and_sampling(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.mp4'
            writer=app.cv2.VideoWriter(str(source),app.cv2.VideoWriter_fourcc(*'mp4v'),10,(320,160))
            self.assertTrue(writer.isOpened())
            for index in range(8):
                image=app.np.random.default_rng(index).integers(0,256,(160,320,3),dtype=app.np.uint8)
                writer.write(image)
            writer.release()
            original=hashlib.sha256(source.read_bytes()).hexdigest()
            settings=dict(width=160,height=160,fov=90,views=8,sample_every=2,projection='panorama',quality='high',start_seconds=.1,end_seconds=.7)
            baseline=root/'baseline';baseline.mkdir();parallel=root/'parallel';parallel.mkdir()
            app.process_file(source,baseline,view_device='cpu',**settings)
            counts=[]
            with patch.dict(os.environ,{'CPU_MODE':'full','CPU_THREADS':'2'}):
                app.process_file(source,parallel,view_device='cpu',isolate_cpu=True,progress_callback=counts.append,**settings)
            before=app.read_json(baseline/'transforms.json');after=app.read_json(parallel/'transforms.json')
            for key in ('frames','settings','capture_plan','fl_x','fl_y','w','h','source_mode'):
                self.assertEqual(before[key],after[key],key)
            for image in baseline.glob('frame_*.jpg'):
                self.assertEqual(image.read_bytes(),(parallel/image.name).read_bytes(),image.name)
            self.assertEqual(counts[-1],48)
            self.assertEqual(counts,sorted(counts))
            self.assertEqual(after['view_compute']['workers'],2)
            self.assertEqual(after['view_compute']['max_pending'],4)
            self.assertFalse(list(parallel.glob('.*.jpg')))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original)


class ProcessLogTests(unittest.TestCase):
    def test_legacy_job_log_links_use_existing_times_without_rewriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);job=root/'legacy';run=job/'model_runs'/'old';run.mkdir(parents=True)
            raw=run/'3d.log';raw.write_text('original command log',encoding='utf8')
            original=raw.stat()
            app.write_json(job/'3d_status.json',{'run_id':'old','state':'running',
                                              'phase_durations_seconds':{'matching':12.5}})
            with patch.object(app,'JOBS',root):page=app.process_logs_markup('legacy')
            self.assertIn('/jobs/legacy/model_runs/old/3d.log',page)
            self.assertIn('12.500 s',page)
            self.assertIn('งานรุ่นก่อน',page)
            self.assertEqual(raw.stat().st_mtime_ns,original.st_mtime_ns)
            self.assertFalse((run/'process_summary.json').exists())

    def test_phase_durations_error_and_distinct_runs_survive_on_disk(self):
        with tempfile.TemporaryDirectory() as temp:
            logger=ProcessLog(Path(temp)/'run-a','model','run-a',{'threads':28})
            with patch('app.jobs.process_logging.time.monotonic',return_value=logger.started_tick):
                logger.update(dict(state='running',progress=12,step='features'))
            with patch('app.jobs.process_logging.time.monotonic',return_value=logger.started_tick+10):
                logger.update(dict(state='running',progress=21,step='matching'))
            with patch('app.jobs.process_logging.time.monotonic',return_value=logger.started_tick+25):
                logger.update(dict(state='error',progress=0,error='failed matching'))
            report=json.loads((logger.directory/'process_summary.json').read_text(encoding='utf8'))
            self.assertEqual([p['elapsed_seconds'] for p in report['phases']],[10,15])
            self.assertEqual(report['state'],'error')
            self.assertEqual(report['elapsed_seconds'],25)
            rows=[json.loads(line) for line in (logger.directory/'process.jsonl').read_text(encoding='utf8').splitlines()]
            self.assertEqual(rows[-1]['event'],'run_finished')
            self.assertEqual(rows[-1]['error'],'failed matching')
            other=ProcessLog(Path(temp)/'run-b','model','run-b');other.update({'state':'done'})
            self.assertEqual(json.loads((logger.directory/'process_summary.json').read_text(encoding='utf8')),report)

    def test_command_failure_records_elapsed_and_reraises(self):
        log=io.StringIO()
        with self.assertRaises(subprocess.CalledProcessError):
            with command_timing(log,['colmap','mapper']):
                raise subprocess.CalledProcessError(1,['colmap','mapper'])
        entries=[json.loads(line.removeprefix('[process] ')) for line in log.getvalue().splitlines()]
        self.assertEqual(entries[0]['event'],'command_started')
        self.assertEqual(entries[-1]['state'],'error')
        self.assertIn('elapsed_seconds',entries[-1])


if __name__=='__main__':unittest.main()
