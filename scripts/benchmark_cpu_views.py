"""Compare CPU Views without touching jobs or using a GPU. Default budget: 2 threads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threads',type=int,default=2)
    parser.add_argument('--positions',type=int,default=12)
    parser.add_argument('--resolution',type=int,default=768)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.threads<1 or not 1<=args.positions<=120 or not 160<=args.resolution<=2048:
        parser.error('threads>=1, positions=1..120, resolution=160..2048')
    os.environ.update(CPU_THREADS=str(args.threads),CPU_MODE='full',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    import web_app as app
    from cpu_runtime import cpu_budget
    app.cv2.setNumThreads(cpu_budget().threads)
    output=args.output or ROOT/'web_data/capture_review'/('cpu_benchmark_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    output.mkdir(parents=True,exist_ok=False)
    source=output/'fixture.mp4'
    writer=app.cv2.VideoWriter(str(source),app.cv2.VideoWriter_fourcc(*'mp4v'),10,(768,384))
    if not writer.isOpened():raise RuntimeError('MP4 writer unavailable')
    for index in range(args.positions):
        image=app.np.random.default_rng(index).integers(0,256,(384,768,3),dtype=app.np.uint8)
        writer.write(image)
    writer.release()
    settings=dict(width=args.resolution,height=args.resolution,fov=90,views=8,sample_every=1,projection='panorama',quality='high',start_seconds=0,end_seconds=0)
    results={}
    for name,isolated in [('serial',False),('parallel',True)]:
        target=output/name;target.mkdir()
        started=time.perf_counter()
        count=app.process_file(source,target,view_device='cpu',isolate_cpu=isolated,**settings)
        results[name]={'seconds':round(time.perf_counter()-started,4),'images':count}
    equal=all(hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256((output/'parallel'/path.name).read_bytes()).digest()
              for path in (output/'serial').glob('frame_*.jpg'))
    report={'cpu_runtime':cpu_budget().metadata(),'settings':settings,'positions':args.positions,**results,
            'speedup':round(results['serial']['seconds']/results['parallel']['seconds'],3),'jpeg_bytes_identical':equal,
            'parallel_compute':app.read_json(output/'parallel/transforms.json')['view_compute'],
            'scope':'Synthetic clip; includes decode / projection / JPEG / disk and CPU worker startup. Not a model/COLMAP benchmark.'}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if equal else 1


if __name__=='__main__':raise SystemExit(main())
