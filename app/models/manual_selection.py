"""Immutable manual deletion masks and screen-space brush/lasso selection."""

from app.paths import ROOT, WORKSPACE
import hashlib
import json
from pathlib import Path
import re
import uuid

import cv2
import numpy as np
from app.jobs.task_history import utc_now

SELECTIONS = WORKSPACE/'web_data/model_edits/selections'


def selection_directory(identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise ValueError('รหัสการเลือกจุดไม่ถูกต้อง')
    return SELECTIONS/identifier


def load_removed(source, identifier, count, signature, source_sha256):
    if not identifier:
        return np.zeros(count, dtype=bool)
    try:
        directory = selection_directory(identifier)
        record = json.loads((directory/'record.json').read_text(encoding='utf8'))
        if (record['model'] != source.name or record['source_signature'] != signature or
                record['source_sha256'] != source_sha256 or record['count'] != count):
            raise ValueError('การเลือกจุดนี้ไม่ตรงกับ Model ต้นทาง กรุณาเลือกใหม่')
        packed = (directory/'removed.bin').read_bytes()
        if len(packed) != (count+7)//8 or hashlib.sha256(packed).hexdigest() != record['mask_sha256']:
            raise ValueError('ข้อมูลการเลือกจุดไม่ครบ กรุณาเลือกใหม่')
        return np.unpackbits(np.frombuffer(packed, dtype=np.uint8), bitorder='little', count=count).astype(bool)
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('ไม่พบข้อมูลการเลือกจุด กรุณาเลือกใหม่') from None


def store_removed(source, removed, signature, source_sha256, parent, operation):
    identifier = uuid.uuid4().hex
    directory = selection_directory(identifier); directory.mkdir(parents=True)
    packed = np.packbits(removed, bitorder='little').tobytes()
    with (directory/'removed.bin').open('xb') as stream:
        stream.write(packed)
    record = {'id': identifier, 'model': source.name, 'count': len(removed), 'source_signature': signature,
              'source_sha256': source_sha256, 'mask_sha256': hashlib.sha256(packed).hexdigest(),
              'removed_count': int(np.count_nonzero(removed)), 'parent': parent,
              'operation': operation, 'created_at': utc_now()}
    with (directory/'record.json').open('x', encoding='utf8') as stream:
        json.dump(record, stream, ensure_ascii=False)
    return identifier


def validate_screen(value):
    try:
        if not isinstance(value, dict):
            raise ValueError()
        tool = value['tool']
        points = np.asarray(value['points'], dtype=float)
        width, height = value['viewport']
        view = np.asarray(value['view'], dtype=float)
        projection = np.asarray(value['view_projection'], dtype=float)
        radius = float(value.get('radius', 20))
        front = value.get('front_only', True)
        depth = float(value.get('depth_tolerance', .02))
        if (tool not in {'brush','lasso'} or points.ndim != 2 or points.shape[1] != 2 or
                len(points) < (3 if tool == 'lasso' else 1) or len(points) > 512 or
                not np.isfinite(points).all() or not isinstance(width, int) or not isinstance(height, int) or
                not 32 <= width <= 8192 or not 32 <= height <= 8192 or width*height > 20000000 or
                np.any(points < 0) or np.any(points > [width, height]) or
                view.shape != (4,4) or projection.shape != (4,4) or not np.isfinite(view).all() or
                not np.isfinite(projection).all() or abs(np.linalg.det(projection)) < 1e-15 or
                not np.allclose(view[3], [0,0,0,1], atol=1e-6) or
                not np.allclose(view[:3,:3].T@view[:3,:3], np.eye(3), atol=1e-5) or
                not 1 <= radius <= 200 or not np.isfinite(depth) or not .001 <= depth <= .2 or not isinstance(front,bool)):
            raise ValueError()
        if tool == 'lasso' and abs(cv2.contourArea(points.astype(np.float32))) < 4:
            raise ValueError()
        return {'tool': tool, 'points': points.tolist(), 'viewport': [width,height], 'view':view.tolist(),
                'view_projection':projection.tolist(), 'radius':radius, 'front_only':front, 'depth_tolerance':depth}
    except (KeyError, TypeError, ValueError, OverflowError, cv2.error):
        raise ValueError('พื้นที่เลือกหรือมุมกล้องไม่ถูกต้อง กรุณาลากเลือกใหม่') from None


def screen_hits(vertices, eligible, operation):
    operation = validate_screen(operation)
    width, height = operation['viewport']
    shape = np.zeros((height,width), dtype=np.uint8)
    points = np.rint(operation['points']).astype(np.int32)
    if operation['tool'] == 'lasso':
        cv2.fillPoly(shape, [points], 1)
    else:
        radius = int(round(operation['radius']))
        if len(points) > 1:
            cv2.polylines(shape, [points], False, 1, max(1,2*radius), cv2.LINE_8)
        for point in points:
            cv2.circle(shape, tuple(point), radius, 1, -1)
    view, projection = np.asarray(operation['view']), np.asarray(operation['view_projection'])
    # Store one projected location per source vertex. No splat parameters are changed.
    pixels = np.full(len(vertices), -1, dtype=np.int32)
    depths = np.full(len(vertices), np.inf)
    for start in range(0, len(vertices), 65536):
        chunk = vertices[start:start+65536]
        world = np.column_stack([chunk['x'],chunk['y'],chunk['z'],np.ones(len(chunk))])
        clip = world@projection.T
        z = -(world@view[2])
        valid = eligible[start:start+len(chunk)].astype(bool) & np.isfinite(clip).all(axis=1) & (clip[:,3] > 1e-9) & (z > 0)
        # A near-transparent center should not hide a deeper surface in front-only selection.
        valid &= chunk['opacity'] >= np.log((1/255)/(1-1/255))
        with np.errstate(invalid='ignore', divide='ignore'):
            ndc = clip[:,:3]/clip[:,3,None]
        valid &= (np.abs(ndc) <= 1).all(axis=1)
        local = np.flatnonzero(valid)
        xy = np.column_stack([(ndc[local,0]+1)*width/2, (1-ndc[local,1])*height/2]).astype(int)
        xy[:,0] = np.clip(xy[:,0],0,width-1); xy[:,1] = np.clip(xy[:,1],0,height-1)
        pixels[start+local] = xy[:,1]*width+xy[:,0]; depths[start+local] = z[local]
    visible = np.flatnonzero(pixels >= 0)
    hit = np.zeros(len(vertices), dtype=bool)
    candidate = visible[shape.ravel()[pixels[visible]].astype(bool)]
    if operation['front_only'] and len(candidate):
        # Approximate the front layer in 4px cells using ALL eligible centers, including
        # occluders outside the drawn path. Exact alpha compositing is not a depth image.
        grid_width = (width+3)//4
        cell = (pixels[visible]//width//4)*grid_width+(pixels[visible]%width//4)
        front = np.full(grid_width*((height+3)//4), np.inf)
        np.minimum.at(front, cell, depths[visible])
        candidate_cell = (pixels[candidate]//width//4)*grid_width+(pixels[candidate]%width//4)
        nearest = front[candidate_cell]
        candidate = candidate[depths[candidate] <= nearest*(1+operation['depth_tolerance'])+1e-6]
    hit[candidate] = True
    return hit
