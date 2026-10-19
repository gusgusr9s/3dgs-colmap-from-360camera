"""Private process: short Views + native spherical poses, no persistent Job or Train."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote


def main():
    request_file = Path(sys.argv[1]).resolve()
    attempt = request_file.parent
    # Set before importing server / paths: generated auxiliary files stay private.
    os.environ['STUDIO_WORKSPACE'] = str(attempt)
    from app.jobs.task_control import read, write, scope
    from app.runtime.tool_environment import tool_environment
    from app.web import server
    from app.pipeline.reconstruction_preview import build_pose_preview
    request = read(request_file)
    path = attempt.parent
    source = Path(request['source'])
    started = time.monotonic()
    def save(state='running',**extra):
        write(path/'status.json',dict(state=state,at=time.time(),elapsed_seconds=round(time.monotonic()-started,3),**extra))
    try:
        stamp = source.stat()
        if stamp.st_size != request['size'] or stamp.st_mtime_ns != request['mtime_ns']:
            raise ValueError('Source เปลี่ยนหลังขอ Preview กรุณาคำนวณใหม่')
        views = attempt/'views'
        views.mkdir(parents=True,exist_ok=True)
        def progress(count):
            save(step=f'สร้างภาพชั่วคราว {count} ภาพ',progress=5)
        with scope(attempt,'temporary-preview'):
            save(step=f'กำลังอ่านคลิปไม่เกิน {request.get("seconds",5)} วินาที',progress=1)
            server.process_file(source,views,progress_callback=progress,isolate_cpu=True,**request['settings'])
            metadata = read(views/'transforms.json')
            executable = server.colmap_bin_dir(prefer_modern=True)
            if not executable:
                raise ValueError('ไม่พบ COLMAP กรุณาตรวจการติดตั้ง')
            executable = executable/('colmap.exe' if os.name=='nt' else 'colmap')
            with (attempt/'poses.log').open('w',encoding='utf8') as log:
                quality = server.run_spherical_colmap(executable,source,views,views/'spherical_colmap',
                                                    views/'brush_data',metadata,log,tool_environment(),save)
            preview = build_pose_preview(views,quality)
            for view in preview['views']:
                view['image_url'] = '/pose-preview-files/'+path.name+'/'+attempt.name+'/views/brush_data/images/'+quote(view['name'])
            write(path/'preview.json',preview)
            save('ready',step='แผนที่ชั่วคราวพร้อม · Mask คนก่อนหา pose · ไม่สร้าง Job ไม่ Train · เก็บไว้ประมาณ 1 ชั่วโมง',progress=100)
    except Exception as exc:
        save('error',error=str(exc),step='พรีวิวไม่สำเร็จ ลองเลื่อนจุดเริ่มหรือขยายช่วงในงานจริง')
        raise


if __name__ == '__main__':
    main()
