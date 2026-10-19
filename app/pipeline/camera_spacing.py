"""Relative camera spacing diagnostics, independent of reconstruction scale."""
import numpy as np

from app.pipeline.sampling_policy import recommended_sample_every


def _recommendation(direction, candidate, current, fps, *, basis, overlap, caveat):
    # Geometry can request denser captures, but automatic advice must not undo
    # the Source target by recommending e.g. every 30 frames for 30 FPS video.
    if fps and np.isfinite(fps) and fps > 0:
        limit = recommended_sample_every(fps)
        if candidate > limit:
            candidate = limit
            if direction in {'increase', 'keep'}:
                basis = 'sampling_target'
        if direction != 'inspect':
            direction = 'decrease' if candidate < current else 'increase' if candidate > current else 'keep'
    return dict(direction=direction, sample_every=candidate, current_sample_every=current,
                gap_seconds=candidate/fps if fps and fps > 0 else None,
                basis=basis, overlap=overlap, estimated_position_ratio=current/candidate,
                minimum_preview_seconds=6*candidate/fps if fps and fps > 0 else None,
                caveat=caveat)


def spacing_report(cameras, minimum_percent=.15, maximum_percent=1.0):
    if not 0 <= minimum_percent < maximum_percent:
        raise ValueError('Spacing thresholds must satisfy 0 <= minimum < maximum')
    centers = np.asarray([camera['position'] for camera in cameras], dtype=float)
    if len(centers) < 2:
        return None
    if centers.shape != (len(cameras), 3) or not np.isfinite(centers).all():
        raise ValueError('Invalid camera centers')
    span = float(np.linalg.norm(np.ptp(centers, axis=0)))
    distances = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    # Degenerate trajectories have no meaningful normalized spacing.
    percentages = distances/span*100 if span > 1e-12 else np.zeros_like(distances)
    return dict(unit='relative COLMAP scale', normalization='camera_path_bounding_box_diagonal',
                path_span=span, degenerate=span <= 1e-12,
                minimum=float(distances.min()), mean=float(distances.mean()), median=float(np.median(distances)), maximum=float(distances.max()),
                minimum_percent=float(percentages.min()), median_percent=float(np.median(percentages)),
                maximum_percent=float(percentages.max()),
                thresholds=dict(minimum_percent=minimum_percent, maximum_percent=maximum_percent),
                segments=[dict(start=index, end=index+1, distance=float(distance), percent=float(percent),
                               state='dense' if percent < minimum_percent else 'wide' if percent > maximum_percent else 'within_range')
                          for index,(distance,percent) in enumerate(zip(distances,percentages))],
                scope='Heuristic for consecutive camera positions, not scene points or guaranteed overlap; no metric calibration.')


