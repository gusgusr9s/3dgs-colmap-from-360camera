"""Compare held-out models against the SAME native 6K reference crops.

Native-input PSNRs at different resolutions are not directly comparable.
Evaluate all rendered images at the baseline 1536x1536 reference resolution
with the baseline masks and exact same held-out camera IDs.
"""
import json
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cv2
import numpy as np
from app.web import server as w


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partial',action='store_true',help='Review finished cases without modifying the running report')
    args=parser.parse_args()
    report_file=w.EXPERIMENTS/'concourse_resolution_comparison.json'
    report=w.read_json(report_file)
    finished=bool(report.get('finished_at')) and all(c['state']=='DONE' for c in report['cases'])
    if not finished and not args.partial:
        raise ValueError('Wait for every controlled comparison to finish')
    ready=[c for c in report['cases'] if c['state']=='DONE']
    if not ready:raise ValueError('No finished comparison yet')
    baseline=w.model_info(w.MODELS/report['baseline'])
    cases=[{'name':'Concourse 6144x3072 baseline','source_width':6144,**baseline},*ready]
    runs=[w.JOBS/c['job']/'model_runs'/c['run_id'] for c in cases]
    renders=[p/'brush_export'/'eval_30000' for p in runs]
    dataset=runs[0]/'dataset'
    metadata=w.read_json(dataset/'transforms.json')
    frames={Path(f['file_path']).stem:f for f in metadata['frames']}
    files=sorted(p.name for p in renders[0].glob('*.png'))
    if len(files)!=232 or any(set(files)!={p.name for p in render.glob('*.png')} for render in renders):
        raise ValueError('Different held-out camera split')
    scores=[[] for _ in cases]
    for filename in files:
        stem=Path(filename).stem.removesuffix('.jpg').removesuffix('.jpeg')
        frame=frames[stem]
        reference=cv2.imread(str(dataset/frame['file_path']))
        mask=cv2.imread(str(dataset/'masks'/(stem+'.png')),0)>127
        for i,render_dir in enumerate(renders):
            rendered=cv2.imread(str(render_dir/filename))
            if rendered is None: raise ValueError('Missing render')
            rendered=cv2.resize(rendered,reference.shape[1::-1],interpolation=cv2.INTER_LINEAR)
            mse=np.mean((reference.astype(np.float32)-rendered.astype(np.float32))[mask]**2)
            scores[i].append(float(10*np.log10(255**2/max(float(mse),1e-9))))
    summary=[]
    for case,values in zip(cases,scores):
        result=dict(source_width=case['source_width'],job=case['job'],run_id=case['run_id'],model=case['model'],
                    common_reference_psnr_mean_db=round(float(np.mean(values)),3),
                    common_reference_psnr_median_db=round(float(np.median(values)),3),
                    common_reference_psnr_min_db=round(min(values),3),
                    native_input_psnr_db=case['quality']['eval_psnr_mean_db'],eval_views=len(values),
                    source_height=case['source_width']//2,
                    crop_size=w.read_json(runs[len(summary)]/'dataset'/'transforms.json')['w'])
        summary.append(result)
    horizontal=[name for name in files if frames[Path(name).stem.removesuffix('.jpg')]['pitch_deg']==0]
    chosen=[min(horizontal,key=lambda name:abs(frames[Path(name).stem.removesuffix('.jpg')]['frame_index']-wanted))
            for wanted in (0,40,80,120,164,200,245)]
    tiles=[]
    for filename in chosen:
        stem=Path(filename).stem.removesuffix('.jpg')
        reference=cv2.imread(str(dataset/frames[stem]['file_path']))
        row=[]
        for label,pixels in [('Source 6K',reference),*[(str(c['source_width'])+' px model',cv2.imread(str(r/filename))) for c,r in zip(cases,renders)]]:
            tile=cv2.resize(pixels,(384,384));cv2.putText(tile,label,(8,24),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,100,255),2)
            row.append(tile)
        tiles.append(np.hstack(row))
    suffix='' if finished else '-partial'
    cv2.imwrite(str(ROOT/'output'/f'concourse-resolution-comparison{suffix}.jpg'),np.vstack(tiles))
    # Native 1536-pixel close-ups make the loss of text/edge detail inspectable.
    detail_name=min(horizontal,key=lambda name:abs(frames[Path(name).stem.removesuffix('.jpg')]['frame_index']-120))
    stem=Path(detail_name).stem.removesuffix('.jpg')
    reference=cv2.imread(str(dataset/frames[stem]['file_path']))
    rows=[]
    for label,pixels in [('Source 6K',reference),*[(str(c['source_width'])+' px model',cv2.imread(str(r/detail_name))) for c,r in zip(cases,renders)]]:
        enlarged=cv2.resize(pixels,(1536,1536),interpolation=cv2.INTER_LINEAR)
        tile=enlarged[512:1024,512:1024].copy();cv2.putText(tile,label,(8,24),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,100,255),2);rows.append(tile)
    cv2.imwrite(str(ROOT/'output'/f'concourse-resolution-details{suffix}.jpg'),np.hstack(rows))
    report['comparison']={'reference':'Original baseline 1536x1536 crops and masks; fixed 232 held-out cameras',
                          'interpolation':'Linear resize renders to baseline dimensions',
                          'limitation':__doc__,'results':summary}
    if finished:w.write_json(report_file,report)
    w.write_json(ROOT/'output'/f'concourse-resolution-comparison{suffix}.json',report['comparison'])
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
