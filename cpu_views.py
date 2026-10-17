"""Bounded parallel CPU projection + JPEG; one shared panorama per captured frame."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, ALL_COMPLETED
import json
import math
from pathlib import Path
import time

import cv2

from cpu_runtime import cpu_budget
from pano_to_views import frames_from_input, panorama_angles, perspective_map, project_frame
from capture_preview import sampling_plan
from view_suggestions import probe_source


def convert(source, output, settings, progress=None, compute=None):
    source, output = Path(source), Path(output)
    budget=cpu_budget()
    started=time.perf_counter()
    profile=probe_source(source)
    plan=sampling_plan(profile,settings)
    width,height=int(settings['width']),int(settings['height'])
    angles=panorama_angles(settings['views'],settings['quality']) if plan['panorama'] else [(0.,0.)]
    maps=[]
    focal=0.5*width/math.tan(math.radians(settings['fov'])/2.0)
    frames=[];pending=set();done=0
    timings={'projection_worker_seconds':0.,'write_worker_seconds':0.,'decode_seconds':0.}
    metadata={'backend':'CPU','device':'CPU','requested':'cpu','fallback_reasons':[],
              'cpu_runtime':budget.metadata(),'workers':budget.threads,'max_pending':budget.threads*2,
              'projection_maps':'precomputed fixed point (OpenCV 1/32 pixel)'}
    if compute:compute(metadata)

    def project_write(panorama,index,frame_index,yaw,pitch):
        tick=time.perf_counter()
        view=(cv2.remap(panorama,*maps[index],cv2.INTER_LINEAR,borderMode=cv2.BORDER_WRAP)
              if plan['panorama'] else project_frame(panorama,width,height,settings['fov'],panorama=False))
        projection_seconds=time.perf_counter()-tick
        tick=time.perf_counter()
        name=f'frame_{frame_index:06d}_view_{index:02d}.jpg'
        target=output/name;temporary=output/('.'+name)
        try:
            if not cv2.imwrite(str(temporary),view,[cv2.IMWRITE_JPEG_QUALITY,90]):
                raise RuntimeError(f'บันทึกภาพ {name} ไม่ได้')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return projection_seconds,time.perf_counter()-tick

    def drain(all_done=False):
        nonlocal pending,done
        finished,pending=wait(pending,return_when=ALL_COMPLETED if all_done else FIRST_COMPLETED)
        for future in finished:
            projection_seconds,write_seconds=future.result()
            timings['projection_worker_seconds']+=projection_seconds
            timings['write_worker_seconds']+=write_seconds
            done+=1
        if progress:progress(done)

    iterator=iter(frames_from_input(source,plan['effective_sample_every'],plan['start_frame'],plan['end_frame'],decode_threads=1))
    with ThreadPoolExecutor(max_workers=budget.threads,thread_name_prefix='cpu-view') as pool:
        while True:
            tick=time.perf_counter()
            try:frame_index,panorama=next(iterator)
            except StopIteration:break
            timings['decode_seconds']+=time.perf_counter()-tick
            if maps and panorama.shape[:2] != (sh,sw):
                raise ValueError('ความละเอียดวิดีโอเปลี่ยนระหว่างคลิป กรุณา Export ด้วยความละเอียดคงที่')
            if plan['panorama'] and not maps:
                sh,sw=panorama.shape[:2]
                for yaw,pitch in angles:
                    mx,my,_=perspective_map(width,height,settings['fov'],yaw,pitch)
                    maps.append(cv2.convertMaps(mx*(sw-1)/(width-1),my*(sh-1)/(height-1),cv2.CV_16SC2))
            for index,(yaw,pitch) in enumerate(angles):
                if len(pending)>=metadata['max_pending']:drain()
                pending.add(pool.submit(project_write,panorama,index,frame_index,yaw,pitch))
                frames.append(dict(file_path=f'frame_{frame_index:06d}_view_{index:02d}.jpg',yaw_deg=yaw,pitch_deg=pitch,frame_index=frame_index))
        if pending:drain(all_done=True)
    if not frames:raise ValueError('ไม่พบเฟรมในช่วงที่เลือก')
    timings={key:round(value,4) for key,value in timings.items()}
    timings.update(elapsed_seconds=round(time.perf_counter()-started,4),
                   note='worker_seconds เป็นผลรวมงานที่ทำพร้อมกัน จึงไม่นำมาบวกเป็นเวลาจริงทั้งหมด')
    metadata['timings']=timings
    meta=dict(camera_model='PINHOLE',fl_x=focal,fl_y=focal,cx=(width-1)/2,cy=(height-1)/2,w=width,h=height,
              frames=frames,source_name=source.name,source_mode='panorama' if plan['panorama'] else 'walkthrough_video',
              settings={**settings,'sample_every':plan['effective_sample_every'],'requested_sample_every':settings['sample_every']},
              capture_plan=plan,view_compute=metadata,
              warning='ใช้ placeholder poses; ต้องใช้ COLMAP/SLAM ก่อน train 3DGS')
    (output/'transforms.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    if compute:compute(metadata)
    return len(frames)
