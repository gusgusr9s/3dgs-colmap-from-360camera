"""Scene composition pages and HTTP endpoints for the existing workspace."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path
from urllib.parse import quote, unquote

from app.models import scene_projects, scene_assets, scene_package, scene_exports
from app.models.scene_geometry import world_box, transform_vector
from app.paths import TEMPLATES, ROOT


def asset_display_name(info, fallback):
    if info.get('edit') and info.get('source_name'):
        return Path(info['source_name']).stem + (' · ตัดขอบ' if info['edit'].get('type')=='spatial_crop' else ' · แก้ไข')
    return info.get('label') or fallback


def resolve_asset(app, kind, reference):
    if kind == 'glb':
        return scene_assets.record(reference)
    if kind == 'ply':
        source = app.model_crop.model_path(app.MODELS, reference)
        info = app.model_info(source)
        quality = info.get('quality') or {}
        return {'kind': kind, 'ref': reference, 'label': reference, 'display_name': asset_display_name(info,reference),
                'url': '/models/' + quote(reference, safe=''), 'bytes': source.stat().st_size,
                'splats': quality.get('published_splats', 0) if isinstance(quality, dict) else 0,
                'world_up': info.get('world_up'), 'initial_view': app.model_viewer_config(reference)}
    if kind == 'spz':
        record = app.compression_record(reference)
        if not record or record.get('state') != 'ready':
            raise ValueError('ไม่พบ SPZ ที่พร้อมใช้งาน')
        source = app.compression_directory(reference) / 'model.spz'
        if not source.is_file():
            raise ValueError('ไม่พบไฟล์ SPZ')
        info = record.get('parent_info', {})
        return {'kind': kind, 'ref': reference,
                'display_name': asset_display_name(info,Path(record.get('model',reference)).stem),
                'label': Path(record.get('model', record.get('parent_model', reference))).stem + '.spz',
                'url': '/compressed-download/' + reference + '.spz', 'bytes': source.stat().st_size,
                'splats': record.get('splats', 0),
                'world_up': info.get('world_up'), 'initial_view': info.get('viewer_config', {})}
    raise ValueError('รองรับโมเดล PLY, SPZ และ GLB')


def source_asset_path(app, kind, reference):
    resolve_asset(app, kind, reference)
    if kind == 'ply':
        return app.model_crop.model_path(app.MODELS, reference)
    if kind == 'spz':
        return app.compression_directory(reference) / 'model.spz'
    return scene_assets.asset_path(reference)


def asset_catalog(app):
    assets = []
    for source in sorted(app.MODELS.glob('*.ply'), key=lambda item: item.name.casefold()):
        try:
            assets.append(resolve_asset(app, 'ply', source.name))
        except (ValueError, OSError):
            continue
    for record in app.compression_records():
        try:
            assets.append(resolve_asset(app, 'spz', record['id']))
        except (ValueError, OSError, KeyError):
            continue
    return assets + scene_assets.records()


def library_page(app):
    rows = []
    for scene in scene_projects.list_scenes():
        identifier = scene['id']
        name = html.escape(scene['name'])
        rows.append(f'<article class="resource"><div><div class="resource-title"><a href="/scene/{identifier}">{name}</a></div>'
                    f'<div class="resource-meta">{len(scene["objects"])} โมเดล · {len(scene["colliders"])} พื้น/กำแพง · '
                    f'{html.escape(scene.get("updated_at", ""))}</div></div><div class="actions">'
                    f'<a class="button primary" href="/scene/{identifier}">เปิดจัดฉาก</a>'
                    f'<a class="button" href="/scene-download/{identifier}.json" download>ดาวน์โหลดฉาก JSON</a>'
                    f'<button class="danger" data-scene-delete="{identifier}" data-revision="{scene["revision"]}">ลบฉาก</button>'
                    '</div></article>')
    body = '''<section class="panel"><div class="panel-head"><div><h2>Scenes / Maps</h2>
<p class="muted">เลือกหลายสถานที่มาจัดตำแหน่งต่อกัน พร้อมพื้นและกำแพงตรวจชน</p></div></div>
<form id="scene-create" class="actions"><label for="scene-new-name">ชื่อฉาก</label>
<input id="scene-new-name" name="name" required maxlength="120" placeholder="เช่น อาคาร A + ทางเดิน">
<button type="submit" class="primary">+ สร้างฉากรวมโมเดล</button>
<label class="button" for="scene-import">นำเข้าฉาก JSON</label><input id="scene-import" type="file" accept=".json,application/json" hidden></form>
<p id="scene-library-status" role="status" aria-live="polite"></p>
<p class="muted">เลือก PLY/SPZ จากคลังเดิมหรือนำเข้า GLB mesh ในหน้าจัดฉาก จัดแนวด้วยจุดคู่และสอบเทียบระยะจริงเป็นเมตรได้
ฉาก JSON อ้างอิงคลังเดิม ส่วน ZIP พร้อม Viewer รวมไฟล์โมเดลสำหรับย้ายไปเปิดเครื่องอื่น</p></section>
<section class="panel"><h2>ฉากที่บันทึกไว้</h2><div class="list">'''
    body += ''.join(rows) or '<div class="empty">ยังไม่มีฉาก สร้างฉากแล้วเลือกโมเดลได้หลายรายการ</div>'
    body += '</div></section><script type="module" src="/assets/scene_library.mjs"></script>'
    return app.layout(body, 'scenes', 'Scenes / Maps', 'รวมสถานที่และทดลองเดินในฉาก')


def editor_page(identifier):
    scene = scene_projects.read_scene(identifier)
    page = (TEMPLATES / 'scene_editor.html').read_text(encoding='utf8')
    return page.replace('__TITLE__', html.escape(scene['name'])).replace(
        '__CONFIG__', json.dumps({'id': identifier}, ensure_ascii=False).replace('<', '\\u003c'))


def collision_obj(scene):
    """Export actual triangles of authored boxes; these are proxy surfaces."""
    lines = ['# 360 Studio floor/wall collision proxy', '# units: ' + scene['units']]
    offset = 1
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2),
             (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5)]
    for box in scene['colliders']:
        if not box['enabled']:
            continue
        position, rotation, size = world_box(box, scene['objects'])
        sx, sy, sz = [value / 2 for value in size]
        lines.append('o collider_' + box['id'])
        for x, y, z in [(-sx,-sy,-sz),(sx,-sy,-sz),(sx,sy,-sz),(-sx,sy,-sz),
                        (-sx,-sy,sz),(sx,-sy,sz),(sx,sy,sz),(-sx,sy,sz)]:
            vertex = [p+v for p,v in zip(position, transform_vector(rotation,[x,y,z]))]
            lines.append('v ' + ' '.join(f'{value:.9g}' for value in vertex))
        lines.extend('f ' + ' '.join(str(offset + vertex) for vertex in face) for face in faces)
        offset += 8
    return ('\n'.join(lines) + '\n').encode('utf8')


def handle_get(handler, app, path, query):
    if not (path in {'/scenes', '/api/scenes', '/api/scene-assets'} or
            path.startswith(('/scene/', '/api/scenes/', '/scene-download/', '/scene-collision/', '/scene-asset/', '/api/scene-package/', '/scene-package-download/', '/api/scene-export/', '/scene-glb-download/', '/scene-glb-viewer/'))):
        return False
    try:
        if path == '/scenes':
            handler.send_html(library_page(app))
        elif path == '/api/scene-assets':
            handler.send_json({'assets': asset_catalog(app)})
        elif path == '/api/scenes':
            handler.send_json({'scenes': scene_projects.list_scenes()})
        elif path.startswith('/scene-asset/') and path.endswith('.glb'):
            identifier = path.removeprefix('/scene-asset/').removesuffix('.glb')
            scene_assets.record(identifier)
            handler.send_file(scene_assets.asset_path(identifier))
        elif path.startswith('/api/scene-package/'):
            identifier = path.removeprefix('/api/scene-package/')
            scene_projects.read_scene(identifier)
            handler.send_json(scene_package.status(identifier))
        elif path.startswith('/api/scene-export/'):
            identifier=path.removeprefix('/api/scene-export/')
            scene_projects.read_scene(identifier)
            handler.send_json(scene_exports.status(identifier))
        elif path.startswith(('/scene-glb-download/','/scene-glb-viewer/')):
            viewer=path.startswith('/scene-glb-viewer/')
            parts=path.split('/')[2:]
            if len(parts)!=2 or not parts[1].endswith('.glb'):raise ValueError('Invalid scene GLB URL')
            identifier,key=parts[0],parts[1].removesuffix('.glb')
            scene_projects.read_scene(identifier)
            target=scene_exports.download_path(identifier,key)
            if viewer:
                page=(ROOT/'examples/threejs-splat.html').read_text(encoding='utf8')
                page=page.replace('https://cdn.jsdelivr.net/npm/three@0.186.1/','/assets/vendor/three-0.186.1/').replace("'./model.glb'",f"'/scene-glb-download/{identifier}/{key}.glb'")
                handler.send_html(page)
            else:handler.send_file(target)
        elif path.startswith('/scene-package-download/'):
            parts = path.removeprefix('/scene-package-download/').split('/')
            if len(parts) != 2 or not parts[1].endswith('.zip'):
                raise ValueError('Invalid package URL')
            scene_projects.read_scene(parts[0])
            handler.send_file(scene_package.download_path(parts[0],parts[1].removesuffix('.zip')))
        elif path.startswith('/scene/'):
            handler.send_html(editor_page(path.removeprefix('/scene/')))
        elif path.startswith('/scene-download/') and path.endswith('.json'):
            scene = scene_projects.read_scene(path.removeprefix('/scene-download/').removesuffix('.json'))
            handler.send_data(json.dumps(scene, ensure_ascii=False, indent=2).encode('utf8'),
                              'application/json; charset=utf-8', 200, 'no-store')
        elif path.startswith('/scene-collision/') and path.endswith('.obj'):
            scene = scene_projects.read_scene(path.removeprefix('/scene-collision/').removesuffix('.obj'))
            handler.send_data(collision_obj(scene), 'text/plain; charset=utf-8', 200, 'no-store')
        elif path.startswith('/api/scenes/'):
            handler.send_json(scene_projects.read_scene(path.removeprefix('/api/scenes/')))
        else:
            handler.send_json({'error': 'ไม่พบฉาก'}, 404)
    except (ValueError, OSError, KeyError) as exc:
        handler.send_json({'error': str(exc)}, 404)
    return True


def handle_post(handler, app, path):
    if path.startswith('/api/scene-export/'):
        try:
            scene=scene_projects.read_scene(path.removeprefix('/api/scene-export/'))
            handler.send_json(scene_exports.start(scene,lambda kind,ref:source_asset_path(app,kind,ref),app.task_lock_alive),202)
        except (ValueError,OSError,KeyError,TypeError) as exc:
            handler.send_json({'error':str(exc)},422)
        return True
    if path == '/api/scene-assets/upload':
        try:
            record = scene_assets.import_glb(handler.rfile,int(handler.headers.get('Content-Length','0')),unquote(handler.headers.get('X-File-Name','model.glb')))
            handler.send_json(record,201)
        except (ValueError,OSError,KeyError,TypeError,UnicodeError,AttributeError) as exc:
            handler.send_json({'error':str(exc)},422)
        return True
    if path.startswith('/api/scene-package/'):
        try:
            scene = scene_projects.read_scene(path.removeprefix('/api/scene-package/'))
            handler.send_json(scene_package.start(scene,lambda kind,ref:source_asset_path(app,kind,ref)),202)
        except (ValueError,OSError,KeyError,TypeError) as exc:
            handler.send_json({'error':str(exc)},422)
        return True
    if path != '/api/scenes' and not path.startswith('/api/scenes/'):
        return False
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 < length <= 2 * 1024 * 1024:
            handler.send_json({'error': 'คำขอฉากต้องมีขนาดไม่เกิน 2 MB'}, 413)
            return True
        payload = json.loads(handler.rfile.read(length).decode('utf8'))
        if not isinstance(payload, dict):
            raise ValueError('ข้อมูลฉากต้องเป็น JSON object')
        resolver = lambda kind, ref: resolve_asset(app, kind, ref)
        if path == '/api/scenes':
            handler.send_json(scene_projects.create_scene(payload, resolver), 201)
        elif path.endswith('/delete'):
            identifier = path.removeprefix('/api/scenes/').removesuffix('/delete')
            scene_projects.delete_scene(identifier, payload.get('revision'))
            handler.send_json({'deleted': identifier})
        else:
            handler.send_json(scene_projects.save_scene(path.removeprefix('/api/scenes/'), payload, resolver))
    except scene_projects.SceneConflict as exc:
        handler.send_json({'error': str(exc), 'current_revision': exc.current_revision}, 409)
    except FileNotFoundError:
        handler.send_json({'error': 'ไม่พบฉาก'}, 404)
    except (ValueError, OSError, KeyError, TypeError, UnicodeError) as exc:
        handler.send_json({'error': str(exc)}, 422)
    return True
