"""Read-only readiness check for Ubuntu/Windows and optional RTX CUDA."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compute_runtime import colmap_version, nvidia_devices, select_colmap_compute, select_mask_device


def executable(name, override=None):
    suffix = ".exe" if os.name == "nt" else ""
    if override and os.environ.get(override):
        candidate = Path(os.environ[override]).expanduser()
        return str(candidate / (name + suffix) if candidate.is_dir() else candidate)
    candidates = {"colmap": ROOT / "tools/colmap/bin" / (name + suffix),
                  "brush_app": ROOT / "tools/brush" / (name + suffix)}
    candidate = candidates.get(name)
    if candidate and candidate.is_file():
        return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    sibling = Path(sysconfig.get_path("scripts")) / (name + suffix)
    return str(sibling) if sibling.is_file() else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view-device", choices=("auto", "cpu", "wgpu"), default=os.environ.get("VIEW_DEVICE", "auto"))
    parser.add_argument("--colmap-device", choices=("auto", "cpu", "cuda"), default=os.environ.get("COLMAP_DEVICE", "auto"))
    parser.add_argument("--mask-device", default=os.environ.get("MASK_DEVICE", "auto"))
    parser.add_argument("--views-only", action="store_true")
    args = parser.parse_args(argv)
    environment = os.environ.copy()
    environment["COLMAP_DEVICE"] = args.colmap_device
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    failures = []

    def check(label, operation):
        try:
            value = operation()
            print(f"[ ok ] {label}: {value}", flush=True)
            return value
        except Exception as exc:
            failures.append(label)
            print(f"[fail] {label}: {exc}", flush=True)
            return None

    print(f"Python: {sys.executable} ({sys.version.split()[0]})")
    modules = ["numpy", "cv2", "PIL"]
    if not args.views_only:
        modules += ["plyfile", "torch", "torchvision", "pycolmap", "nerfstudio"]
    for module in modules:
        check(module, lambda module=module: getattr(importlib.import_module(module), "__version__", "importable"))
    def view_device():
        if args.view_device == 'cpu':
            return 'CPU requested'
        try:
            import numpy as np
            from gpu_projection import WGPUProjector
            from pano_to_views import perspective_map
            projector = WGPUProjector(16,16,(16,32,3),[perspective_map(16,16,90,0,0)[:2]])
            metadata = projector.metadata;projector.close()
            return f'{metadata["device"]} / {metadata["backend"]}; compute/readback passed'
        except Exception as exc:
            if args.view_device == 'wgpu':
                raise
            return f'CPU fallback ({exc})'
    check('Views compute', view_device)
    if args.views_only:
        return int(bool(failures))

    def probe(name, override=None, arguments=("--version",)):
        command = executable(name, override)
        if not command:
            raise RuntimeError(f"{name} is missing; run the setup script")
        result = subprocess.run([command, *arguments], capture_output=True, text=True,
                                check=True, timeout=30, env=environment)
        return result.stdout + result.stderr

    check("ffmpeg", lambda: probe("ffmpeg", arguments=("-version",)).splitlines()[0])

    def colmap():
        banner = probe("colmap", "COLMAP_BIN", ("-h",))
        if colmap_version(banner) < (4, 1, 0):
            raise RuntimeError("360 input requires COLMAP 4.1+; bash setup.sh builds 4.2.1")
        selected = select_colmap_compute(banner, environment)
        return f"{banner.splitlines()[0]} -> {selected.backend} ({selected.reason})"

    check("COLMAP", colmap)
    check("Brush CLI", lambda: probe("brush_app", "BRUSH_CLI").strip())
    check("ns-process-data", lambda: probe("ns-process-data", arguments=("--help",)).splitlines()[0])
    print("NVIDIA GPUs: " + (", ".join(nvidia_devices(environment)) or "none detected"))

    def torch_device():
        import torch
        from mask_compute import MaskPredictor
        from torchvision.models.segmentation import LRASPP_MobileNet_V3_Large_Weights, lraspp_mobilenet_v3_large
        predictor = MaskPredictor(lraspp_mobilenet_v3_large(weights=LRASPP_MobileNet_V3_Large_Weights.DEFAULT).eval(), args.mask_device)
        predictor.probabilities(torch.zeros(1,3,520,520))
        return f'{predictor.metadata}; inference passed'

    check("Person masks", torch_device)
    if sys.platform.startswith("linux"):
        def vulkan():
            summary = probe("vulkaninfo", arguments=("--summary",))
            names = [line.strip() for line in summary.splitlines() if "deviceName" in line]
            if not names:
                raise RuntimeError("Vulkan did not report a device")
            if not any(kind in summary for kind in ("DISCRETE_GPU", "INTEGRATED_GPU")):
                raise RuntimeError("Vulkan exposes only a software adapter; check the GPU driver")
            return "; ".join(names)
        check("Vulkan for Brush", vulkan)
    def viewer_assets():
        import json
        import hashlib
        directory = ROOT / "assets/vendor"
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            raise RuntimeError("Run scripts/install_viewer_assets.py")
        assets = json.loads(manifest.read_text(encoding="utf-8"))
        if not assets:
            raise RuntimeError("Viewer asset manifest is empty")
        for name, details in assets.items():
            target = directory / name
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != details.get("sha256"):
                raise RuntimeError(f"Missing or changed viewer asset: {name}")
        return f"{len(assets)} verified files"
    check("Viewer assets", viewer_assets)
    print("Readiness check " + ("failed: " + ", ".join(failures) if failures else "passed. Confirm Brush's selected GPU in the training log."))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