def _track_overlap_recommendation(preview, geometry, current, fps, degenerate):
    overlaps = geometry.get('overlap', {})
    adjacent = overlaps.get('1', {})
    common = adjacent.get('p10_common', 0)
    fraction = adjacent.get('p10_fraction', 0)
    summary = preview.get('summary', {})
    issues = bool(preview.get('unregistered_frames') or summary.get('suspicious_segments') or degenerate)
    direction, candidate, basis = 'keep', current, 'track_overlap'
    if issues:
        direction, basis = 'inspect', 'pose_quality'
    elif common < 100 or fraction < .15:
        candidate = max(1, current // 2)
        direction = 'decrease' if candidate < current else 'inspect'
        basis = 'low_track_overlap'
    elif fraction >= .75:
        supported = [stride for stride in range(2, 5)
                     if overlaps.get(str(stride), {}).get('p10_common', 0) >= 100
                     and overlaps.get(str(stride), {}).get('p10_fraction', 0) >= .25]
        if supported:
            candidate = min(10000, current * max(supported))
            if candidate > current:
                direction, basis = 'increase', 'high_track_overlap'
    return _recommendation(direction, candidate, current, fps, basis=basis, overlap=adjacent,
        caveat='คำแนะนำจากช่วงทดสอบเท่านั้น ตรวจช่วงเลี้ยวและจุดเชื่อมฉากก่อนใช้กับคลิปทั้งหมด')


def compare_spacing(preview, geometry, reference, sample_every=1, fps=None):
    """Compare baseline / observed depth, avoiding a full-path versus short-clip gauge."""
    report = spacing_report(preview['cameras'])
    if not report:
        return report
    depths = geometry.get('depths', {})
    cameras = preview['cameras']
    values = []
    for segment, a, b in zip(report['segments'], cameras, cameras[1:]):
        da, db = depths.get(str(a.get('frame_index'))), depths.get(str(b.get('frame_index')))
        value = segment['distance']/((da+db)/2)*100 if da and db and np.isfinite([da,db]).all() and da>0 and db>0 else None
        segment['depth_percent'] = value
        if value is not None and np.isfinite(value):
            values.append(value)
    if len(values) != len(report['segments']) or not values:
        report['comparison_unavailable'] = 'ไม่พบความลึกจุด 3D ที่กล้องสังเกตครบทุกตำแหน่ง'
        report['recommendation'] = _track_overlap_recommendation(
            preview, geometry, max(1, int(sample_every)), fps, report['degenerate'])
        return report
    report['depth_normalized'] = dict(mean=float(np.mean(values)), minimum=min(values),
                                    maximum=max(values), median=float(np.median(values)))
    current = max(1, int(sample_every))
    summary = preview.get('summary', {})
    issues = bool(preview.get('unregistered_frames') or summary.get('suspicious_segments') or report['degenerate'])
    if not reference or reference.get('mean', 0) <= 1e-9:
        report['comparison_unavailable'] = 'ไม่มีงานอ้างอิงที่เทียบความลึกได้ จึงใช้จุดภาพ COLMAP ที่ซ้อนกันในช่วงทดสอบ'
        # Without a comparable prior job, use observed COLMAP track overlap
        # between this preview's neighboring frames as a conservative signal.
        report['recommendation'] = _track_overlap_recommendation(
            preview, geometry, current, fps, report['degenerate'])
        return report
    baseline = reference['mean']
    report['reference'] = reference
    report['ratio_to_reference'] = report['depth_normalized']['mean']/baseline
    for segment in report['segments']:
        ratio = segment['depth_percent']/baseline
        segment['reference_ratio'] = ratio
        segment['density_state'] = 'very_dense' if ratio < .5 else 'dense' if ratio < 1 else 'wide' if ratio > 2 else 'within_range'
    ratio = report['ratio_to_reference']
    desired = current*1.25/max(ratio, 1e-12)
    candidate = max(1, min(current*4, int(round(desired))))
    if candidate >= 20:
        candidate = max(1, int(round(candidate/5)*5))
    candidate = min(10000, current*4, candidate)
    direction = 'increase' if ratio < 1 else 'decrease' if ratio > 2 else 'keep'
    if direction == 'keep':
        candidate = current
    checked_candidate = min(candidate, recommended_sample_every(fps)) if fps and np.isfinite(fps) and fps > 0 else candidate
    stride = max(1, int(np.ceil(checked_candidate/current)))
    overlap = geometry.get('overlap', {}).get(str(stride), {})
    supported = overlap.get('p10_common',0) >= 100 and overlap.get('p10_fraction',0) >= .15
    if direction == 'increase' and (issues or not supported or ratio < .01):
        direction, candidate = 'inspect', current
    elif direction == 'decrease':
        candidate = max(1, min(current-1, candidate))
        if candidate == current:
            direction = 'inspect'
    report['recommendation'] = _recommendation(direction, candidate, current, fps,
        basis='pose_quality' if direction == 'inspect' else 'depth_reference', overlap=overlap,
        caveat='เกณฑ์ทดลอง: เป้าหมาย 1.25 เท่าของระยะอ้างอิง ตรวจ track ร่วมก่อนลดภาพ; ไม่รับประกัน pose หรือภาพซ้อน ต้องทดสอบช่วงเลี้ยวและจุดเชื่อมฉาก')
    return report
