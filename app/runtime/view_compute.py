"""Run optional native GPU projection in a process isolated from the web server."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from app.runtime.gpu_projection import GPUUnavailable
from app.runtime.gpu_worker_io import worker_lines
from app.paths import ROOT
from app.runtime.python_command import module_command

EVENT_PREFIX = "@views:"


def view_mode(requested=None):
    mode = (requested or os.environ.get("VIEW_DEVICE", "auto")).strip().lower()
    if mode not in {"auto", "cpu", "wgpu"}:
        raise ValueError("VIEW_DEVICE must be auto, cpu, or wgpu")
    return mode


def cpu_worker_views(source, output, settings, progress=None, compute=None, previous=None):
    from app.runtime.cpu_runtime import cpu_budget
    source,output=Path(source).resolve(),Path(output).resolve()
    budget=cpu_budget()
    environment=budget.environment(native_threads=1)
    environment['PYTHONUTF8']='1'
    result=None
    with (Path(output)/'views.log').open('a',encoding='utf8') as log:
        log.write('CPU worker: '+json.dumps(budget.metadata())+'\n');log.flush()
        process=subprocess.Popen(module_command('app.runtime.cpu_view_worker',source,output,json.dumps(settings)),
                                 stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf8',env=environment,cwd=ROOT,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        with worker_lines(process,label='CPU Views') as lines:
            for line in lines:
                if not line.startswith(EVENT_PREFIX):log.write(line);continue
                event=json.loads(line[len(EVENT_PREFIX):])
                if 'error' in event:raise RuntimeError(event['error'])
                if 'count' in event and progress:progress(event['count'])
                if 'compute' in event and compute:
                    compute({**event['compute'],'requested':(previous or {}).get('requested','cpu'),
                             'fallback_reasons':(previous or {}).get('fallback_reasons',[])})
                if 'complete' in event:result=event['complete']
        if process.returncode!=0 or result is None:raise RuntimeError(f'CPU Views worker failed ({process.returncode}); see views.log')
    path=Path(output)/'transforms.json'
    data=json.loads(path.read_text(encoding='utf8'))
    data['view_compute'].update(requested=(previous or {}).get('requested','cpu'),
                                fallback_reasons=(previous or {}).get('fallback_reasons',[]))
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    return result


def gpu_worker_views(source, output, settings, progress=None, compute=None, requested=None):
    """Return count on GPU success, or None to use the unmodified CPU path."""
    source,output=Path(source).resolve(),Path(output).resolve()
    mode = view_mode(requested)
    if mode == "cpu":
        return None
    reasons = []
    if importlib.util.find_spec("wgpu") is None:
        if mode == "wgpu":
            raise GPUUnavailable("wgpu is missing; install requirements-gpu.txt")
        reasons.append("wgpu is not installed")
        if compute:
            compute({"backend": "CPU", "device": "CPU", "requested": mode, "fallback_reasons": reasons})
        return None
    backends = ["Vulkan", "D3D12"] if os.name == "nt" else ["Vulkan"]
    for backend in backends:
        environment = os.environ.copy()
        environment["WGPU_BACKEND_TYPE"] = backend
        environment["PYTHONUTF8"] = "1"
        command = module_command('app.runtime.view_worker',source,output,json.dumps(settings))
        with (Path(output) / "views.log").open("a", encoding="utf-8") as log:
            log.write(f"Trying WGPU/{backend}\n"); log.flush()
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                                       text=True, encoding="utf-8", env=environment,cwd=ROOT)
            result = None
            try:
                with worker_lines(process) as lines:
                    for line in lines:
                        if not line.startswith(EVENT_PREFIX):
                            log.write(line); continue
                        event = json.loads(line[len(EVENT_PREFIX):])
                        if "compute" in event and compute:
                            metadata = {**event["compute"], "requested": mode, "fallback_reasons": list(reasons) + event["compute"].get('fallback_reasons', [])}
                            compute(metadata)
                        if "count" in event and progress:
                            progress(event["count"])
                        if "complete" in event:
                            result = event["complete"]
                        if "gpu_error" in event:
                            reasons.append(f"{backend}: {event['gpu_error']}")
                        if "error" in event:
                            raise RuntimeError(event["error"])
                code = process.returncode
            except GPUUnavailable as exc:
                reasons.append(f'{backend}: {exc}')
                code = 82

        if code == 0 and result is not None:
            metadata_path = Path(output) / "transforms.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["view_compute"] = {**metadata.get("view_compute", {}), "requested": mode, "fallback_reasons": reasons + metadata.get('view_compute', {}).get('fallback_reasons', [])}
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            return result
        if code not in {82} and 0 < code < 256:
            raise RuntimeError(f"View worker exited with {code}; see views.log")
        if not reasons or not reasons[-1].startswith(backend + ":"):
            reasons.append(f"{backend}: GPU worker exited with {code}")
    if mode == "wgpu":
        raise GPUUnavailable("; ".join(reasons))
    if compute:
        compute({"backend": "CPU", "device": "CPU", "requested": mode, "fallback_reasons": reasons})
    return None
