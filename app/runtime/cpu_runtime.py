"""CPU budgets for processing workers, independent from the web/preview process."""
from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path


def available_cpus():
    count = os.cpu_count() or 1
    if hasattr(os, 'sched_getaffinity'):
        count = min(count, len(os.sched_getaffinity(0)))
    elif os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        mask, system = ctypes.c_size_t(), ctypes.c_size_t()
        kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        if kernel.GetProcessAffinityMask(kernel.GetCurrentProcess(), ctypes.byref(mask), ctypes.byref(system)):
            count = min(count, mask.value.bit_count() or count)
    # Respect container CPU quota as well as affinity. No changes to affinity/priority.
    if os.name != 'nt':
        try:
            quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
            if quota != 'max':
                count = min(count, max(1, math.ceil(int(quota) / int(period))))
        except (OSError, ValueError, ZeroDivisionError):
            pass
    return max(1, count)


@dataclass(frozen=True)
class CPUBudget:
    mode: str
    available: int
    threads: int

    def metadata(self):
        return asdict(self)

    def environment(self, environment=None, native_threads=None):
        result = dict(os.environ if environment is None else environment)
        threads = str(self.threads if native_threads is None else native_threads)
        for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                    'NUMEXPR_NUM_THREADS', 'RAYON_NUM_THREADS'):
            result[key] = threads
        return result


def cpu_budget(environment=None):
    environment = os.environ if environment is None else environment
    mode = environment.get('CPU_MODE', 'full').strip().lower()
    if mode not in {'full', 'balanced'}:
        raise ValueError('CPU_MODE must be full or balanced')
    available = available_cpus()
    requested = environment.get('CPU_THREADS', '').strip()
    if requested and (not requested.isdigit() or int(requested) < 1):
        raise ValueError('CPU_THREADS must be a positive integer')
    count = min(available, int(requested)) if requested else (
        available if mode == 'full' else max(1, math.floor(available * .75)))
    return CPUBudget(mode, available, count)


def colmap_threads(compute, stage, environment):
    budget = cpu_budget(environment)
    if stage == 'mapper':
        return ['--Mapper.num_threads', str(budget.threads)]
    if compute.backend != 'CPU':
        return []
    prefix = ('FeatureExtraction' if stage == 'extraction' else 'FeatureMatching') if compute.modern else (
        'SiftExtraction' if stage == 'extraction' else 'SiftMatching')
    return [f'--{prefix}.num_threads', str(budget.threads)]


def colmap_command(command, compute, environment):
    command=list(command)
    operation=command[1]
    stage='extraction' if operation=='feature_extractor' else 'matching' if operation.endswith('_matcher') else 'mapper' if operation=='mapper' else None
    if stage:
        command.extend(colmap_threads(compute,stage,environment))
    return command
