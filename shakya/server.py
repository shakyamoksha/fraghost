from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .update import updates

MAX_BODY = 64 * 1024 * 1024
FALLBACK = """<!doctype html>
<meta charset="utf-8">
<title>fraghost</title>
<body style="margin:0;background:#12130f;color:#f3efe4;font:16px Segoe UI,sans-serif">
<main style="max-width:36rem;margin:12vh auto;padding:0 24px">
<h1 style="font-weight:560">fraghost</h1>
<p>The interface has not been built yet.</p>
<p>From the <code>ui</code> folder, run <code>npm install</code> and then <code>npm run build</code>.</p>
</main>
""".encode("utf-8")


def serve(engine, port: int = 47371, tries: int = 10, dist_dir: Path | None = None):
    handler = _make_handler(engine, dist_dir)
    httpd = None
    if port == 0:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    else:
        last = None
        for candidate in range(port, port + tries):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
                break
            except OSError as exc:
                last = exc
        if httpd is None:
            raise last
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="shakya-http", daemon=True)
    thread.start()
    bound = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{bound}/"


def _make_handler(engine, dist_dir: Path | None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def do_OPTIONS(self):
            self._send(204, b"")

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _dispatch(self, method: str):
            try:
                if not self._authorized():
                    self._send_json(401, {"error": "Password required"})
                    return
                parsed = urllib.parse.urlparse(self.path)
                parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
                query = urllib.parse.parse_qs(parsed.query)
                if not parts or parts[0] != "api":
                    if method != "GET":
                        self._send_json(405, {"error": "Method not allowed"})
                        return
                    self._static(parsed.path)
                    return
                self._api(method, parts[1:], query)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except KeyError:
                self._send_json(404, {"error": "Torrent not found"})
            except Exception as exc:
                engine._log(f"API error: {exc}")
                self._send_json(500, {"error": str(exc)})

        def _authorized(self) -> bool:
            expected = engine.settings.get("web_password") or ""
            if not expected:
                return True
            import hmac

            got = (
                self.headers.get("X-Fraghost-Password")
                or self.headers.get("X-Cairn-Password")
                or self.headers.get("X-Shakya-Password")
                or ""
            )
            return hmac.compare_digest(got, expected)

        def _api(self, method: str, parts: list[str], query: dict):
            if parts == ["health"] and method == "GET":
                self._send_json(200, {"ok": True, "version": __version__})
                return
            if parts == ["state"] and method == "GET":
                detail = (query.get("detail") or [None])[0]
                self._send_json(200, engine.snapshot(detail))
                return
            if parts == ["settings"] and method == "GET":
                self._send_json(200, engine.settings)
                return
            if parts == ["settings"] and method == "PUT":
                self._send_json(200, engine.update_settings(self._body()))
                return
            if parts == ["update"] and method == "GET":
                self._send_json(200, updates.check(engine.settings))
                return
            if parts == ["update"] and method == "POST":
                self._send_json(200, updates.apply(engine.settings))
                return
            if parts == ["logs"] and method == "GET":
                self._send_json(200, {"lines": engine.logs()})
                return
            if parts == ["preview"] and method == "POST":
                body = self._body()
                self._send_json(200, engine.preview(body.get("source", ""), body.get("value", "")))
                return
            if parts == ["torrents"] and method == "POST":
                body = self._body()
                torrent = engine.add(
                    body.get("source", ""),
                    body.get("value", ""),
                    save_path=body.get("save_path") or "",
                    label=body.get("label") or "",
                    paused=bool(body.get("paused")),
                    sequential=bool(body.get("sequential")),
                    super_seeding=bool(body.get("super_seeding")),
                    priorities=body.get("file_priorities"),
                    stop_when_ready=bool(body.get("stop_when_ready")),
                    manual=body.get("manual"),
                )
                self._send_json(200, torrent)
                return
            if parts == ["create"] and method == "POST":
                body = self._body()
                self._send_json(
                    200,
                    engine.create_torrent(
                        body.get("path") or "",
                        body.get("output") or "",
                        trackers=body.get("trackers") or "",
                        comment=body.get("comment") or "",
                        private=bool(body.get("private")),
                        piece_size=int(body.get("piece_size") or 0),
                        hybrid=bool(body.get("hybrid")),
                        start=bool(body.get("start")),
                    ),
                )
                return
            if parts == ["blocklist"] and method == "POST":
                body = self._body()
                self._send_json(200, engine.import_blocklist(body.get("path") or ""))
                return
            if parts == ["rss", "refresh"] and method == "POST":
                self._send_json(200, engine.refresh_rss())
                return
            if len(parts) >= 2 and parts[0] == "torrents":
                tid = parts[1]
                action = parts[2] if len(parts) > 2 else ""
                if method == "PATCH" and not action:
                    self._send_json(200, engine.update(tid, self._body()))
                    return
                if method == "DELETE" and not action:
                    flag = (query.get("delete_files") or ["0"])[0]
                    engine.remove(tid, flag in ("1", "true", "yes"))
                    self._send_json(200, {"ok": True})
                    return
                if method == "POST" and action == "action":
                    body = self._body()
                    self._send_json(200, engine.action(tid, body.get("action") or ""))
                    return
                if method == "POST" and action == "open":
                    self._send_json(200, {"path": engine.reveal(tid)})
                    return
                if method == "GET" and action == "export":
                    filename, data = engine.torrent_file_bytes(tid)
                    self._send(200, data, content_type="application/x-bittorrent", filename=filename)
                    return
            self._send_json(404, {"error": "Not found"})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError("Request is too large")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("Request is not valid JSON") from exc
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data

        def _static(self, path: str):
            root = dist_dir.resolve() if dist_dir and dist_dir.exists() else None
            relative = path.split("?", 1)[0].lstrip("/") or "index.html"
            if root is None:
                if relative in ("", "index.html"):
                    self._send(200, FALLBACK, content_type="text/html; charset=utf-8")
                    return
                self._send_json(404, {"error": "Not found"})
                return
            target = (root / relative).resolve()
            if root != target and root not in target.parents:
                self._send_json(403, {"error": "Forbidden"})
                return
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                if "." not in Path(relative).name:
                    target = root / "index.html"
                else:
                    self._send_json(404, {"error": "Not found"})
                    return
            data = target.read_bytes()
            guessed = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if guessed.startswith("text/") or guessed in ("application/javascript", "image/svg+xml"):
                guessed += "; charset=utf-8"
            self._send(200, data, content_type=guessed, cache=target.name != "index.html")

        def _send_json(self, status: int, payload) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), content_type="application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8", filename: str = "", cache: bool = False):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-cache")
            self.send_header("Connection", "close")
            origin = self.headers.get("Origin") or ""
            if origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, X-Fraghost-Password, X-Cairn-Password, X-Shakya-Password",
                )
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
                self.send_header("Vary", "Origin")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

    return Handler
