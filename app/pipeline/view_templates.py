"""Reuse successful model recipes while adapting pixel and sampling budgets."""
from copy import deepcopy
import math

from app.pipeline.capture_preview import sampling_plan
from app.pipeline.view_suggestions import suggest_view_settings


def model_template(record):
    quality = record.get('quality') or {}
    settings = record.get('view_settings') or record.get('settings') or {}
    profile = record.get('source_profile') or {}
    registered = quality.get('registered_panoramas', quality.get('registered_views', 0))
    inputs = quality.get('input_panoramas', quality.get('input_views', 0))
    coverage = registered / inputs if inputs else 0
    psnr = quality.get('eval_psnr_mean_db', 0)
    steps = quality.get('training_steps', 0)
    cumulative = quality.get('cumulative_training_steps', steps)
    if (record.get('state') != 'done' or record.get('selected_frames')
            or cumulative != steps
            or quality.get('quality_preset', settings.get('quality')) != settings.get('quality')
            or not math.isfinite(float(psnr)) or psnr < 18 or coverage < .5
            or not profile.get('width') or not settings
            or settings.get('quality') not in ('preview', 'high', 'ultra')):
        return None
    return {'id': record['model'], 'name': record.get('label') or profile.get('name') or record['model'],
            'model': record['model'], 'job': record.get('job'), 'run_id': record.get('run_id'),
            'source_profile': deepcopy(profile), 'settings': deepcopy(settings),
            'evidence': {'psnr_db': psnr, 'coverage': coverage, 'training_steps': steps,
                         'max_splats': quality.get('max_splats'), 'pose_source': quality.get('pose_source'),
                         'comparison_limitation': quality.get('comparison_limitation')},
            'recorded_at': record.get('finished_at', '')}


def adapt_template(template, profile):
    old = template['settings']
    projection = old.get('projection', 'auto')
    if projection == 'auto': projection = template['source_profile']['detected_projection']
    if projection != profile['detected_projection']:
        return None
    suggested = suggest_view_settings(profile, quality=old['quality'], projection=projection, fov=old['fov'])
    settings = suggested['settings']
    # Preserve angular coverage; bound crops by both the new source and the proven recipe.
    scale = min(1, old['width'] / settings['width'], old['height'] / settings['height'])
    settings.update(views=old['views'], width=max(160, round(settings['width'] * scale)),
                    height=max(160, round(settings['height'] * scale)))
    same_source = (bool(profile.get('fingerprint'))
                   and profile.get('fingerprint') == template['source_profile'].get('fingerprint')
                   and profile.get('modified_ns') == template['source_profile'].get('modified_ns'))
    if same_source:
        settings = {**settings, **old}
        settings['sample_every'] = old.get('sample_every', 1)
    else:
        # Do not transplant a 2 FPS still-sequence interval into a 30 FPS video.
        settings.update(start_seconds=0, end_seconds=0)
    plan = sampling_plan(profile, settings)
    return {**template, 'adapted_settings': settings, 'capture_plan': plan,
            'adaptation': ('ใช้ค่าชุดที่สำเร็จของ Source เดิม' if same_source else
                           'ปรับขนาดภาพและการเก็บเฟรมตาม Source ใหม่ ใช้ทั้งวิดีโอ'),
            'warning': 'ตำแหน่งกล้องและ depth ของชุดตัวอย่างไม่ได้ติดมากับ template ต้องสร้างจากไฟล์ใหม่'}
