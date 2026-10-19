"""Small, standalone, password protected GLB gallery. Python 3.11+ only."""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import struct
import time
import uuid
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("GLB_PORTAL_DATA", ROOT / "data")).resolve()
DB = DATA / "portal.sqlite3"
MAX_UPLOAD = int(os.environ.get("GLB_PORTAL_MAX_MB", "1024")) * 1024 * 1024
MAX_GLB_JSON = 16 * 1024 * 1024
SESSION_AGE = 7 * 86400
MIN_PASSWORD_LENGTH = 5
USERNAME = re.compile(r"^[a-zA-Z0-9_.-]{3,40}$")
MODEL_ID = re.compile(r"^[0-9a-f]{32}$")
PROJECT_ID = re.compile(r"^(default|[0-9a-f]{32})$")
DEFAULT_PROJECT_ID = "default"
ASSETS = {"/style.css": "style.css", "/project.css": "project.css", "/app.js": "app.js", "/viewer.js": "viewer.js"}
VENDOR = ROOT / "vendor" / "three"
IMPORTMAP = re.search(rb'<script type="importmap">([^<]+)</script>', (ROOT / "index.html").read_bytes()).group(1)
IMPORTMAP_HASH = base64.b64encode(hashlib.sha256(IMPORTMAP).digest()).decode()


class PortalConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    db = sqlite3.connect(DB, timeout=30, factory=PortalConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "models").mkdir(exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','viewer')), created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
                csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, filename TEXT NOT NULL,
                bytes INTEGER NOT NULL, uploaded_by TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT 'default' REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS login_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                username TEXT NOT NULL, ip TEXT NOT NULL, success INTEGER NOT NULL,
                reason TEXT NOT NULL, user_agent TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS login_log_at ON login_log(at);
        """)
        db.execute("INSERT OR IGNORE INTO projects VALUES(?,?,?,?)", (DEFAULT_PROJECT_ID, "ทั่วไป", "system", now()))
        # Preserve uploads made before projects were introduced.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(models)")}
        if "project_id" not in columns:
            db.execute("ALTER TABLE models ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS projects_name_ci ON projects(name COLLATE NOCASE)")
        db.execute("CREATE INDEX IF NOT EXISTS models_project_uploaded ON models(project_id, uploaded_at DESC)")


def password_hash(password):
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return "pbkdf2_sha256$310000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def check_password(password, stored):
    try:
        method, iterations, salt, key = stored.split("$")
        if method != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, base64.b64decode(key))
    except (ValueError, TypeError):
        return False


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Handler(BaseHTTPRequestHandler):
    server_version = "GLBPortal/1.0"

    def log_message(self, fmt, *args):
        # HTTP access log stays on stdout; authentication events are in SQLite.
        message = fmt % args
        safe_message = re.sub(r"[\x00-\x1f\x7f-\x9f]", lambda match: f"\\x{ord(match.group()):02x}", message)
        print(f"{self.address_string()} {safe_message}", flush=True)

    def respond(self, status, body=b"", content_type="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self' 'sha256-{IMPORTMAP_HASH}'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def fail(self, status, message):
        self.respond(status, json.dumps({"error": message}, ensure_ascii=False), "application/json; charset=utf-8")

    def json(self, value, status=200, extra=None):
        self.respond(status, json.dumps(value, ensure_ascii=False), "application/json; charset=utf-8", extra)

    def session(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookies["portal_session"].value
            if not re.fullmatch(r"[0-9a-f]{64}", token):
                return None
        except (KeyError, ValueError):
            return None
        with connect() as db:
            return db.execute("SELECT s.username,s.csrf,u.role FROM sessions s JOIN users u USING(username) WHERE s.token_hash=? AND s.expires_at>?", (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()

    def origin_ok(self):
        origin = self.headers.get("Origin")
        expected = os.environ.get("GLB_PORTAL_ORIGIN")
        if not origin:
            return False
        if expected:
            return origin.rstrip("/") == expected.rstrip("/")
        return urlsplit(origin).netloc == self.headers.get("Host") and urlsplit(origin).scheme in ("http", "https")

    def client_ip(self):
        if os.environ.get("GLB_PORTAL_TRUST_PROXY") == "1":
            forwarded = self.headers.get("X-Real-IP", "")
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return self.client_address[0]

    def csrf_ok(self, session):
        return self.origin_ok() and hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"])

    def require_session(self):
        session = self.session()
        if not session:
            self.fail(401, "กรุณาเข้าสู่ระบบ")
        return session

    def require_admin(self):
        session = self.require_session()
        if session and session["role"] != "admin":
            self.fail(403, "เฉพาะผู้ดูแลระบบ")
            return None
        return session

    def body_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def file(self, path, content_type, private=False):
        if private and not self.require_session():
            return
        try:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "private, no-store" if private else "public, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self' 'sha256-{IMPORTMAP_HASH}'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            if self.command != "HEAD":
                with path.open("rb") as src:
                    while chunk := src.read(1024 * 1024):
                        self.wfile.write(chunk)
        except FileNotFoundError:
            self.fail(404, "ไม่พบไฟล์")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/login"):
            return self.file(ROOT / "index.html", "text/html; charset=utf-8")
        if path in ASSETS:
            mime = "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8"
            return self.file(ROOT / ASSETS[path], mime)
        if path.startswith("/vendor/three/"):
            rel = path.removeprefix("/vendor/three/")
            if rel not in VENDOR_FILES:
                return self.fail(404, "ไม่พบไฟล์")
            return self.file(VENDOR / rel, "text/javascript; charset=utf-8")
        if path == "/api/me":
            session = self.session()
            return self.json({"user": session["username"], "role": session["role"], "csrf": session["csrf"]} if session else {"user": None})
        if path == "/api/projects":
            if not self.require_session():
                return
            with connect() as db:
                rows = db.execute("SELECT p.id,p.name,COUNT(m.id) AS model_count FROM projects p LEFT JOIN models m ON m.project_id=p.id GROUP BY p.id ORDER BY p.created_at,p.name").fetchall()
            return self.json({"projects": [dict(row) for row in rows]})
        if path == "/api/models":
            if not self.require_session():
                return
            project_id = parse_qs(urlsplit(self.path).query).get("project", [DEFAULT_PROJECT_ID])[0]
            if not PROJECT_ID.fullmatch(project_id):
                return self.fail(400, "Project ไม่ถูกต้อง")
            with connect() as db:
                exists = db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
                if not exists:
                    return self.fail(404, "ไม่พบ Project")
                rows = db.execute("SELECT id,name,bytes,uploaded_by,uploaded_at,project_id FROM models WHERE project_id=? ORDER BY uploaded_at DESC,id DESC", (project_id,)).fetchall()
            return self.json({"models": [dict(row) for row in rows]})
        if path == "/api/login-log":
            if not self.require_admin():
                return
            with connect() as db:
                rows = db.execute("SELECT at,username,ip,success,reason,user_agent FROM login_log ORDER BY id DESC LIMIT 200").fetchall()
            return self.json({"events": [dict(row) for row in rows]})
        match = re.fullmatch(r"/api/models/([0-9a-f]{32})/file", path)
        if match:
            if not self.require_session():
                return
            with connect() as db:
                row = db.execute("SELECT filename FROM models WHERE id=?", (match[1],)).fetchone()
            return self.file(DATA / "models" / row["filename"], "model/gltf-binary", True) if row else self.fail(404, "ไม่พบโมเดล")
        self.fail(404, "ไม่พบหน้า")

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/login":
            if not self.origin_ok():
                return self.fail(403, "Origin ไม่ถูกต้อง")
            data = self.body_json()
            if not isinstance(data, dict):
                return self.fail(400, "ข้อมูลไม่ถูกต้อง")
            username = str(data.get("username", ""))[:100]
            password = str(data.get("password", ""))[:1024]
            ip = self.client_ip()
            with connect() as db:
                since = datetime.fromtimestamp(time.time() - 900, timezone.utc).isoformat(timespec="seconds")
                failures = db.execute("SELECT COUNT(*) FROM login_log WHERE ip=? AND success=0 AND at>?", (ip, since)).fetchone()[0]
                row = db.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
                success = failures < 10 and row is not None and check_password(password, row["password_hash"])
                reason = "success" if success else ("rate_limited" if failures >= 10 else "invalid_credentials")
                db.execute("INSERT INTO login_log(at,username,ip,success,reason,user_agent) VALUES(?,?,?,?,?,?)", (now(), username, ip, int(success), reason, self.headers.get("User-Agent", "")[:300]))
                if success:
                    token = secrets.token_hex(32)
                    csrf = secrets.token_hex(32)
                    db.execute("DELETE FROM sessions WHERE expires_at<?", (int(time.time()),))
                    db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), username, csrf, int(time.time()) + SESSION_AGE))
            if not success:
                return self.fail(429 if failures >= 10 else 401, "ลองใหม่ภายหลัง" if failures >= 10 else "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
            cookie = f"portal_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_AGE}"
            if os.environ.get("GLB_PORTAL_SECURE_COOKIE") == "1":
                cookie += "; Secure"
            return self.json({"ok": True}, extra={"Set-Cookie": cookie})
        if path == "/api/logout":
            session = self.require_session()
            if not session:
                return
            if not self.csrf_ok(session):
                return self.fail(403, "CSRF token ไม่ถูกต้อง")
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(cookies["portal_session"].value.encode()).hexdigest(),))
            return self.json({"ok": True}, extra={"Set-Cookie": "portal_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})
        if path == "/api/projects":
            session = self.require_admin()
            if not session:
                return
            if not self.csrf_ok(session):
                return self.fail(403, "CSRF token ไม่ถูกต้อง")
            data = self.body_json()
            if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                return self.fail(400, "กรุณาระบุชื่อ Project")
            name = data["name"].strip()
            if not 1 <= len(name) <= 80:
                return self.fail(400, "ชื่อ Project ต้องยาว 1–80 ตัวอักษร")
            with connect() as db:
                if db.execute("SELECT 1 FROM projects WHERE lower(name)=lower(?)", (name,)).fetchone():
                    return self.fail(409, "ชื่อ Project นี้มีแล้ว")
                project_id = uuid.uuid4().hex
                db.execute("INSERT INTO projects VALUES(?,?,?,?)", (project_id, name, session["username"], now()))
            return self.json({"id": project_id, "name": name}, 201)
        if path == "/api/models":
            session = self.require_admin()
            if not session:
                return
            if not self.csrf_ok(session):
                return self.fail(403, "CSRF token ไม่ถูกต้อง")
            project_id = self.headers.get("X-Project-Id", DEFAULT_PROJECT_ID)
            if not PROJECT_ID.fullmatch(project_id):
                return self.fail(400, "Project ไม่ถูกต้อง")
            with connect() as db:
                if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                    return self.fail(404, "ไม่พบ Project")
            self.upload(session, project_id)
            return
        self.fail(404, "ไม่พบหน้า")

    def upload(self, session, project_id):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 20 <= length <= MAX_UPLOAD:
            return self.fail(413, f"ไฟล์ต้องมีขนาด 20 bytes ถึง {MAX_UPLOAD // 1048576} MB")
        name = unquote(self.headers.get("X-Filename", "model.glb"))
        name = Path(name.replace("\\", "/")).name.strip()[:120]
        if not name.lower().endswith(".glb") or not name:
            return self.fail(400, "รองรับไฟล์ .glb เท่านั้น")
        model_id = uuid.uuid4().hex
        filename = model_id + ".glb"
        destination = DATA / "models" / filename
        temporary = DATA / "models" / (filename + ".partial")
        try:
            remaining = length
            with temporary.open("xb") as out:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("อัปโหลดไม่ครบ")
                    out.write(chunk)
                    remaining -= len(chunk)
            with temporary.open("rb") as src:
                magic, version, declared = struct.unpack("<4sII", src.read(12))
                json_length, json_type = struct.unpack("<I4s", src.read(8))
                if (magic != b"glTF" or version != 2 or declared != length or json_type != b"JSON"
                        or json_length > length - 20 or json_length > MAX_GLB_JSON):
                    raise ValueError("ไฟล์ GLB ไม่ถูกต้อง")
                payload = json.loads(src.read(json_length).decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("asset", {}).get("version") != "2.0":
                    raise ValueError("ไฟล์ GLB ไม่ถูกต้อง")
                # A standalone GLB must not fetch related files from arbitrary URLs.
                for item in payload.get("buffers", []) + payload.get("images", []):
                    uri = item.get("uri")
                    if uri and not uri.startswith("data:"):
                        raise ValueError("GLB ต้องรวมไฟล์ที่เกี่ยวข้องไว้ภายใน")
            temporary.replace(destination)
            with connect() as db:
                db.execute("INSERT INTO models(id,name,filename,bytes,uploaded_by,uploaded_at,project_id) VALUES(?,?,?,?,?,?,?)", (model_id, name[:-4] or "Model", filename, length, session["username"], now(), project_id))
            return self.json({"id": model_id, "name": name[:-4], "project_id": project_id}, 201)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
            destination.unlink(missing_ok=True)
            return self.fail(400, str(exc))
        finally:
            temporary.unlink(missing_ok=True)

    def do_DELETE(self):
        match = re.fullmatch(r"/api/models/([0-9a-f]{32})", urlsplit(self.path).path)
        if not match:
            return self.fail(404, "ไม่พบโมเดล")
        session = self.require_admin()
        if not session:
            return
        if not self.csrf_ok(session):
            return self.fail(403, "CSRF token ไม่ถูกต้อง")
        with connect() as db:
            row = db.execute("SELECT filename FROM models WHERE id=?", (match[1],)).fetchone()
            if not row:
                return self.fail(404, "ไม่พบโมเดล")
            db.execute("DELETE FROM models WHERE id=?", (match[1],))
        (DATA / "models" / row["filename"]).unlink(missing_ok=True)
        self.json({"ok": True})


VENDOR_FILES = {
    "build/three.core.js", "build/three.module.js", "build/three.webgpu.js", "build/three.tsl.js",
    "examples/jsm/controls/OrbitControls.js", "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/loaders/GLTFGaussianSplatLoaderExtension.js", "examples/jsm/objects/GaussianSplat.js",
    "examples/jsm/utils/GaussianSplatUtils.js", "examples/jsm/utils/BufferGeometryUtils.js",
    "examples/jsm/utils/SkeletonUtils.js", "examples/jsm/gpgpu/CountingSort.js",
}


def main():
    parser = argparse.ArgumentParser(description="Standalone GLB portal")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8088)
    user = commands.add_parser("set-user")
    user.add_argument("username")
    user.add_argument("--role", choices=("admin", "viewer"), default="viewer")
    remove = commands.add_parser("delete-user")
    remove.add_argument("username")
    commands.add_parser("inspect")
    args = parser.parse_args()
    init_db()
    if args.command == "set-user":
        if not USERNAME.fullmatch(args.username):
            parser.error("username must be 3-40 letters, numbers, _, . or -")
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            parser.error(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        if password != getpass.getpass("Confirm password: "):
            parser.error("passwords do not match")
        with connect() as db:
            db.execute("INSERT INTO users VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash,role=excluded.role", (args.username, password_hash(password), args.role, now()))
            db.execute("DELETE FROM sessions WHERE username=?", (args.username,))
        print(f"Saved {args.username} ({args.role})")
    elif args.command == "delete-user":
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE username=?", (args.username,))
            db.execute("DELETE FROM users WHERE username=?", (args.username,))
        print(f"Deleted {args.username}")
    elif args.command == "inspect":
        with connect() as db:
            rows = db.execute("SELECT p.name,COUNT(m.id) AS count FROM projects p LEFT JOIN models m ON m.project_id=p.id GROUP BY p.id ORDER BY p.name").fetchall()
            files = db.execute("SELECT filename FROM models").fetchall()
        print(f"Data directory: {DATA}")
        print(f"Database: {DB}")
        print(f"Models: {len(files)}; missing GLB files: {sum(not (DATA / 'models' / row['filename']).is_file() for row in files)}")
        for row in rows:
            print(f"  {row['name']}: {row['count']} model(s)")
    else:
        print(f"Serving on http://{args.host}:{args.port}", flush=True)
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
