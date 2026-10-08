"""Exercise the consumer HTTP path on real clips, including actual GPU training."""
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen, Request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import web_app as app

base='http://127.0.0.1:8003'
results=[]
output=app.ROOT/'web_data'/'capture_review'
output.mkdir(exist_ok=True)
for source,start,end in [('sample_360.mp4',1.1,3.1),('insta360_x4_exported_sample.mp4',0,4)]:
    key='data/raw:'+source
    with urlopen(base+'/api/view-suggestions?'+urlencode({'source':key})) as response:
        suggestions=json.load(response)
    settings={**suggestions['settings'],'start_seconds':start,'end_seconds':end}
    query=urlencode({'source':key,**settings})
    with urlopen(base+'/api/capture-plan?'+query) as response:plan=json.load(response)
    images=[]
    for ordinal,direction in [(0,0),(2,1),(4,2)]:
        with urlopen(base+'/api/capture-image?'+query+f'&ordinal={ordinal}&direction={direction}') as response:
            path=output/f'{Path(source).stem}_position{ordinal}_direction{direction}.jpg'
            path.write_bytes(response.read());images.append(str(path.relative_to(app.ROOT)))
    post={'action':'generate','source':key,'label':Path(source).stem+' · automatic trimmed capture test',
          'auto_model':'1',**settings}
    with urlopen(Request(base+'/actions',data=urlencode(post).encode()),timeout=60) as response:
        job=urlparse(response.url).path.split('/')[-1]
    if not (app.JOBS/job).is_dir():raise RuntimeError('POST did not create job: '+job)
    print(json.dumps({'started_job':job,'source':source,'plan':plan['plan']},ensure_ascii=True),flush=True)
    last=None;started=time.monotonic()
    while True:
        with urlopen(base+'/api/job/'+job,timeout=20) as response:status=json.load(response)
        current=(status.get('state'),status.get('step'))
        if current!=last:
            print(json.dumps({'job':job,'state':current[0],'step':current[1]},ensure_ascii=True),flush=True);last=current
        if status.get('state') in {'done','error'} and not status.get('busy'):break
        if time.monotonic()-started>1800:raise TimeoutError(job+' exceeded 30 minutes')
        time.sleep(5)
    result={'job':job,'source':source,'settings':settings,'plan':plan['plan'],'preview_images':images,'status':status,
            'limits':['Preview JPEGs are downscaled source projections, not reconstructed 3D.',
                      'Brush evaluation renders are held-out cameras before PLY outlier filtering.',
                      'Masked PSNR does not prove novel-view geometry or moving-object quality.']}
    results.append(result)
    app.write_json(output/'pipeline_results.json',results)
    print(json.dumps({'finished':job,'state':status.get('state'),'model':status.get('model'),
                      'quality':status.get('quality'),'error':status.get('error')},ensure_ascii=True),flush=True)
print('Real capture pipeline review saved to web_data/capture_review/pipeline_results.json',flush=True)
