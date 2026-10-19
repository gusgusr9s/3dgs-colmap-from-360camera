"""HTTP tests for authentication, GLB storage, and login audit trail."""
import json
import gzip
import os
import struct
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_db = server.DATA, server.DB
        server.DATA = Path(self.temp.name)
        server.DB = server.DATA / "portal.sqlite3"
        server.init_db()
        with server.connect() as db:
            db.execute("INSERT INTO users VALUES(?,?,?,?)", ("admin", server.password_hash("correct horse battery"), "admin", server.now()))
            db.execute("INSERT INTO users VALUES(?,?,?,?)", ("guest", server.password_hash("another long password"), "viewer", server.now()))
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.DATA, server.DB = self.old_data, self.old_db
        self.temp.cleanup()

    def call(self, path, method="GET", body=None, headers=None):
        headers = {"Origin": self.base, **(headers or {})}
        req = Request(self.base + path, data=body, headers=headers, method=method)
        try:
            res = urlopen(req)
        except HTTPError as error:
            res = error
        with res:
            return res.status, dict(res.headers), res.read()

    def login(self, user, password):
        status, headers, body = self.call("/api/login", "POST", json.dumps({"username": user, "password": password}).encode(), {"Content-Type": "application/json"})
        return status, headers.get("Set-Cookie", "").split(";", 1)[0], json.loads(body)

    def test_login_upload_access_and_audit(self):
        for path in ("/", "/app.js", "/viewer.js", "/model-cache-sw.js", "/style.css", "/project.css", "/vendor/spark/spark.module.js", *(f"/vendor/three/{item}" for item in server.VENDOR_FILES)):
            self.assertEqual(self.call(path)[0], 200, path)
        self.assertIn(b'<form id="login-form" method="post"', self.call("/")[2])
        _, asset_headers, _ = self.call("/model-cache-sw.js")
        self.assertEqual(asset_headers["Cache-Control"], "public, no-cache")
        self.assertEqual(self.call("/model-cache-sw.js", headers={"If-None-Match": asset_headers["ETag"]})[0], 304)
        status, _, _ = self.call("/api/models")
        self.assertEqual(status, 401)
        self.assertEqual(self.login("admin", "wrong")[0], 401)
        status, cookie, _ = self.login("admin", "correct horse battery")
        self.assertEqual(status, 200)
        status, _, body = self.call("/api/me", headers={"Cookie": cookie})
        csrf = json.loads(body)["csrf"]
        self.assertEqual(status, 200)
        status, _, body = self.call("/api/projects", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["projects"][0]["id"], "default")
        header = {"Cookie": cookie, "X-CSRF-Token": csrf, "X-Filename": "room.glb"}
        payload = json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0}).encode()
        payload += b" " * ((-len(payload)) % 4)
        glb = struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) + struct.pack("<I4s", len(payload), b"JSON") + payload
        self.assertEqual(self.call("/api/models", "POST", glb, {"Cookie": cookie})[0], 403)
        status, _, body = self.call("/api/models", "POST", glb, header)
        self.assertEqual(status, 201, body)
        model_id = json.loads(body)["id"]
        self.assertEqual(self.call(f"/api/models/{model_id}/file")[0], 401)
        self.assertEqual(self.call(f"/api/models/{model_id}/file", "HEAD")[0], 401)
        head_status, head_headers, head_body = self.call(f"/api/models/{model_id}/file", "HEAD", headers={"Cookie": cookie})
        self.assertEqual(head_status, 200)
        self.assertEqual(head_body, b"")
        self.assertEqual(head_headers["Content-Length"], str(len(glb)))
        self.assertEqual(head_headers["Cache-Control"], "private, no-store")
        self.assertTrue(head_headers["ETag"].startswith(f'"{model_id}-'))
        self.assertEqual(self.call(f"/api/models/{model_id}/file", headers={"Cookie": cookie})[2], glb)
        self.assertEqual(self.call("/api/models", "POST", b"not glb", header)[0], 413)
        self.assertEqual(self.call("/api/models", "POST", b"bad!" + glb[4:], header)[0], 400)
        status, _, body = self.call("/api/projects", "POST", json.dumps({"name": "House A"}).encode(), {"Cookie": cookie, "X-CSRF-Token": csrf})
        self.assertEqual(status, 201, body)
        project_id = json.loads(body)["id"]
        status, _, body = self.call("/api/models", "POST", glb, {**header, "X-Project-Id": project_id})
        self.assertEqual(status, 201, body)
        second_id = json.loads(body)["id"]
        self.assertEqual(len(json.loads(self.call("/api/models?project=default", headers={"Cookie": cookie})[2])["models"]), 1)
        self.assertEqual([m["id"] for m in json.loads(self.call(f"/api/models?project={project_id}", headers={"Cookie": cookie})[2])["models"]], [second_id])
        # A new server process reading the same data directory must see both uploads.
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        self.assertEqual(self.call(f"/api/models/{second_id}/file", headers={"Cookie": cookie})[2], glb)
        self.assertEqual(len(json.loads(self.call(f"/api/models?project={project_id}", headers={"Cookie": cookie})[2])["models"]), 1)
        status, _, body = self.call("/api/login-log", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual([event["success"] for event in json.loads(body)["events"]], [1, 0])
        guest_status, guest_cookie, _ = self.login("guest", "another long password")
        self.assertEqual(guest_status, 200)
        self.assertEqual(self.call("/api/models", "POST", glb, {"Cookie": guest_cookie})[0], 403)
        self.assertEqual(self.call("/api/projects", "POST", b'{"name":"Forbidden"}', {"Cookie": guest_cookie})[0], 403)
        self.assertEqual(self.call("/api/login-log", headers={"Cookie": guest_cookie})[0], 403)
        self.assertEqual(self.call(f"/api/models/{model_id}", "DELETE", headers=header)[0], 200)
        self.assertEqual(self.call(f"/api/models/{model_id}/file", headers={"Cookie": cookie})[0], 404)

    def test_existing_models_migrate_to_default_project(self):
        with server.connect() as db:
            db.execute("DROP TABLE models")
            db.execute("CREATE TABLE models (id TEXT PRIMARY KEY,name TEXT NOT NULL,filename TEXT NOT NULL,bytes INTEGER NOT NULL,uploaded_by TEXT NOT NULL,uploaded_at TEXT NOT NULL)")
            db.execute("INSERT INTO models VALUES(?,?,?,?,?,?)", ("a" * 32, "Old room", "old.glb", 20, "admin", server.now()))
        (server.DATA / "models" / "old.glb").write_bytes(b"old")
        server.init_db()
        with server.connect() as db:
            row = db.execute("SELECT project_id FROM models WHERE id=?", ("a" * 32,)).fetchone()
        self.assertEqual(row["project_id"], "default")
        _, cookie, _ = self.login("admin", "correct horse battery")
        rows = json.loads(self.call("/api/models?project=default", headers={"Cookie": cookie})[2])["models"]
        self.assertEqual([row["name"] for row in rows], ["Old room"])

    def test_spz_upload_persists_and_rejects_corruption(self):
        _, cookie, _ = self.login("admin", "correct horse battery")
        csrf = json.loads(self.call("/api/me", headers={"Cookie": cookie})[2])["csrf"]
        headers = {"Cookie": cookie, "X-CSRF-Token": csrf, "X-Filename": "room.spz"}
        spz = (server.ROOT / "test_splat.spz").read_bytes()
        status, _, body = self.call("/api/models", "POST", spz, headers)
        self.assertEqual(status, 201, body)
        model_id = json.loads(body)["id"]
        self.assertEqual(json.loads(body)["format"], "spz")
        listed = json.loads(self.call("/api/models", headers={"Cookie": cookie})[2])["models"]
        self.assertEqual(listed[0]["format"], "spz")
        self.assertEqual(listed[0]["name"], "room")
        self.assertNotIn("filename", listed[0])
        self.assertEqual(self.call(f"/api/models/{model_id}/file")[0], 401)
        status, response_headers, data = self.call(f"/api/models/{model_id}/file", headers={"Cookie": cookie})
        self.assertEqual((status, response_headers["Content-Type"], data), (200, "application/octet-stream", spz))
        self.assertEqual(self.call("/api/models", "POST", spz[:-4], headers)[0], 400)
        self.assertEqual(self.call("/api/models", "POST", b"x" * len(spz), headers)[0], 400)
        decoded = bytearray(gzip.decompress(spz))
        decoded[4:8] = struct.pack("<I", 2)
        self.assertEqual(self.call("/api/models", "POST", gzip.compress(decoded), headers)[0], 400)
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        self.assertEqual(self.call(f"/api/models/{model_id}/file", headers={"Cookie": cookie})[2], spz)

    def test_five_character_password_and_untrusted_glb(self):
        with patch("sys.argv", ["server.py", "set-user", "short", "--role", "admin"]), \
             patch("server.getpass.getpass", side_effect=["abcd", "abcd"]), \
             redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                server.main()
        with patch("sys.argv", ["server.py", "set-user", "short", "--role", "admin"]), \
             patch("server.getpass.getpass", side_effect=["abcde", "abcde"]), \
             redirect_stdout(StringIO()):
            server.main()
        status, cookie, _ = self.login("short", "abcde")
        self.assertEqual(status, 200)
        csrf = json.loads(self.call("/api/me", headers={"Cookie": cookie})[2])["csrf"]
        payload = json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": "file:///etc/passwd"}]}).encode()
        payload += b" " * ((-len(payload)) % 4)
        glb = struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) + struct.pack("<I4s", len(payload), b"JSON") + payload
        headers = {"Cookie": cookie, "X-CSRF-Token": csrf, "X-Filename": "../escape.glb"}
        self.assertEqual(self.call("/api/models", "POST", glb, headers)[0], 400)
        with patch.object(server, "MAX_GLB_JSON", 8):
            self.assertEqual(self.call("/api/models", "POST", glb, headers)[0], 400)
        self.assertEqual(list((server.DATA / "models").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
