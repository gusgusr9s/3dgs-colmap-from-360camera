"""Project organization, portable imports, previews and saved camera views."""
import json
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import re
import sqlite3
import struct
import threading
import time
from urllib.parse import unquote, quote
import uuid
import zlib
import zipfile

from scene_data import name, normalize_viewpoints, summary
from scene_imports import prepare, remove_folder, safe_path

IDENTIFIER = r'[0-9a-f]{32}'
_lock = threading.RLock()


def cleanup_staging(root):
    """Discard abandoned previews after a day; committed scenes are separate."""
    folder=root/'imports'
    if not folder.exists():return
    with _lock:
        for item in folder.iterdir():
            if not re.fullmatch(IDENTIFIER,item.name) or not item.is_dir():continue
            try:
                if item.stat().st_mtime<time.time()-86400:remove_folder(item,folder)
            except FileNotFoundError:pass


def model_record(row,root):
    value={key:row[key] for key in ('id','name','bytes','uploaded_by','uploaded_at','project_id')}
    value['format']=Path(row['filename']).suffix[1:].lower()
    value['metadata']=summary(json.loads(row['metadata']))
    value['thumbnail']=(root/'thumbnails'/(row['id']+'.png')).is_file()
    return value


def remove_model_files(root,row):
    identifier=row['id']
    if not re.fullmatch(IDENTIFIER,identifier):raise ValueError('Invalid model ID')
    target=root/'models'/row['filename']
    if target.resolve().parent!=(root/'models').resolve():raise ValueError('Invalid model path')
    target.unlink(missing_ok=True)
    (root/'thumbnails'/(identifier+'.png')).unlink(missing_ok=True)
    remove_folder(root/'scenes'/identifier,root/'scenes')


