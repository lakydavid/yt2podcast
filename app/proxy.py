"""Chunked googlevideo proxy.

A single long open-ended request to googlevideo gets throttled by YouTube to
roughly playback speed, which leaves the browser with no buffer cushion and
makes it stall every few seconds. Fetching the stream in ~1 MiB ranges keeps
each request short and un-throttled, so the browser can buffer ahead and play
smoothly. (This is the same trick yt-dlp uses via --http-chunk-size.)

Shared by the FastAPI (app.main) and Starlette (app.lite_app) servers; both
pass in their httpx.AsyncClient. Returns a status, headers and an async body
generator, framework-agnostically.
"""
from __future__ import annotations

import re

CHUNK = 1024 * 1024  # 1 MiB per upstream request
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class ProxyError(Exception):
    """Upstream returned an error before we could start streaming."""


def _parse_range(h: str | None):
    """Return (start, end, is_ranged). start is None for a suffix range."""
    if not h:
        return 0, None, False
    m = _RANGE_RE.search(h)
    if not m:
        return 0, None, False
    s, e = m.group(1), m.group(2)
    if s == "":  # bytes=-N  (last N bytes)
        return None, (int(e) if e else 0), True
    return int(s), (int(e) if e else None), True


def _total_from(content_range: str | None):
    if content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    return None


async def _chunk(client, url, start, end, ua):
    return await client.get(
        url, headers={"User-Agent": ua, "Accept": "*/*", "Range": f"bytes={start}-{end}"}
    )


async def open_audio(client, url, range_header, fallback_mime, ua):
    """Resolve sizing + first bytes, then return (status, headers, body_gen).

    Raises ProxyError if the upstream rejects the initial request.
    """
    start, end, ranged = _parse_range(range_header)

    if start is None:  # suffix range — need the total length first
        probe = await _chunk(client, url, 0, 0, ua)
        if probe.status_code >= 400:
            raise ProxyError()
        total = _total_from(probe.headers.get("content-range")) or 0
        start = max(0, total - (end or 0))
        end = total - 1

    chunk_end = end if (end is not None and end < start + CHUNK) else start + CHUNK - 1
    first = await _chunk(client, url, start, chunk_end, ua)
    if first.status_code >= 400:
        raise ProxyError()

    total = _total_from(first.headers.get("content-range")) or (start + len(first.content))
    if end is None:
        end = total - 1
    length = max(0, end - start + 1)

    headers = {
        "Content-Type": first.headers.get("content-type", fallback_mime),
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    status = 200
    if ranged:
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"

    async def body():
        sent = 0
        data = first.content[:length]
        if data:
            yield data
            sent += len(data)
        cur = start + len(first.content)
        while sent < length and cur <= end:
            r = await _chunk(client, url, cur, min(cur + CHUNK - 1, end), ua)
            if r.status_code >= 400 or not r.content:
                break
            b = r.content[: length - sent]
            yield b
            sent += len(b)
            cur += len(r.content)

    return status, headers, body
