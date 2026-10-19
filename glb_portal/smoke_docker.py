"""Run against a local Compose service with .env set to http://127.0.0.1:8088."""
import json
import secrets
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8088"


def docker(*args, input_text=None):
    return subprocess.run(["docker", "compose", *args], input=input_text, text=True, check=True, capture_output=True)


def http(path, method="GET", data=None, cookie=None, csrf=None, filename=None, client_ip=None):
    headers = {"Origin": BASE}
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if filename:
        headers["X-Filename"] = filename
    if client_ip:
        headers["X-Real-IP"] = client_ip
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    request = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def ready():
    for _ in range(30):
        try:
            if http("/api/me")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(1)
    raise AssertionError("container did not become ready")


def cleanup(user=None):
    code = """import json,sys,server
user=json.load(sys.stdin)
db=server.connect()
names=[user] if user else [r['username'] for r in db.execute("SELECT username FROM users WHERE username LIKE 'smoke_%'")]
for name in names:
    rows=db.execute('SELECT id,filename FROM models WHERE uploaded_by=?',(name,)).fetchall()
    for row in rows:
        if server.MODEL_ID.fullmatch(row['id']) and row['filename'] in (row['id']+'.glb', row['id']+'.spz'):
            (server.DATA/'models'/row['filename']).unlink(missing_ok=True)
    db.execute('DELETE FROM models WHERE uploaded_by=?',(name,))
    db.execute('DELETE FROM sessions WHERE username=?',(name,))
    db.execute('DELETE FROM login_log WHERE username=?',(name,))
    db.execute('DELETE FROM users WHERE username=?',(name,))
db.commit(); db.close()
print('Removed',len(names),'temporary account(s)')"""
    print(docker("exec", "-T", "portal", "python", "-c", code, input_text=json.dumps(user)).stdout.strip())


def main():
    ready()
    username = "smoke_" + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    setup = "import json,sys,server; x=json.load(sys.stdin); server.init_db(); db=server.connect(); db.execute('INSERT INTO users VALUES(?,?,?,?)',(x['user'],server.password_hash(x['password']),'admin',server.now())); db.commit(); db.close()"
    docker("exec", "-T", "portal", "python", "-c", setup, input_text=json.dumps({"user": username, "password": password}))
    cookie = csrf = model_id = spz_id = None
    try:
        assert http("/")[0] == 200
        assert http("/app.js")[0] == 200
        assert http("/api/models")[0] == 401
        status, headers, _ = http("/api/login", "POST", {"username": username, "password": password}, client_ip="198.51.100.42")
        assert status == 200, f"login returned {status}"
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf = json.loads(http("/api/me", cookie=cookie)[2])["csrf"]
        payload = json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0}).encode()
        payload += b" " * ((-len(payload)) % 4)
        glb = struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) + struct.pack("<I4s", len(payload), b"JSON") + payload
        status, _, body = http("/api/models", "POST", glb, cookie, csrf, "smoke.glb")
        assert status == 201, body
        model_id = json.loads(body)["id"]
        assert http(f"/api/models/{model_id}/file")[0] == 401
        assert http(f"/api/models/{model_id}/file", cookie=cookie)[2] == glb
        spz = (Path(__file__).parent / "test_splat.spz").read_bytes()
        status, _, body = http("/api/models", "POST", spz, cookie, csrf, "smoke.spz")
        assert status == 201, body
        spz_id = json.loads(body)["id"]
        assert http(f"/api/models/{spz_id}/file")[0] == 401
        assert http(f"/api/models/{spz_id}/file", cookie=cookie)[2] == spz
        print("GLB/SPZ upload, private download and login: OK")

        docker("up", "-d", "--force-recreate", "--no-build", "portal")
        ready()
        models = json.loads(http("/api/models?project=default", cookie=cookie)[2])["models"]
        assert any(model["id"] == model_id for model in models)
        assert any(model["id"] == spz_id and model["format"] == "spz" for model in models)
        assert http(f"/api/models/{model_id}/file", cookie=cookie)[2] == glb
        assert http(f"/api/models/{spz_id}/file", cookie=cookie)[2] == spz
        print("Both models and session after container recreation: OK")

        events = json.loads(http("/api/login-log", cookie=cookie)[2])["events"]
        assert any(event["username"] == username and event["success"] and event["ip"] == "198.51.100.42" for event in events)
        print("Login audit log: OK")
    finally:
        cleanup(username)


if __name__ == "__main__":
    try:
        cleanup() if "--cleanup-orphans" in sys.argv else main()
    except Exception as error:
        print(f"Docker smoke test failed: {error}", file=sys.stderr)
        raise
