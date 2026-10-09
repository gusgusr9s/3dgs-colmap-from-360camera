"""Prepare masks for a ready pose dataset before queued GPU training."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import web_app as w


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True)
    args=parser.parse_args()
    directory=w.job_directory(args.job)
    if directory is None or not w.claim_task(args.job):raise RuntimeError('Job missing or busy')
    try:
        status=w.read_json(directory/'pose_status.json')
        if status.get('state')!='ready' or not w.cached_pose_preview(directory):raise ValueError('Prepare and verify poses first')
        import torch
        torch.set_num_threads(8);torch.set_num_interop_threads(2)
        quality=w.create_dynamic_person_masks(directory/'brush_data')
        preview=w.read_json(directory/'pose_preview.json')
        status['quality'].update(quality);preview['quality']=status['quality']
        w.write_json(directory/'pose_preview.json',preview)
        w.write_json(directory/'pose_status.json',status)
        print(quality,flush=True)
    finally:w.release_task(args.job)


if __name__=='__main__':main()
