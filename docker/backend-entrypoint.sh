#!/bin/sh
set -eu

# Runs as root at this point (see backend.Dockerfile) so it can adjust the app user's UID/GID to
# match whatever owns the bind-mounted data directory, instead of the image being locked to one
# fixed UID/GID. Defaults to 568/568 (TrueNAS SCALE's built-in "apps" user) — existing TrueNAS
# deployments don't need to set anything; anywhere else, set PUID/PGID in .env to match your host
# (see docker/README.md).
PUID="${PUID:-568}"
PGID="${PGID:-568}"

if [ "$(id -u lola)" != "$PUID" ]; then
  usermod -o -u "$PUID" lola
fi
if [ "$(id -g lola)" != "$PGID" ]; then
  groupmod -o -g "$PGID" lola
fi

# Not created automatically by the app — without this step, avatar/profile picture uploads and the
# first Whisper model download would fail on first use.
mkdir -p "${AVATAR_UPLOAD_DIR:-uploads/avatars}" "${PROFILE_PICTURE_UPLOAD_DIR:-uploads/profile-pictures}" \
  "${AVATAR_THUMBNAIL_UPLOAD_DIR:-uploads/avatar-thumbnails}" "${BACKGROUND_UPLOAD_DIR:-uploads/backgrounds}" \
  "${STT_MODEL_CACHE_DIR:-whisper-cache}" "${VOICE_CLIP_UPLOAD_DIR:-uploads/voice-clips}"
# The on-device speech recognition model's folder, bind-mounted by the web and stt-model services
# (see docker-compose.yml). Created here so the chown below covers it: if Docker created it first,
# while starting one of those services, it would belong to root and stt-model couldn't write to it.
mkdir -p /data/models
# Same for the optional knowledge (RAG) and evaluation services, which run without root and can't
# fix ownership themselves (see docker/rag.Dockerfile). Harmless when they aren't used.
mkdir -p /data/rag /data/rag-eval

# Re-applied on every start, not just once at image build time — /data is a bind mount, so its
# actual on-disk ownership is independent of whatever the image expects, and changes if PUID/PGID
# differ from a previous run.
chown -R lola:lola /data

# Must run from within backend/, otherwise Python can't find the app package (see the comment in
# backend/alembic/env.py). WORKDIR is already set to /app/backend in the Dockerfile. Both steps run
# as the (now correctly configured) app user, never as root.
gosu lola python -m alembic upgrade head

# Creates/promotes an admin account from ADMIN_EMAIL/ADMIN_PASSWORD if set — a no-op otherwise,
# and safe to run on every start (see app/cli/bootstrap_admin.py).
gosu lola python -m app.cli.bootstrap_admin

exec gosu lola "$@"
