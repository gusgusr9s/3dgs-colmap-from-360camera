"""Read ordinary model bundles/glTF ZIPs and optional scene descriptions."""
import hashlib
import json
import posixpath
import re
import shutil
import stat
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
import zipfile

from scene_data import normalize_scene, normalize_navigation

MODEL_TYPES = {'glb','gltf','obj','ply','spz','splat'}
RELATED_TYPES = MODEL_TYPES | {'bin','png','jpg','jpeg','webp','avif','mtl'}


def safe_path(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or '\0' in value:
        raise ValueError('ชื่อไฟล์ใน ZIP ไม่ถูกต้อง')
    path=PurePosixPath(value)
    if path.is_absolute() or any(part in ('..','.') for part in value.split('/')):
        raise ValueError('ZIP มีเส้นทางไฟล์ไม่ถูกต้อง')
    return str(path)


def resolve_reference(owner,uri):
    if not isinstance(uri,str) or urlsplit(uri).scheme or urlsplit(uri).netloc or '?' in uri or '#' in uri:
        raise ValueError('ไฟล์ประกอบต้องเป็น relative path ภายใน ZIP')
    decoded=unquote(uri)
    if decoded.startswith('/') or '\\' in decoded or ':' in decoded:
        raise ValueError('ไฟล์ประกอบอ้างอิงนอก ZIP')
    return safe_path(posixpath.normpath(posixpath.join(posixpath.dirname(owner),decoded)))


def mtl_references(text):
    options={'-s':3,'-o':3,'-t':3,'-mm':2,'-bm':1,'-clamp':1,'-blendu':1,'-blendv':1,'-imfchan':1,'-type':1,'-texres':1}
    for line in text.splitlines():
        tokens=line.split()
        if not tokens or tokens[0].lower() not in ('map_kd','map_ks','map_ke','map_d','map_bump','bump','norm','disp','decal'):continue
        tokens=tokens[1:]
        while tokens and tokens[0].startswith('-'):
            option=tokens.pop(0)
            if option not in options:raise ValueError('ตัวเลือก MTL ไม่รองรับ: '+option)
            del tokens[:options[option]]
        if tokens:yield ' '.join(tokens)


def prepare(source,folder,validate,max_bytes):
    """Extract only model data referenced by the preview; bundled code is ignored."""
    with zipfile.ZipFile(source) as archive:
        infos=archive.infolist()
        if len(infos)>4096:raise ValueError('ZIP มีไฟล์มากเกินไป')
        files={};total=0
        for info in infos:
            relative=safe_path(info.filename.rstrip('/'))
            if relative in files:raise ValueError('ZIP มีชื่อไฟล์ซ้ำ')
            if info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16):raise ValueError('ZIP ที่เข้ารหัสหรือมี symlink ไม่รองรับ')
            if info.is_dir():continue
            if info.file_size>max_bytes:raise ValueError('ไฟล์ภายใน ZIP ใหญ่เกินเพดานอัปโหลด')
            total+=info.file_size
            if total>max_bytes*2:raise ValueError('ขนาดข้อมูลหลังคลาย ZIP ใหญ่เกินไป')
            files[relative]=info
        def read_json(filename,maximum=2*1024*1024):
            if files[filename].file_size>maximum:raise ValueError('JSON ใน ZIP ใหญ่เกินไป')
            return json.loads(archive.read(files[filename]))
        # Scene descriptions are optional. A normal ZIP needs no portal manifest.
        scene=None;catalog=[]
        if 'scene.json' in files:
            try:
                descriptor=read_json('scene.json')
                if isinstance(descriptor,dict) and isinstance(descriptor.get('scene'),dict) and isinstance(descriptor.get('assets'),list):
                    scene=normalize_scene(descriptor['scene'])
                    for item in descriptor['assets']:
                        catalog.append({'kind':item['kind'],'ref':item['ref'],'path':resolve_reference('scene.json',item['url'])})
            except (ValueError,TypeError,KeyError,AttributeError):scene=None;catalog=[]
        if scene:
            keys={(item['kind'],item['ref']):item for item in catalog}
            needed={(item['asset']['kind'],item['asset']['ref']) for item in scene['objects']}
            if not needed<=keys.keys():raise ValueError('ฉากอ้างอิงโมเดลที่ไม่อยู่ใน ZIP')
            catalog=[keys[key] for key in sorted(needed)]
            composition=scene['objects']
        else:
            roots=[filename for filename in files if filename.rsplit('.',1)[-1].lower() in MODEL_TYPES and not any(part.startswith('.') or part=='__MACOSX' for part in PurePosixPath(filename).parts)]
            catalog=[{'kind':filename.rsplit('.',1)[-1].lower(),'ref':filename,'path':filename} for filename in sorted(roots)]
            composition=[{'id':f'file-{index}','name':PurePosixPath(item['path']).stem,'asset':{'kind':item['kind'],'ref':item['ref']},
                          'position':[0,0,0],'rotation':[0,0,0],'scale':1,'visible':True} for index,item in enumerate(catalog)]
        if not 1<=len(catalog)<=128:raise ValueError('ZIP ต้องมีโมเดลที่รองรับ 1–128 ไฟล์')
        extracted=set();metadata={};manifest=read_json('manifest.json',8*1024*1024).get('files',{}) if scene and 'manifest.json' in files else {}
        def extract(relative):
            relative=safe_path(relative)
            if relative in extracted:return
            if relative not in files:raise ValueError('ไม่พบไฟล์ประกอบ: '+relative)
            extension=relative.rsplit('.',1)[-1].lower()
            if extension not in RELATED_TYPES:raise ValueError('ไฟล์ประกอบชนิดนี้ไม่รองรับ: '+relative)
            target=folder/'assets'/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            digest=hashlib.sha256()
            with archive.open(files[relative]) as src,target.open('xb') as dst:
                while block:=src.read(1024*1024):dst.write(block);digest.update(block)
            if relative in manifest and (digest.hexdigest()!=manifest[relative]['sha256'] or target.stat().st_size!=manifest[relative]['bytes']):
                raise ValueError('Checksum ของไฟล์ไม่ตรง: '+relative)
            extracted.add(relative)
            if extension in MODEL_TYPES:
                metadata[relative]=validate(target,allow_external=True)
            if extension in ('gltf','glb'):
                if extension=='gltf':document=json.loads(target.read_bytes())
                else:
                    with target.open('rb') as stream:
                        stream.seek(12);length=int.from_bytes(stream.read(4),'little');stream.read(4);document=json.loads(stream.read(length))
                for value in document.get('buffers',[])+document.get('images',[]):
                    uri=value.get('uri')
                    if uri and not uri.startswith('data:'):extract(resolve_reference(relative,uri))
            elif extension=='obj':
                for uri in metadata[relative].get('material_libraries',[]):extract(resolve_reference(relative,uri))
            elif extension=='mtl':
                if target.stat().st_size>4*1024*1024:raise ValueError('MTL ใหญ่เกินไป')
                for uri in mtl_references(target.read_text(encoding='utf-8-sig')):extract(resolve_reference(relative,uri))
        for item in catalog:extract(item['path'])
        assets=[{**item,'bytes':files[item['path']].file_size,**{key:value for key,value in metadata[item['path']].items() if key!='scene'}} for item in catalog]
        if not scene and len(catalog)==1:scene=metadata[catalog[0]['path']].get('scene')
        if 'navigation.json' in files:
            try:scene=normalize_navigation(read_json('navigation.json'))
            except (ValueError,TypeError,KeyError,AttributeError):pass
        gaussian_glb=any(metadata[item['path']].get('points') and item['kind'] in ('gltf','glb') for item in catalog)
        spark=any(item['kind'] in ('spz','splat') or metadata[item['path']].get('representation')=='gaussian' and item['kind']=='ply' for item in catalog)
        if gaussian_glb and spark:raise ValueError('ZIP ที่ผสม Gaussian GLB กับ SPZ/PLY Gaussian ยังเปิดร่วมกันไม่ได้ กรุณาแยกเป็นสองรายการ')
        return {'scene':scene,'composition':composition,'assets':assets,'files':sorted(extracted),
                'points':sum(metadata[item['path']].get('points') or 0 for item in catalog) or None,
                'object_count':len(composition),'representation':'scene','renderer':'webgpu' if gaussian_glb else 'webgl'}


def remove_folder(folder,root):
    resolved=folder.resolve();root=root.resolve()
    if resolved.parent!=root or not re.fullmatch('[0-9a-f]{32}',resolved.name):raise ValueError('Invalid data directory')
    if resolved.is_dir():shutil.rmtree(resolved)
