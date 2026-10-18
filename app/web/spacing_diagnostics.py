"""Cached native geometry for historical Jobs and disposable previews."""
from functools import lru_cache
import json
from pathlib import Path

from app.pipeline.camera_spacing import compare_spacing

REFERENCE_JOB = 'cbf586354f'


@lru_cache(maxsize=8)
def _geometry(model, stamps):
    import pycolmap
    from app.pipeline.spacing_geometry import measure
    return measure(pycolmap.Reconstruction(model))


def geometry(directory, preview):
    saved = preview.get('quality', {}).get('spacing_geometry')
    if saved:
        return saved
    try:
        metadata = json.loads((directory/'brush_data/transforms.json').read_text(encoding='utf8'))
        model = (directory/metadata['colmap_model_path']).resolve()
        if not model.is_relative_to(directory.resolve()):
            return {}
        stamps = tuple((p.name,p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(model.glob('*.bin')))
        return _geometry(str(model),stamps) if stamps else {}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError):
        return {}


def diagnostics(directory, preview, jobs):
    reference = None
    try:
        ref_dir = Path(jobs)/REFERENCE_JOB
        ref_preview = json.loads((ref_dir/'pose_preview.json').read_text(encoding='utf8'))
        measured = compare_spacing(ref_preview,geometry(ref_dir,ref_preview),None)
        if measured and measured.get('depth_normalized'):
            reference = dict(job=REFERENCE_JOB, **measured['depth_normalized'],
                             raw_mean=measured['mean'], normalization='baseline / median observed 3D depth')
    except (OSError, ValueError, KeyError):
        pass
    try:
        metadata = json.loads((directory/'transforms.json').read_text(encoding='utf8'))
    except (OSError, ValueError):
        metadata = preview
    plan = metadata.get('capture_plan', {})
    sample = plan.get('effective_sample_every',metadata.get('settings',{}).get('sample_every',1))
    gap = plan.get('sample_gap_seconds')
    fps = sample/gap if gap and gap>0 else None
    return compare_spacing(preview,geometry(directory,preview),reference,sample,fps)
