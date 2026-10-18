"""Start an isolated HTTP listener, never a pipeline task or a GPU workload."""
from pathlib import Path
import sys
import tempfile
import threading
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.web import server as app

with tempfile.TemporaryDirectory(prefix='app-install-smoke-') as temp:
    root = Path(temp)
    for name in ('JOBS','MODELS','RAW','UPLOADS','PREVIEWS'):
        directory = root/name.lower(); directory.mkdir(); setattr(app, name, directory)
    app.SOURCE_DIRECTORIES = {'raw': app.RAW, 'uploads': app.UPLOADS}
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        for path in ('/', '/sources', '/jobs', '/models', '/assets/job_logs.mjs'):
            with urlopen(f'http://127.0.0.1:{server.server_port}'+path, timeout=10) as response:
                assert response.status == 200, path
        print('HTTP startup passed: Dashboard / Sources / Jobs / Models / assets; no jobs started')
    finally:
        server.shutdown(); server.server_close(); thread.join()
