#!/bin/sh
set -eu

# Same PUID/PGID adjustment as docker/backend-entrypoint.sh, and for the same reason: this
# service's model-cache/custom-voices directories are bind-mounted subdirectories of the same
# LOLA_DATA_DIR the backend uses, so they need to end up owned by whatever host UID/GID
# actually owns that directory.
PUID="${PUID:-568}"
PGID="${PGID:-568}"

if [ "$(id -u tts)" != "$PUID" ]; then
  usermod -o -u "$PUID" tts
fi
if [ "$(id -g tts)" != "$PGID" ]; then
  groupmod -o -g "$PGID" tts
fi

# Not created automatically by the app — without this, the first model download and any
# stored teacher clip would fail to write on a fresh bind mount.
mkdir -p "${TTS_MODEL_CACHE_DIR:-/data/model-cache}" "${TTS_CUSTOM_VOICES_DIR:-/data/custom-voices}"

# Re-applied on every start, not just once at image build time — these are bind mounts, so their
# actual on-disk ownership is independent of the image and changes if PUID/PGID differ from a
# previous run (same reasoning as backend-entrypoint.sh's identical chown).
chown -R tts:tts "${TTS_MODEL_CACHE_DIR:-/data/model-cache}" "${TTS_CUSTOM_VOICES_DIR:-/data/custom-voices}"

exec gosu tts "$@"
