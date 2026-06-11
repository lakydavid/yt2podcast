#!/usr/bin/env python3
"""Dependency-light yt2podcast server (standard library + yt-dlp only).

No FastAPI / uvicorn / httpx — handy where compiling pydantic-core is painful
(e.g. Termux on Android). Serves the same frontend and the same
``/api/info`` and ``/api/audio`` (Range-proxy) endpoints as ``app.main``.

    pip install yt-dlp
    python server_lite.py            # -> http://localhost:8000
    PORT=8080 python server_lite.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Allow running both as "python server_lite.py" and "python -m server_lite".
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import core  # noqa: E402

CHUNK = 64 * 1024
_SSL_CTX = ssl.create_default_context(
    cafile=core.CA_BUNDLE if core.USE_SYSTEM_CERTS else None
)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "yt2podcast-lite"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # concise access log: ›"GET /path" 206
        sys.stderr.write("› " + (fmt % args) + "\n")

    # -- helpers ---------------------------------------------------------
    def _json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    # -- routing ---------------------------------------------------------
    def do_GET(self):
        path, qs = self._query()
        try:
            if path == "/api/info":
                return self._api_info(qs)
            if path == "/api/audio":
                return self._api_audio(qs)
            if path == "/healthz":
                return self._json(200, {"ok": True})
            return self._static(path)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # client seeked/closed mid-stream — normal, just stop
        except Exception:  # noqa: BLE001
            try:
                self._json(500, {"error": "Szerverhiba."})
            except OSError:
                pass

    # -- endpoints -------------------------------------------------------
    def _api_info(self, qs):
        url = (qs.get("url") or [""])[0]
        if len(url) < 8:
            return self._json(400, {"error": "Hiányzó vagy hibás URL."})
        try:
            info = core.get_info(url)
        except core.InfoError as e:
            return self._json(e.status, {"error": e.detail})
        return self._json(200, core.public_info(info))

    def _api_audio(self, qs):
        url = (qs.get("url") or [""])[0]
        quality = (qs.get("q") or ["compat"])[0]
        if not url:
            return self._json(400, {"error": "Hiányzó URL."})
        try:
            info = core.get_info(url)
        except core.InfoError as e:
            return self._json(e.status, {"error": e.detail})
        fmt = core.select_format(info["audio_formats"], quality)
        if not core.is_allowed_media_url(fmt["url"]):
            return self._json(502, {"error": "Nem engedélyezett hangforrás."})

        headers = {"User-Agent": core.UA, "Accept": "*/*"}
        rng = self.headers.get("Range")
        if rng:
            headers["Range"] = rng

        req = urllib.request.Request(fmt["url"], headers=headers)
        try:
            upstream = urllib.request.urlopen(req, context=_SSL_CTX, timeout=30)
        except urllib.error.HTTPError:
            core.invalidate(url)  # stream URL likely expired
            return self._json(502, {"error": "A hangfolyam lejárt, próbáld újra."})
        except (urllib.error.URLError, TimeoutError):
            return self._json(502, {"error": "Nem sikerült elérni a hangfolyamot."})

        with upstream:
            status = upstream.status or 200
            self.send_response(status)
            self.send_header("Content-Type", upstream.headers.get("Content-Type", fmt["mime"]))
            self.send_header("Accept-Ranges", "bytes")
            # Keep cacheable — no-store makes Chromium treat audio as a
            # non-seekable live stream and breaks seeking.
            for h in ("Content-Length", "Content-Range"):
                v = upstream.headers.get(h)
                if v is not None:
                    self.send_header(h, v)
            self.end_headers()
            while True:
                chunk = upstream.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _static(self, path: str):
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (core.STATIC_DIR / rel).resolve()
        if core.STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._json(404, {"error": "Nincs ilyen oldal."})
        data = target.read_bytes()
        ctype = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Always revalidate HTML so UI updates show up without a manual cache clear.
        if target.suffix == ".html":
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def main():
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"yt2podcast (lite) -> http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
