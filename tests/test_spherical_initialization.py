import io
import subprocess
import unittest

from app.pipeline.spherical_initialization import run_mapper


class SphericalInitializationTests(unittest.TestCase):
    def test_retry_preserves_gpu_and_quality_flags_and_reports_once(self):
        command=['colmap','mapper','--Mapper.ba_use_gpu','1','--Mapper.min_model_size','3']
        calls, reports, log=[],[],io.StringIO()
        def execute(arguments):
            calls.append(arguments)
            if len(calls)==1:
                log.write('No good initial image pair found\n')
                raise subprocess.CalledProcessError(1,arguments)
            return 'ok'
        result=run_mapper(command,panorama_count=19,resuming=False,execute=execute,log=log,report=reports.append)
        self.assertEqual(result,'ok')
        self.assertEqual(calls[1],command+['--Mapper.init_max_forward_motion','1'])
        self.assertEqual(len(reports),1)

    def test_success_and_unrelated_errors_do_not_retry(self):
        calls=[]
        self.assertEqual(run_mapper(['c','mapper'],panorama_count=19,resuming=False,
                                    execute=lambda command: calls.append(command) or 1,log=io.StringIO()),1)
        self.assertEqual(len(calls),1)
        def fail(command):
            raise subprocess.CalledProcessError(1,command)
        # An earlier failure in an appended log must not cause a retry now.
        log=io.StringIO();log.write('No good initial image pair found\n')
        with self.assertRaises(subprocess.CalledProcessError):
            run_mapper(['c','mapper'],panorama_count=19,resuming=False,execute=fail,log=log)

    def test_resumed_long_and_explicitly_configured_runs_do_not_retry(self):
        for count,resuming,extra in [(19,True,[]),(60,False,[]),(19,False,['--Mapper.init_max_forward_motion','0.9'])]:
            calls,log=[],io.StringIO()
            def fail(command):
                calls.append(command);log.write('No good initial image pair found\n')
                raise subprocess.CalledProcessError(1,command)
            with self.assertRaises(subprocess.CalledProcessError):
                run_mapper(['c','mapper',*extra],panorama_count=count,resuming=resuming,execute=fail,log=log)
            self.assertEqual(len(calls),1)

    def test_retry_failure_is_propagated_after_two_attempts(self):
        calls,log=[],io.StringIO()
        def fail(command):
            calls.append(command);log.write('No good initial image pair found\n')
            raise subprocess.CalledProcessError(1,command)
        with self.assertRaises(subprocess.CalledProcessError):
            run_mapper(['c','mapper'],panorama_count=19,resuming=False,execute=fail,log=log)
        self.assertEqual(len(calls),2)


if __name__ == '__main__':
    unittest.main()
