"""Measure serial, bounded parallel and cached preparation using actual Views."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import cv2
from app.pipeline import dataset_preparation as prepare
from app.jobs.task_control import write


def benchmark(source,output,samples=64,workers=4):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    paths=sorted(Path(source).resolve().glob('frame_*.jpg'))[:samples]
    if not paths:raise ValueError('No Views JPEGs')
    originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    first=cv2.imread(str(paths[0]));height,width=first.shape[:2]
    results={};hashes={};previous=os.environ.get('DATASET_WORKERS')
    try:
        for label,count,cache in [('serial',1,'serial_cache'),('parallel',workers,'parallel_cache'),('cached',workers,'parallel_cache')]:
            destination=output/label;destination.mkdir()
            os.environ['DATASET_WORKERS']=str(count)
            tick=time.perf_counter()
            _,stats=prepare.prepare_images([(p,destination/p.name) for p in paths],output/cache,width,height)
            stats['wall_seconds']=time.perf_counter()-tick
            results[label]=stats
            hashes[label]={p.name:hashlib.sha256((destination/p.name).read_bytes()).hexdigest() for p in paths}
    finally:
        if previous is None:os.environ.pop('DATASET_WORKERS',None)
        else:os.environ['DATASET_WORKERS']=previous
    same=hashes['serial']==hashes['parallel']==hashes['cached']
    unchanged=originals=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result={'source':str(source),'images':len(paths),'resolution':[width,height],'timings':results,
            'parallel_speedup':results['serial']['wall_seconds']/results['parallel']['wall_seconds'],
            'cached_speedup':results['serial']['wall_seconds']/results['cached']['wall_seconds'],
            'identical_encoded_bytes':same,'source_unchanged':unchanged,
            'limits':'OS disk cache is not reset; other production CPU/I/O work may be running. Not a whole-job speedup.'}
    write(output/'result.json',result)
    if not same or not unchanged:raise AssertionError('Pixel/source regression')
    print(json.dumps(result,ensure_ascii=True),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--samples',type=int,default=64)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    benchmark(args.source,args.output,args.samples,args.workers)
