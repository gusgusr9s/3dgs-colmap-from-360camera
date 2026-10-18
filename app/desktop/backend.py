"""Desktop-only backend bootstrap with an external workspace and an OS lease."""
import argparse
import json
import os
from pathlib import Path
import shutil


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--state-file',type=Path,required=True)
    parser.add_argument('--instance',required=True)
    args=parser.parse_args()
    workspace=args.workspace.expanduser().resolve();workspace.mkdir(parents=True,exist_ok=True)
    os.environ['STUDIO_WORKSPACE']=str(workspace)
    # App imports happen only after the desktop workspace is selected.
    from app.paths import ROOT
    from app.jobs.processing_queue import try_lock
    from app.desktop.gateway import DesktopGateway
    lease=(workspace/'.studio-desktop.lock').open('a+b')
    if not lease.tell():lease.write(b'0');lease.flush()
    if not try_lock(lease):raise RuntimeError('Workspace นี้เปิดอยู่ใน Desktop อีกหน้าต่างแล้ว')
    engine_paths=[ROOT/'tools/ffmpeg/bin',ROOT/'tools/node/bin',ROOT/'tools/colmap/bin',ROOT/'tools/brush']
    os.environ['PATH']=os.pathsep.join([*map(str,engine_paths),os.environ.get('PATH','')])
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    os.environ['TORCH_HOME']=str(workspace/'web_data/cache/torch')
    for source in (ROOT/'tools/mask_weights').glob('*.pth'):
        target=Path(os.environ['TORCH_HOME'])/'hub/checkpoints'/source.name
        if not target.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    from app.web import server as app
    gateway=DesktopGateway(os.environ.get('STUDIO_SESSION_TOKEN',''),args.instance,workspace)
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    server.desktop_gateway=gateway
    state={'schema':1,'pid':os.getpid(),'port':server.server_port,'instance':args.instance,
           'workspace':str(workspace),'cookie':gateway.cookie}
    args.state_file.parent.mkdir(parents=True,exist_ok=True)
    app.write_json(args.state_file,state)
    try:
        print(json.dumps({'desktop_backend':state}),flush=True)
        server.serve_forever()
    finally:
        server.server_close();lease.close()


if __name__=='__main__':main()
