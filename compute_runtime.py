"""Select CUDA for COLMAP and person masks independently of Brush/WGPU."""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
import re
import shutil
import subprocess
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing


def nvidia_devices(environment=None) -> list[str]:
    environment = os.environ if environment is None else environment
    executable = shutil.which("nvidia-smi", path=environment.get("PATH", ""))
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, check=False, timeout=10, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()] if result.returncode == 0 else []


def colmap_version(banner: str) -> tuple[int, int, int]:
    match = re.search(r"COLMAP\s+(\d+)\.(\d+)(?:\.(\d+))?", banner)
    return tuple(int(value or 0) for value in match.groups()) if match else (0, 0, 0)


@dataclass(frozen=True)
class ColmapCompute:
    backend: str
    gpu_index: str
    modern: bool
    reason: str
    fallback_reasons: tuple[str, ...] = ()

    def options(self, stage: str) -> list[str]:
        if stage not in {"extraction", "matching"}:
            raise ValueError(f"Unknown COLMAP stage: {stage}")
        prefix = ("FeatureExtraction" if stage == "extraction" else "FeatureMatching") if self.modern else (
            "SiftExtraction" if stage == "extraction" else "SiftMatching")
        options = [f"--{prefix}.use_gpu", "1" if self.backend == "CUDA" else "0"]
        if self.backend == "CUDA":
            options += [f"--{prefix}.gpu_index", self.gpu_index]
        return options

    def metadata(self) -> dict:
        return {"colmap_backend": self.backend, "colmap_gpu_index": self.gpu_index,
                "colmap_device_reason": self.reason, "colmap_fallback_reasons": list(self.fallback_reasons)}


def select_colmap_compute(banner: str, environment=None) -> ColmapCompute:
    environment = os.environ if environment is None else environment
    mode = environment.get("COLMAP_DEVICE", "auto").strip().lower()
    if mode not in {"auto", "cpu", "cuda"}:
        raise ValueError("COLMAP_DEVICE must be auto, cpu, or cuda")
    gpu_index = environment.get("COLMAP_GPU_INDEX", "-1").strip()
    if not re.fullmatch(r"-1|\d+", gpu_index):
        raise ValueError("COLMAP_GPU_INDEX must be -1 (automatic) or a nonnegative GPU index")
    modern = colmap_version(banner) >= (3, 13, 0)
    if mode == "cpu":
        return ColmapCompute("CPU", gpu_index, modern, "CPU requested")
    # CUDA is a property of the COLMAP executable, not of the Torch wheel.
    has_cuda = bool(re.search(r"with CUDA(?: support)?|CUDA-enabled", banner, re.I))
    devices = nvidia_devices(environment) if has_cuda else []
    reason = "COLMAP was not built with CUDA" if not has_cuda else "No NVIDIA GPU visible to nvidia-smi"
    if devices:
        return ColmapCompute("CUDA", gpu_index, modern, "CUDA build and NVIDIA driver detected")
    if mode == "cuda":
        raise RuntimeError(reason + "; install CUDA-enabled COLMAP / NVIDIA driver, or use COLMAP_DEVICE=cpu")
    return ColmapCompute("CPU", gpu_index, modern, reason)


def select_mask_device(torch, requested=None):
    mode = (requested if requested is not None else os.environ.get("MASK_DEVICE", "auto")).strip().lower()
    if not re.fullmatch(r"auto|cpu|cuda(?::\d+)?", mode):
        raise ValueError("MASK_DEVICE must be auto, cpu, cuda, or cuda:N")
    if mode == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        device = torch.device("cuda" if mode == "auto" else mode)
        if device.index is not None and device.index >= torch.cuda.device_count():
            raise RuntimeError(f"MASK_DEVICE={mode} does not exist")
        return device
    if mode != "auto":
        raise RuntimeError("CUDA person masking requested but PyTorch CUDA is unavailable; install a CUDA Torch wheel / driver or use MASK_DEVICE=cpu")
    return torch.device("cpu")


def run_colmap_command(command, compute, log, environment, cwd):
    """Retry a failed GPU feature stage against a clean/restored database."""
    gpu_stage = command[1] == "feature_extractor" or command[1].endswith("_matcher")
    automatic = environment.get("COLMAP_DEVICE", "auto").lower() == "auto"
    recoverable = gpu_stage and compute.backend == "CUDA" and automatic
    backup = None
    database = Path(command[command.index("--database_path") + 1]) if recoverable else None
    if database is not None and database.exists():
        with tempfile.NamedTemporaryFile(dir=database.parent, suffix=".backup", delete=False) as file:
            backup = Path(file.name)
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
    start = log.tell()
    try:
        from task_control import run
        run(command, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=cwd, env=environment)
        return compute
    except subprocess.CalledProcessError as exc:
        log.flush()
        if hasattr(log, "getvalue"):
            error = log.getvalue()[start:]
        else:
            with Path(log.name).open("r", encoding="utf-8", errors="replace") as reader:
                reader.seek(start); error = reader.read()[-32768:]
        error += str(exc.stderr or "")
        if not recoverable or not re.search(r"(?:CUDA|GPU).{0,60}(?:fail|error|unavailable|unsupported)|(?:fail|error).{0,60}(?:CUDA|GPU)|SiftGPU.*not.*supported|out of memory|device (?:lost|removed)|no kernel image|invalid device function", error, re.I):
            raise
        # Commands own this run's DB. Never mix half-written GPU features/matches with CPU retry.
        for suffix in ("", "-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
        if backup:
            backup.replace(database); backup = None
        reason = "GPU stage failed: " + next((line for line in reversed(error.splitlines()) if re.search(r"CUDA|GPU|memory", line, re.I)), "driver error")[:400]
        selected = replace(compute, backend="CPU", reason=reason, fallback_reasons=compute.fallback_reasons + (reason,))
        retry = list(command)
        for flag in ("--FeatureExtraction.use_gpu", "--FeatureMatching.use_gpu", "--SiftExtraction.use_gpu", "--SiftMatching.use_gpu"):
            if flag in retry:
                retry[retry.index(flag) + 1] = "0"
        for flag in ("--FeatureExtraction.gpu_index", "--FeatureMatching.gpu_index", "--SiftExtraction.gpu_index", "--SiftMatching.gpu_index"):
            if flag in retry:
                offset = retry.index(flag); del retry[offset:offset+2]
        from cpu_runtime import colmap_threads
        stage='extraction' if retry[1]=='feature_extractor' else 'matching'
        thread_flag,thread_count=colmap_threads(selected,stage,environment)
        if thread_flag in retry:retry[retry.index(thread_flag)+1]=thread_count
        else:retry.extend([thread_flag,thread_count])
        log.write("CPU fallback: " + reason + "\n$ " + " ".join(retry) + "\n"); log.flush()
        run(retry, stdout=log, stderr=subprocess.STDOUT, check=True, cwd=cwd, env=environment)
        return selected
    finally:
        if backup:
            backup.unlink(missing_ok=True)
