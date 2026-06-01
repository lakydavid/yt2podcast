"""Framework-agnostic core for yt2podcast.

Only depends on the standard library and yt-dlp (pure Python), so it can be
shared by the FastAPI server (`app.main`) and the dependency-light stdlib
server (`server_lite.py`, handy on Termux / minimal hosts).
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import yt_dlp

STATIC_DIR = Path(__file__).parent / "static"

# Respect a custom CA bundle if the environment provides one (corporate proxies,
# sandboxes with TLS interception). Falls back to the default trust store.
CA_BUNDLE = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
USE_SYSTEM_CERTS = bool(CA_BUNDLE and Path(CA_BUNDLE).exists())

# How long a resolved stream URL stays usable. YouTube URLs are valid for a few
# hours; we re-resolve well before they expire.
CACHE_TTL = 60 * 60  # seconds
# Browser User-Agent so googlevideo serves us at full speed.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Optional production knobs for YouTube's anti-bot measures (see README):
#   YT2P_COOKIES_FILE   – Netscape cookies.txt exported from a logged-in browser
#   YT2P_PLAYER_CLIENTS – comma list, e.g. "ios,web_safari,tv" to dodge 403/PoToken
COOKIES_FILE = os.environ.get("YT2P_COOKIES_FILE")
PLAYER_CLIENTS = [c.strip() for c in os.environ.get("YT2P_PLAYER_CLIENTS", "").split(",") if c.strip()]

QUALITIES = ("ultra", "compat")


class InfoError(Exception):
    """Raised when a URL cannot be turned into playable audio info."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# url -> (resolved_at, slim_info)
_info_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


# Matches a timestamp token like 0:00, 12:34 or 1:02:03, not glued to other digits.
_TS_RE = re.compile(r"(?<![\d:])(\d{1,2}):([0-5]?\d)(?::([0-5]?\d))?(?![\d:])")


def parse_chapters_from_description(desc: str | None, duration: int | None) -> list[dict]:
    """Extract `[{start, title}]` chapters from timestamps in a description.

    Used as a fallback when YouTube exposes no native chapters. Accepts the
    common `0:00 Title` / `1:02:03 - Title` / `[0:00] Title` line formats.
    """
    if not desc:
        return []
    found: list[tuple[int, str]] = []
    for line in desc.splitlines():
        m = _TS_RE.search(line)
        if not m:
            continue
        h_or_m, mid, last = m.groups()
        if last is not None:  # H:MM:SS
            secs = int(h_or_m) * 3600 + int(mid) * 60 + int(last)
        else:  # M:SS
            secs = int(h_or_m) * 60 + int(mid)
        if duration and secs > duration + 1:
            continue
        title = (line[: m.start()] + " " + line[m.end():]).strip(" \t-–—:•·.)(][>")
        title = re.sub(r"\s{2,}", " ", title).strip()
        found.append((secs, title or f"{secs // 60}:{secs % 60:02d}"))
    # Need a few, roughly starting near the top and strictly increasing in time.
    found.sort(key=lambda x: x[0])
    deduped: list[tuple[int, str]] = []
    for s, t in found:
        if not deduped or s > deduped[-1][0]:
            deduped.append((s, t))
    if len(deduped) < 2 or deduped[0][0] > 60:
        return []
    return [{"start": s, "title": t} for s, t in deduped]


def build_chapters(info: dict, duration: int | None) -> list[dict]:
    native = info.get("chapters") or []
    chapters = [
        {"start": int(c.get("start_time") or 0), "title": (c.get("title") or "").strip()}
        for c in native
        if c.get("start_time") is not None
    ]
    if chapters:
        return chapters
    return parse_chapters_from_description(info.get("description"), duration)


def ext_to_mime(ext: str | None) -> str:
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
    if USE_SYSTEM_CERTS:
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
                "mime": ext_to_mime(f.get("ext")),
            }
        )

    audio_formats.sort(key=lambda x: x["abr"] or 1e9)

    duration = info.get("duration")
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": duration,
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "chapters": build_chapters(info, duration),
        "audio_formats": audio_formats,
    }


def clean_err(msg: str) -> str:
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
        return "A YouTube botnak nézte a kérést. Próbáld újra később (vagy adj meg cookie-fájlt)."
    if "not available" in low:
        return "A videó nem elérhető."
    return msg[:300] or "Nem sikerült feldolgozni a linket."


def get_info(url: str) -> dict:
    """Cached, thread-safe resolution of a URL into a slim info dict.

    Raises ``InfoError`` with a user-friendly Hungarian message on failure.
    """
    now = time.time()
    with _cache_lock:
        cached = _info_cache.get(url)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]
    try:
        info = _extract(url)
    except yt_dlp.utils.DownloadError as e:
        raise InfoError(422, clean_err(str(e)))
    except Exception as e:  # noqa: BLE001
        raise InfoError(500, f"Hiba a feldolgozáskor: {e}")
    if not info.get("audio_formats"):
        raise InfoError(422, "Ehhez a videóhoz nincs elérhető hangsáv.")
    with _cache_lock:
        _info_cache[url] = (now, info)
    return info


def invalidate(url: str) -> None:
    with _cache_lock:
        _info_cache.pop(url, None)


def select_format(audio_formats: list[dict], quality: str) -> dict:
    """Pick an audio format. Default favours an m4a track for broad device
    compatibility (iOS Safari) while still being very low bitrate."""
    if quality == "ultra":  # absolute smallest, may be opus
        return audio_formats[0]
    # "compat" (default): smallest m4a, falls back to smallest overall
    m4a = [f for f in audio_formats if (f["ext"] == "m4a" or (f["acodec"] or "").startswith(("mp4a", "aac")))]
    return (m4a or audio_formats)[0]


def public_info(info: dict) -> dict:
    """Shape a cached info dict into the JSON returned by /api/info."""
    out = {k: info[k] for k in ("id", "title", "uploader", "duration", "thumbnail", "webpage_url", "chapters")}
    out["qualities"] = []
    seen = set()
    for q in QUALITIES:
        f = select_format(info["audio_formats"], q)
        abr = round(f["abr"]) if f["abr"] else None
        size = f["filesize"]
        if size is None and abr and info.get("duration"):
            size = int(abr * 1000 / 8 * info["duration"])  # bytes estimate
        key = (q, f["format_id"])
        if key in seen:
            continue
        seen.add(key)
        out["qualities"].append({"id": q, "abr": abr, "ext": f["ext"], "est_bytes": size})
    return out
