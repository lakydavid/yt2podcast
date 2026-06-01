"""yt2podcast — listen to a YouTube video's audio as a low-data podcast.

The app never downloads the video stream. It resolves the *audio-only*
track that YouTube already offers (opus ~30 kbps / m4a ~48 kbps and up) and
proxies it to the browser with HTTP Range support, so even multi-hour videos
stream with minimal mobile data and seeking only fetches what is needed.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"

# Respect a custom CA bundle if the environment provides one (corporate proxies,
# sandboxes with TLS interception). Falls back to the default trust store.
CA_BUNDLE = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
_HTTPX_VERIFY = CA_BUNDLE if CA_BUNDLE and Path(CA_BUNDLE).exists() else True

# How long a resolved stream URL stays usable. YouTube URLs are valid for a few
# hours; we re-resolve well before they expire.
CACHE_TTL = 60 * 60  # seconds
# Browser User-Agent so googlevideo serves us at full speed.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Optional production knobs for YouTube's anti-bot measures (see README):
#   YT2P_COOKIES_FILE  – Netscape cookies.txt exported from a logged-in browser
#   YT2P_PLAYER_CLIENTS – comma list, e.g. "ios,web_safari,tv" to dodge 403/PoToken
COOKIES_FILE = os.environ.get("YT2P_COOKIES_FILE")
PLAYER_CLIENTS = [c.strip() for c in os.environ.get("YT2P_PLAYER_CLIENTS", "").split(",") if c.strip()]

app = FastAPI(title="yt2podcast")

# url -> (resolved_at, slim_info)
_info_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = asyncio.Lock()

# A single shared async client for proxying audio bytes.
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, read=None),
    follow_redirects=True,
    headers={"User-Agent": UA},
    verify=_HTTPX_VERIFY,
)


def _ext_to_mime(ext: str | None) -> str:
    return {
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "webm": "audio/webm",
        "opus": "audio/ogg",
        "ogg": "audio/ogg",
        "mp3": "audio/mpeg",
    }.get((ext or "").lower(), "audio/mpeg")


def _extract(url: str) -> dict:
    """Run yt-dlp (blocking) and return a slim, JSON-friendly info dict."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        # We only need audio; this hints format sorting but we pick ourselves.
        "format": "bestaudio/best",
    }
    if _HTTPX_VERIFY is not True:
        # A custom CA bundle is configured (corporate TLS proxy / sandbox).
        # Tell yt-dlp to use the OS trust store instead of bundled certifi,
        # so the extra CA is honoured. Verification stays ON.
        ydl_opts["compat_opts"] = ["no-certifi"]
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        ydl_opts["cookiefile"] = COOKIES_FILE
    if PLAYER_CLIENTS:
        ydl_opts["extractor_args"] = {"youtube": {"player_client": PLAYER_CLIENTS}}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    audio_formats = []
    for f in info.get("formats", []):
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        if not f.get("url"):
            continue
        if acodec in (None, "none"):
            continue
        if vcodec not in (None, "none"):  # skip muxed video+audio
            continue
        audio_formats.append(
            {
                "format_id": f.get("format_id"),
                "url": f["url"],
                "ext": f.get("ext"),
                "acodec": acodec,
                "abr": f.get("abr") or f.get("tbr") or 0,
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "mime": _ext_to_mime(f.get("ext")),
            }
        )

    audio_formats.sort(key=lambda x: x["abr"] or 1e9)

    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "audio_formats": audio_formats,
    }


async def _get_info(url: str) -> dict:
    now = time.time()
    async with _cache_lock:
        cached = _info_cache.get(url)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]
    try:
        info = await asyncio.to_thread(_extract, url)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=_clean_err(str(e)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Hiba a feldolgozáskor: {e}")
    if not info.get("audio_formats"):
        raise HTTPException(status_code=422, detail="Ehhez a videóhoz nincs elérhető hangsáv.")
    async with _cache_lock:
        _info_cache[url] = (now, info)
    return info


def _clean_err(msg: str) -> str:
    msg = msg.replace("ERROR:", "").strip()
    if "Private video" in msg:
        return "Ez egy privát videó."
    if "members-only" in msg or "Join this channel" in msg:
        return "Ez a videó csak tagoknak elérhető."
    low = msg.lower()
    if ("age-restricted" in low or "age restricted" in low
            or ("inappropriate for some users" in low) or "confirm your age" in low):
        return "Korhatáros videó, nem érhető el bejelentkezés nélkül."
    if "sign in to confirm" in low and "bot" in low:
        return "A YouTube botnak nézte a kérést. Próbáld újra később."
    if "not available" in msg.lower():
        return "A videó nem elérhető."
    return msg[:300] or "Nem sikerült feldolgozni a linket."


def _select_format(audio_formats: list[dict], quality: str) -> dict:
    """Pick an audio format. Default favours an m4a track for broad device
    compatibility (iOS Safari) while still being very low bitrate."""
    m4a = [f for f in audio_formats if (f["ext"] == "m4a" or (f["acodec"] or "").startswith(("mp4a", "aac")))]
    if quality == "ultra":          # absolute smallest, may be opus
        return audio_formats[0]
    if quality == "high":           # best-quality m4a (still audio-only)
        return (m4a or audio_formats)[-1]
    # "compat" (default): smallest m4a, falls back to smallest overall
    return (m4a or audio_formats)[0]


@app.get("/api/info")
async def api_info(url: str = Query(..., min_length=8)):
    info = await _get_info(url)
    out = {k: info[k] for k in ("id", "title", "uploader", "duration", "thumbnail", "webpage_url")}
    out["qualities"] = []
    seen = set()
    for q in ("ultra", "compat", "high"):
        f = _select_format(info["audio_formats"], q)
        abr = round(f["abr"]) if f["abr"] else None
        size = f["filesize"]
        if size is None and abr and info.get("duration"):
            size = int(abr * 1000 / 8 * info["duration"])  # bytes estimate
        key = (q, f["format_id"])
        if key in seen:
            continue
        seen.add(key)
        out["qualities"].append(
            {"id": q, "abr": abr, "ext": f["ext"], "est_bytes": size}
        )
    return out


@app.get("/api/audio")
async def api_audio(request: Request, url: str = Query(...), q: str = Query("compat")):
    info = await _get_info(url)
    fmt = _select_format(info["audio_formats"], q)
    upstream_url = fmt["url"]

    fwd_headers = {"User-Agent": UA, "Accept": "*/*"}
    range_header = request.headers.get("range")
    if range_header:
        fwd_headers["Range"] = range_header

    try:
        upstream_req = _client.build_request("GET", upstream_url, headers=fwd_headers)
        upstream = await _client.send(upstream_req, stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Nem sikerült elérni a hangfolyamot: {e}")

    if upstream.status_code >= 400:
        await upstream.aclose()
        # Stream URL likely expired — drop cache so the next try re-resolves.
        async with _cache_lock:
            _info_cache.pop(url, None)
        raise HTTPException(status_code=502, detail="A hangfolyam lejárt, próbáld újra.")

    out_headers = {
        "Content-Type": upstream.headers.get("content-type", fmt["mime"]),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    for h in ("content-length", "content-range"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
