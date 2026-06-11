"""yt2podcast — listen to a YouTube video's audio as a low-data podcast.

The app never downloads the video stream. It resolves the *audio-only*
track that YouTube already offers (opus ~30 kbps / m4a ~48 kbps and up) and
proxies it to the browser with HTTP Range support, so even multi-hour videos
stream with minimal mobile data and seeking only fetches what is needed.

This is the FastAPI server. For a dependency-light alternative (only needs
yt-dlp, e.g. on Termux) see ``server_lite.py`` — both share ``app.core``.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import core

_HTTPX_VERIFY = core.CA_BUNDLE if core.USE_SYSTEM_CERTS else True

app = FastAPI(title="yt2podcast")

# A single shared async client for proxying audio bytes.
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, read=None),
    follow_redirects=True,
    headers={"User-Agent": core.UA},
    verify=_HTTPX_VERIFY,
)


async def _get_info(url: str) -> dict:
    try:
        return await asyncio.to_thread(core.get_info, url)
    except core.InfoError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@app.get("/api/info")
async def api_info(url: str = Query(..., min_length=8)):
    info = await _get_info(url)
    return core.public_info(info)


_active_streams = 0


@app.get("/api/audio")
async def api_audio(request: Request, url: str = Query(...), q: str = Query("compat")):
    global _active_streams
    if _active_streams >= core.MAX_STREAMS:
        raise HTTPException(status_code=503, detail="Most túl sokan hallgatnak, próbáld pár perc múlva.")

    # Reserve a slot up front so a burst can't slip past the gate; released in
    # the body's finally on success, or here on any setup failure.
    _active_streams += 1
    try:
        info = await _get_info(url)
        fmt = core.select_format(info["audio_formats"], q)
        if not core.is_allowed_media_url(fmt["url"]):
            raise HTTPException(status_code=502, detail="Nem engedélyezett hangforrás.")

        fwd_headers = {"User-Agent": core.UA, "Accept": "*/*"}
        range_header = request.headers.get("range")
        if range_header:
            fwd_headers["Range"] = range_header

        try:
            upstream_req = _client.build_request("GET", fmt["url"], headers=fwd_headers)
            upstream = await _client.send(upstream_req, stream=True)
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="Nem sikerült elérni a hangfolyamot.")

        if upstream.status_code >= 400:
            await upstream.aclose()
            core.invalidate(url)  # stream URL likely expired — re-resolve next time
            raise HTTPException(status_code=502, detail="A hangfolyam lejárt, próbáld újra.")
    except BaseException:
        _active_streams -= 1
        raise

    # NB: must stay cacheable — a no-store/no-cache header makes Chromium treat
    # the audio as a non-seekable live stream, which breaks seeking.
    out_headers = {
        "Content-Type": upstream.headers.get("content-type", fmt["mime"]),
        "Accept-Ranges": "bytes",
    }
    for h in ("content-length", "content-range"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]

    async def body():
        global _active_streams
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            _active_streams -= 1
            await upstream.aclose()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index():
    # Always revalidate the HTML so UI updates show up without a manual cache clear.
    return FileResponse(core.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


app.mount("/", StaticFiles(directory=core.STATIC_DIR), name="static")
