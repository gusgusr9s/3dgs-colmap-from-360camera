"""Measure real CPU/GPU projection and mask parity without touching existing jobs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resolution', type=int, default=1024)
    parser.add_argument('--seconds', type=float, default=2.7)
    args = parser.parse_args()
    from pano_to_views import frames_from_input, panorama_angles, perspective_map, project_frame
    from gpu_projection import WGPUProjector
    from web_app import process_file
    from mask_compute import MaskPredictor
    import torch
    from PIL import Image
    from torchvision.models.segmentation import LRASPP_MobileNet_V3_Large_Weights, lraspp_mobilenet_v3_large

    image = next(frames_from_input(args.source, 1))[1]
    side = args.resolution
    angles = panorama_angles(8, 'high')
    maps = [perspective_map(side, side, 90, yaw, pitch)[:2] for yaw,pitch in angles]
    gpu = WGPUProjector(side, side, image.shape, maps); gpu.upload(image)
    errors=[]
    for index,(yaw,pitch) in enumerate(angles):
        reference=project_frame(image,side,side,90,yaw,pitch,maps=maps[index])
        difference=np.abs(gpu.project(index).astype(np.int16)-reference.astype(np.int16))
        errors.append({'yaw':yaw,'pitch':pitch,'mean':float(difference.mean()),'p99':float(np.percentile(difference,99)), 'max':int(difference.max())})
    result={'views':{'compute':gpu.metadata,'pixel_differences':errors}}
    gpu.close()
    result['views']['passed']=all(e['mean']<=.5 and e['p99']<=2 for e in errors)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gpu_benchmark_',dir=ROOT/'web_data') as temporary:
        measurements={'cpu':[],'wgpu':[]}
        inputs=None
        for trial in range(3):
            for backend in ('cpu','wgpu'):
                destination=Path(temporary)/f'{backend}-{trial}';destination.mkdir()
                started=time.perf_counter()
                count=process_file(args.source,destination,width=side,height=side,quality='high',projection='panorama',
                    sample_every=4,end_seconds=args.seconds,view_device=backend)
                measurements[backend].append(time.perf_counter()-started)
                meta=json.loads((destination/'transforms.json').read_text(encoding='utf-8'))
                if backend=='wgpu' and not meta['view_compute']['backend'].startswith('WGPU/'):
                    raise RuntimeError('GPU benchmark silently ran on CPU')
                if inputs is None:
                    weights=LRASPP_MobileNet_V3_Large_Weights.DEFAULT
                    names=sorted(destination.glob('frame_*.jpg'))
                    samples=[Image.open(names[i]).convert('RGB') for i in [0,3,8,12]]
                    inputs=torch.stack([weights.transforms()(sample) for sample in samples])
                    for sample in samples:sample.close()
                print(f'Views {backend} trial {trial+1}: {measurements[backend][-1]:.3f}s / {count} images',flush=True)
        result['views'].update(seconds=measurements, count=count, improvement_percent=100*(1-np.median(measurements['wgpu'])/np.median(measurements['cpu'])))
        torch.set_num_threads(4)
        model=lraspp_mobilenet_v3_large(weights=weights).eval()
        cpu=MaskPredictor(model,'cpu');reference=cpu.probabilities(inputs)
        dml=MaskPredictor(model,'directml');candidate=dml.probabilities(inputs)
        ignored_a=reference>.05;ignored_b=candidate>.05
        union=np.logical_or(ignored_a,ignored_b).sum();intersection=np.logical_and(ignored_a,ignored_b).sum()
        iou=float(intersection/union) if union else 1.0
        timings={'cpu':[],'directml':[]}
        for trial in range(3):
            for name,predictor in [('cpu',cpu),('directml',dml)]:
                started=time.perf_counter();predictor.probabilities(inputs);timings[name].append(time.perf_counter()-started)
        result['masks']={'compute':dml.metadata,'iou':iou,'max_probability_difference':float(np.abs(reference-candidate).max()),
            'seconds':timings,'improvement_percent':100*(1-np.median(timings['directml'])/np.median(timings['cpu'])), 'passed':iou>=.99}
    result['passed']=result['views']['passed'] and result['masks']['passed']
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({name:{k:v for k,v in section.items() if k in {'passed','improvement_percent','iou','compute'}} for name,section in result.items() if isinstance(section,dict)},indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
