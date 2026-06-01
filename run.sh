#!/usr/bin/env bash
# Start yt2podcast locally.
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
python3 -m pip install -q -r requirements.txt
echo "yt2podcast -> http://localhost:$PORT"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
