"""Quality contracts and reproducible evaluation at unseen camera positions."""
import hashlib
import json
import math
from pathlib import Path
import uuid

from training_selection import link_or_copy


def require_evaluation(quality):
    if quality.get('eval_missing_views', 0):
        raise ValueError('ยังเผยแพร่ Model ไม่ได้: ภาพประเมินหรือ Mask อ่านได้ไม่ครบ กรุณาตรวจ Log แล้วลองใหม่')
    count = quality.get('eval_views', 0)
    score = quality.get('eval_psnr_mean_db')
    if not isinstance(count, (int, float)) or not math.isfinite(count) or count < 3:
        raise ValueError('ยังเผยแพร่ Model ไม่ได้: ต้องมีภาพประเมินที่อ่านได้และไม่ถูก Mask ทั้งภาพอย่างน้อย 3 ภาพ กรุณาตรวจภาพ/Mask หรือเลือกช่วงถ่ายให้ยาวขึ้น')
    if not isinstance(score, (int, float)) or not math.isfinite(score) or score < 18:
        raise ValueError('Model quality check failed: คะแนนภาพประเมินไม่ถึง 18 dB กรุณาตรวจภาพสั่น แสง และ Camera Pose แล้วลองใหม่')


def read_metadata(directory):
    return json.loads((Path(directory) / 'transforms.json').read_text(encoding='utf-8'))


def write_metadata(path, value):
    temporary = path.with_name(uuid.uuid4().hex + '.partial')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_evaluation_split(dataset, fps=None):
    """Keep the complete immutable dataset for inspection; give Brush disjoint sets."""
    dataset = Path(dataset)
    metadata = read_metadata(dataset)
    groups = {}
    for ordinal, frame in enumerate(metadata['frames']):
        groups.setdefault(frame.get('frame_index', ordinal), []).append(frame)
    positions = sorted(groups)
    if len(positions) < 4:
        raise ValueError('การตรวจคุณภาพต้องใช้หลายตำแหน่งที่แยกจากภาพเทรน กรุณาเลือกอย่างน้อย 4 ตำแหน่ง (แนะนำ 10 ตำแหน่งขึ้นไป)')
    # A fixed source-time grid gives High and Ultra the same anchors when present.
    spacing = max(1, round(float(fps or 30) * 2))
    held = [p for p in positions[1:-1] if isinstance(p, int) and p % spacing == 0]
    limit = max(1, len(positions) // 5)
    if len(held) > limit:
        held = held[::math.ceil(len(held) / limit)]
    if not held:
        held = [positions[len(positions) // 2]]
    for position in positions[1:-1]:
        if sum(len(groups[p]) for p in held) >= 3:
            break
        if position not in held and len(held) < len(positions) - 3:
            held.append(position)
    if sum(len(groups[p]) for p in held) < 3 or len(positions) - len(held) < 3:
        raise ValueError('ภาพยังไม่พอแยกชุดทดสอบ กรุณาเลือกอย่างน้อย 6 ตำแหน่งสำหรับวิดีโอธรรมดา หรือ 4 ตำแหน่งสำหรับภาพ 360')
    held = set(held)
    train = [f for p in positions if p not in held for f in groups[p]]
    evaluation = [f for p in positions if p in held for f in groups[p]]
    output = dataset / 'training_input'
    output.mkdir(exist_ok=False)
    for frame in metadata['frames']:
        relative = Path(frame['file_path'])
        source, target = (dataset / relative).resolve(), (output / relative).resolve()
        if not source.is_relative_to(dataset.resolve()) or not target.is_relative_to(output.resolve()):
            raise ValueError('พาธภาพเทรนไม่ถูกต้อง')
        link_or_copy(source, target)
        mask = dataset / 'masks' / (relative.stem + '.png')
        if mask.is_file():
            link_or_copy(mask, output / 'masks' / mask.name)
    seed = Path(metadata.get('ply_file_path', 'sparse_pc.ply'))
    if not (dataset / seed).resolve().is_relative_to(dataset.resolve()) or seed.is_absolute():
        raise ValueError('พาธจุดตั้งต้นไม่ถูกต้อง')
    link_or_copy(dataset / seed, output / seed)
    write_metadata(output / 'transforms.json', {**metadata, 'frames': train})
    write_metadata(output / 'transforms_val.json', {**metadata, 'frames': evaluation})
    identity = [(f.get('frame_index'), f.get('yaw_deg'), f.get('pitch_deg')) for f in evaluation]
    report = {'mode': 'camera_positions', 'train_positions': len(positions) - len(held),
              'eval_positions': sorted(held), 'train_views': len(train), 'eval_views_expected': len(evaluation),
              'signature': hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
              'note': 'ภาพทดสอบมาจากตำแหน่งที่ไม่ได้ใช้เทรน Gaussian; COLMAP ใช้ภาพทั้งหมดเพื่อหา pose'}
    write_metadata(dataset / 'evaluation_split.json', report)
    return output, report


def refresh_refinement_input(dataset):
    """Reuse the exact parent split; only replace the initialization checkpoint."""
    dataset = Path(dataset)
    path = dataset / 'training_input'
    if not path.is_dir():
        return dataset, {'mode': 'legacy_views', 'note': 'รุ่นเดิมแยกตามมุมภาพ ตำแหน่งทดสอบอาจมีอยู่ในชุดเทรน'}
    target = path / 'refinement_init.ply'
    target.unlink(missing_ok=True)
    link_or_copy(dataset / 'refinement_init.ply', target)
    for name in ('transforms.json', 'transforms_val.json'):
        meta = json.loads((path / name).read_text(encoding='utf-8'))
        meta['ply_file_path'] = target.name
        write_metadata(path / name, meta)
    return path, json.loads((dataset / 'evaluation_split.json').read_text(encoding='utf-8'))
