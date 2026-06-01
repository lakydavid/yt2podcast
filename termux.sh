#!/data/data/com.termux/files/usr/bin/bash
# yt2podcast on Android (Termux). Run this from inside the cloned repo:
#   bash termux.sh
# Only needs the pure-Python yt-dlp — no FastAPI/Rust compilation.
set -e
cd "$(dirname "$0")"

echo "==> Installing Python + yt-dlp (first run only)…"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python >/dev/null 2>&1 || true
pip install -U --quiet yt-dlp

PORT="${PORT:-8000}"
echo "==> Starting yt2podcast on http://localhost:$PORT"
echo "    Open that address in your phone's browser. Ctrl+C to stop."
PORT="$PORT" python server_lite.py
