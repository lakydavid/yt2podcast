"""Starlette + uvicorn variant of the yt2podcast server.

Same endpoints as ``app.main`` (FastAPI) but built on plain Starlette, whose
only deps — like uvicorn and httpx — are pure Python. That means it installs
without any Rust/pydantic compilation, so it runs cleanly on Termux/Android
while still using a battle-tested ASGI server that handles media seeking,
range requests and client aborts correctly.

    pip install starlette uvicorn httpx yt-dlp
    uvicorn app.lite_app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import sys

import httpx
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app import core, proxy

_HTTPX_VERIFY = core.CA_BUNDLE if core.USE_SYSTEM_CERTS else True
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, read=None),
    follow_redirects=True,
    headers={"User-Agent": core.UA},
    verify=_HTTPX_VERIFY,
)

_active_streams = 0


async def _info(url: str) -> dict:
    return await asyncio.to_thread(core.get_info, url)


async def api_info(request):
    url = request.query_params.get("url", "")
    try:
        info = await _info(url)
    except core.InfoError as e:
        return JSONResponse({"error": e.detail}, status_code=e.status)
    return JSONResponse(core.public_info(info))


async def api_audio(request):
    global _active_streams
    url = request.query_params.get("url", "")
    q = request.query_params.get("q", "min")
    rng = request.headers.get("range")
    if _active_streams >= core.MAX_STREAMS:
        return JSONResponse({"error": "Most túl sokan hallgatnak, próbáld pár perc múlva."}, status_code=503)

    _active_streams += 1
    try:
        info = await _info(url)
        fmt = core.select_format(info["audio_formats"], q)
        if not core.is_allowed_media_url(fmt["url"]):
            raise _Stop(502, "Nem engedélyezett hangforrás.")
        try:
            status, headers, body_gen = await proxy.open_audio(_client, fmt["url"], rng, fmt["mime"], core.UA)
        except (proxy.ProxyError, httpx.HTTPError):
            core.invalidate(url)  # stream URL likely expired — re-resolve next time
            raise _Stop(502, "A hangfolyam lejárt, próbáld újra.")
    except core.InfoError as e:
        _active_streams -= 1
        return JSONResponse({"error": e.detail}, status_code=e.status)
    except _Stop as e:
        _active_streams -= 1
        return JSONResponse({"error": e.detail}, status_code=e.status)
    except BaseException:
        _active_streams -= 1
        raise

    # Cacheable on purpose: a no-store header makes Chromium treat audio as a
    # non-seekable live stream and breaks seeking.
    sys.stderr.write(f"[audio] q={q} range={rng or '-'} -> {status} clen={headers.get('Content-Length','-')}\n")

    async def body():
        global _active_streams
        n = 0
        why = "done"
        try:
            async for chunk in body_gen():
                n += len(chunk)
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            why = "client-abort(seek/close)"
            raise
        except Exception as e:  # noqa: BLE001
            why = f"error:{e!r}"
        finally:
            _active_streams -= 1
            sys.stderr.write(f"[audio] {why} sent={n}B active={_active_streams}\n")

    return StreamingResponse(body(), status_code=status, headers=headers)


class _Stop(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail


async def healthz(request):
    return JSONResponse({"ok": True})


async def index(request):
    return FileResponse(core.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app = Starlette(routes=[
    Route("/api/info", api_info),
    Route("/api/audio", api_audio),
    Route("/healthz", healthz),
    Route("/", index),
    Mount("/", app=StaticFiles(directory=str(core.STATIC_DIR))),
])