class PortalAPI:
    def __init__(self,handler,root,connect,validate,max_upload):
        self.h,self.root,self.connect,self.validate,self.max_upload=handler,root,connect,validate,max_upload

    def handle(self,method,path):
        matched=(method=='GET' and re.fullmatch(r'/api/models/'+IDENTIFIER+r'/(details|thumbnail|assets/.+)',path)
                 or method=='POST' and (path in ('/api/imports','/api/models/batch') or re.fullmatch(r'/api/imports/'+IDENTIFIER+r'/commit',path) or re.fullmatch(r'/api/models/'+IDENTIFIER+r'/thumbnail',path))
                 or method=='PATCH' and (re.fullmatch(r'/api/projects/(default|'+IDENTIFIER+r')',path) or re.fullmatch(r'/api/models/'+IDENTIFIER+r'/viewpoints',path))
                 or method=='DELETE' and re.fullmatch(r'/api/imports/'+IDENTIFIER,path))
        if not matched:return False
        session=self.h.require_session() if method=='GET' else self.h.require_admin()
        if not session:return True
        if method!='GET' and not self.h.csrf_ok(session):self.h.fail(403,'CSRF token ไม่ถูกต้อง');return True
        try:
            if method=='GET':self.get(path)
            elif method=='POST':self.post(path,session)
            elif method=='PATCH':self.patch(path)
            else:
                folder=self.stage(path.rsplit('/',1)[1],session)
                with _lock:remove_folder(folder,self.root/'imports')
                self.h.json({'ok':True})
        except ConnectionError:pass
        except FileNotFoundError:self.h.fail(404,'ไม่พบไฟล์หรือรายการนี้')
        except sqlite3.IntegrityError:self.h.fail(409,'ชื่อ Project นี้มีแล้ว')
        except (ValueError,TypeError,KeyError,AttributeError,IndexError,RecursionError,struct.error,UnicodeError,zipfile.BadZipFile,OSError) as error:
            self.h.fail(400,str(error) or 'ข้อมูลไม่ถูกต้อง')
        return True

    def model(self,identifier):
        with self.connect() as db:row=db.execute('SELECT * FROM models WHERE id=?',(identifier,)).fetchone()
        if row is None:raise FileNotFoundError()
        return row

    def details(self,row):
        metadata=json.loads(row['metadata'])
        if not metadata and not row['filename'].endswith('.zip'):
            metadata=self.validate(self.root/'models'/row['filename'])
            with self.connect() as db:db.execute('UPDATE models SET metadata=? WHERE id=? AND metadata=\'{}\'',(json.dumps(metadata,allow_nan=False),row['id']))
        assets=[{**item,'url':f"/api/models/{row['id']}/assets/"+quote(item['path'],safe='/')} for item in metadata.get('assets',[])]
        return {'metadata':summary(metadata),'scene':metadata.get('scene'),'composition':metadata.get('composition',[]),
                'assets':assets,'renderer':metadata.get('renderer'),
                'viewpoints':json.loads(row['viewpoints']),'viewpoints_revision':row['viewpoints_revision']}

    def get(self,path):
        parts=path.split('/');row=self.model(parts[3]);kind=parts[4]
        if kind=='details':return self.h.json(self.details(row))
        if kind=='thumbnail':return self.h.file(self.root/'thumbnails'/(row['id']+'.png'),'image/png',True)
        relative=safe_path(unquote('/'.join(parts[5:])))
        metadata=json.loads(row['metadata'])
        if relative not in metadata.get('files',[]):raise FileNotFoundError()
        target=self.root/'scenes'/row['id']/'assets'/relative
        if not target.resolve().is_relative_to((self.root/'scenes'/row['id']/'assets').resolve()):raise ValueError('Invalid asset path')
        mime=mimetypes.guess_type(relative)[0] or 'application/octet-stream'
        if target.suffix.lower() in ('.obj','.mtl'):mime='text/plain; charset=utf-8'
        if target.suffix.lower()=='.gltf':mime='model/gltf+json'
        return self.h.file(target,mime,True)

    def stage(self,identifier,session):
        folder=self.root/'imports'/identifier
        record=json.loads((folder/'record.json').read_text(encoding='utf8'))
        if record['owner']!=session['username']:raise FileNotFoundError()
        return folder

    def post(self,path,session):
        if path=='/api/imports':return self.create_import(session)
        if path=='/api/models/batch':return self.batch()
        if path.endswith('/commit'):
            with _lock:return self.commit(path.split('/')[3],session)
        row=self.model(path.split('/')[3])
        length=int(self.h.headers.get('Content-Length','0'))
        if not 33<=length<=512*1024:raise ValueError('ภาพปกต้องเป็น PNG ไม่เกิน 512 KB')
        data=self.h.rfile.read(length)
        if len(data)!=length or data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('ภาพปก PNG ไม่ถูกต้อง')
        width,height=struct.unpack('>II',data[16:24])
        if not 1<=width<=1024 or not 1<=height<=1024 or data[12:16]!=b'IHDR':raise ValueError('ขนาดภาพปกไม่ถูกต้อง')
        offset=8;ended=False
        while offset+12<=length:
            size=int.from_bytes(data[offset:offset+4],'big');end=offset+12+size
            if end>length or zlib.crc32(data[offset+4:end-4])&0xffffffff!=int.from_bytes(data[end-4:end],'big'):raise ValueError('ข้อมูล PNG ไม่ครบ')
            if data[offset+4:offset+8]==b'IEND':ended=end==length;break
            offset=end
        if not ended:raise ValueError('ข้อมูล PNG ไม่ครบ')
        target=self.root/'thumbnails'/(row['id']+'.png');temporary=target.with_name(uuid.uuid4().hex+'.tmp')
        try:temporary.write_bytes(data);temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
        self.h.json({'thumbnail':True})

    def create_import(self,session):
        cleanup_staging(self.root)
        length=int(self.h.headers.get('Content-Length','0'))
        if not 20<=length<=self.max_upload:return self.h.fail(413,'ZIP ใหญ่เกินเพดานอัปโหลด')
        identifier=uuid.uuid4().hex;folder=self.root/'imports'/identifier;folder.mkdir(parents=True)
        try:
            source=folder/'source.zip'
            with source.open('xb') as output:
                remaining=length
                while remaining:
                    block=self.h.rfile.read(min(1024*1024,remaining))
                    if not block:raise ValueError('อัปโหลด ZIP ไม่ครบ')
                    output.write(block);remaining-=len(block)
            metadata=prepare(source,folder,self.validate,self.max_upload)
            record={'owner':session['username'],'metadata':metadata,'created':time.time()}
            (folder/'record.json').write_text(json.dumps(record,ensure_ascii=False,allow_nan=False),encoding='utf8')
            self.h.json({'id':identifier,'metadata':summary(metadata),
                         'objects':[{'name':item['name'],'position':item['position'],'rotation':item['rotation'],'scale':item['scale']} for item in metadata['composition']],
                         'files':[{'path':item['path'],'kind':item['kind'],'bytes':item['bytes']} for item in metadata['assets']]},201)
        except Exception:
            remove_folder(folder,self.root/'imports')
            raise

    def commit(self,identifier,session):
        folder=self.stage(identifier,session);record=json.loads((folder/'record.json').read_text(encoding='utf8'))
        data=self.h.body_json();project_id=data['project_id'];display=name(data['name'])
        if not isinstance(project_id,str) or not re.fullmatch(r'default|'+IDENTIFIER,project_id):raise ValueError('Project ไม่ถูกต้อง')
        model_id=uuid.uuid4().hex;target=self.root/'scenes'/model_id;source=self.root/'models'/(model_id+'.zip')
        try:
            with self.connect() as db:
                if not db.execute('SELECT 1 FROM projects WHERE id=?',(project_id,)).fetchone():raise FileNotFoundError()
                size=(folder/'source.zip').stat().st_size
                (folder/'source.zip').replace(source);folder.replace(target)
                db.execute('INSERT INTO models(id,name,filename,bytes,uploaded_by,uploaded_at,project_id,metadata) VALUES(?,?,?,?,?,?,?,?)',
                           (model_id,display,model_id+'.zip',size,session['username'],datetime.now(timezone.utc).isoformat(timespec='seconds'),project_id,json.dumps(record['metadata'],allow_nan=False)))
            (target/'record.json').unlink(missing_ok=True)
        except Exception:
            source.unlink(missing_ok=True);remove_folder(target,self.root/'scenes');raise
        self.h.json({'id':model_id,'name':display,'format':'zip','project_id':project_id},201)

    def batch(self):
        data=self.h.body_json();identifiers=data['ids'];action=data['action']
        if not isinstance(identifiers,list) or not 1<=len(identifiers)<=256 or any(not isinstance(item,str) or not re.fullmatch(IDENTIFIER,item) for item in identifiers):raise ValueError('เลือกรายการ 1–256 รายการ')
        identifiers=list(dict.fromkeys(identifiers));parameters=','.join('?' for _ in identifiers)
        with self.connect() as db:
            rows=db.execute(f'SELECT * FROM models WHERE id IN ({parameters})',identifiers).fetchall()
            if len(rows)!=len(identifiers):raise FileNotFoundError()
            if action=='move':
                project=data['project_id']
                if not isinstance(project,str) or not db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():raise FileNotFoundError()
                db.execute(f'UPDATE models SET project_id=? WHERE id IN ({parameters})',[project,*identifiers])
            elif action=='delete':db.execute(f'DELETE FROM models WHERE id IN ({parameters})',identifiers)
            else:raise ValueError('คำสั่งไม่ถูกต้อง')
        if action=='delete':
            for row in rows:remove_model_files(self.root,row)
        self.h.json({'ok':True,'ids':identifiers,'action':action})

    def patch(self,path):
        data=self.h.body_json()
        if path.startswith('/api/projects/'):
            identifier=path.rsplit('/',1)[1];display=name(data['name'],'ชื่อ Project')
            with self.connect() as db:
                if not db.execute('SELECT 1 FROM projects WHERE id=?',(identifier,)).fetchone():raise FileNotFoundError()
                db.execute('UPDATE projects SET name=? WHERE id=?',(display,identifier))
            return self.h.json({'id':identifier,'name':display})
        identifier=path.split('/')[3];items=normalize_viewpoints(data['viewpoints']);revision=data['revision']
        if isinstance(revision,bool) or not isinstance(revision,int) or not 0<=revision<2**53:raise ValueError('รุ่นข้อมูลจุดนำทางไม่ถูกต้อง')
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM models WHERE id=?',(identifier,)).fetchone():raise FileNotFoundError()
            result=db.execute('UPDATE models SET viewpoints=?,viewpoints_revision=viewpoints_revision+1 WHERE id=? AND viewpoints_revision=?',(json.dumps(items,ensure_ascii=False,allow_nan=False),identifier,revision))
            if not result.rowcount:return self.h.fail(409,'จุดนำทางถูกแก้จากหน้าต่างอื่น กรุณาปิดแล้วเปิดรายการใหม่')
        self.h.json({'viewpoints':items,'viewpoints_revision':revision+1})
