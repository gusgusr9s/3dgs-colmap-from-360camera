"""Honest phase progress: measurable work gets a %, other phases remain indeterminate."""
import re
from app.jobs.task_estimates import phase
from app.jobs.colmap_progress import pose_details

LABELS = {'views':'สร้าง Views', 'preparation':'เตรียมข้อมูล', 'features':'COLMAP · สกัด features',
          'matching':'COLMAP · จับคู่ภาพ', 'poses':'COLMAP · หา Camera Pose',
          'dataset':'เตรียม dataset', 'masks':'สร้าง Masks', 'training':'Train Model',
          'finalize':'ประเมินผล / ส่งออก'}


def progress_details(status, log=''):
    result = dict(status)
    state = status.get('state', 'waiting')
    current = status.get('phase') or phase(status)
    step = str(status.get('step', '')).lower()
    # Old statuses can lack phases, and mask/preparation callbacks share overall 39%.
    if 'masking' in step or 'เตรียม mask' in step:
        current = 'masks'
    done = total = None
    if current == 'views':
        done, total = status.get('frames', 0), status.get('capture_plan', {}).get('total_views')
    elif current == 'features':
        matches = re.findall(r'Processed file\s*\[(\d+)/(\d+)\]', log)
        if matches:
            done, total = map(int, matches[-1])
    elif current == 'matching':
        matches = re.findall(r'Processing image\s*\[(\d+)/(\d+)\]', log)
        if matches:
            done, total = map(int, matches[-1])
    elif current == 'poses':
        result.update(pose_details(status, log))
        done, total = result.get('registered_positions_so_far'), result.get('pose_total_positions')
    elif current == 'training':
        done, total = status.get('iteration'), status.get('training_steps')
        matches = re.findall(r'Refine iter (\d+),', log)
        if matches:
            done = int(matches[-1])
    elif current == 'masks':
        done, total = status.get('mask_images_written'), status.get('mask_images_total')
        if done is None:
            matches = re.findall(r'Masking people ([\d,]+)/([\d,]+)', str(status.get('step', '')))
            if matches:
                done, total = [int(n.replace(',', '')) for n in matches[-1]]
    if status.get('work_total'):
        done, total = status.get('work_done'), status['work_total']
    percent = None
    if done is not None and total and total > 0:
        done = max(0, min(done, total))
        percent = round(100 * done / total, 1)
        if current == 'poses' and state == 'running':
            percent = min(99.0, percent)  # Final BA/model validation still remain.
    if state in {'ready', 'done'}:
        percent = 100.0
    result.update(phase_key=current, phase_label=LABELS.get(current, current),
                  phase_percent=percent, phase_done=done, phase_total=total,
                  overall_is_estimate=status.get('stage') != 'views')
    result['phase_percent_is_estimate'] = current == 'poses'
    if state == 'queued':
        label = 'รอคิว · ยังไม่เริ่มประมวลผล'
    elif state in {'waiting', 'cancelled', 'missing', 'deleted'}:
        label = 'ยกเลิกแล้ว' if state == 'cancelled' else 'ยังไม่เริ่มงาน' if state == 'waiting' else 'ไม่มีข้อมูล'
    elif state in {'ready', 'done'}:
        label = 'เสร็จแล้ว · 100%'
    elif state == 'error':
        label = 'หยุดเนื่องจากข้อผิดพลาด'
    elif state == 'stopped':
        label = 'หยุดตามคำขอแล้ว · เก็บข้อมูลที่ทำเสร็จไว้'
    else:
        overall = max(0, min(100, int(status.get('progress', 0))))
        label = (f'ภาพรวมโดยประมาณ {overall}% · ' if result['overall_is_estimate'] else '')
        label += result['phase_label'] + (f' {percent:g}%' if percent is not None else ' · กำลังทำงาน (ยังวัด % ขั้นนี้ไม่ได้)')
        if done is not None and total:
            label += f' · {done:,}/{total:,}'
        if current == 'poses':
            label += ' · ' + result['pose_progress_note']
    result['progress_label'] = label
    return result
