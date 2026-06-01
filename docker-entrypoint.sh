#!/usr/bin/env sh
# YouTube changes constantly, so refresh yt-dlp on each start unless disabled
# (YT2P_UPDATE_YTDLP=0). Failure is non-fatal — we keep the baked-in version.
set -e
if [ "${YT2P_UPDATE_YTDLP:-1}" = "1" ]; then
  echo "==> Updating yt-dlp…"
  pip install --no-cache-dir -U yt-dlp || echo "   (update failed, using existing version)"
fi
exec "$@"
