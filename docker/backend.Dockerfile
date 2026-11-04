# Build context is the repo root (see .github/workflows/docker-publish.yml, "context:" — this isn't
# built via docker-compose.yml, that only pulls prebuilt images), so COPY can reach both backend/
# and this Dockerfile itself.
FROM python:3.15-slim

# libpq/gcc etc. are deliberately not installed here: cryptography, bcrypt and the rest of
# backend/pyproject.toml's dependencies ship prebuilt wheels for python:3.12-slim (manylinux,
# glibc), so there's nothing to compile at build time.
WORKDIR /app/backend

# gosu: lets the entrypoint start as root (needed to adjust the app user's UID/GID to match the
# host before touching the bind-mounted data directory, see PUID/PGID in backend-entrypoint.sh) and
# then drop to that user for the actual process. Unlike su/sudo, gosu forwards signals correctly and
# doesn't leave an extra supervisor process behind, so it behaves properly as the container's PID 1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

# pyproject.toml AND app/ both need to exist before "pip install .": setuptools builds a real wheel
# from the local package here (packages = ["app"], see backend/pyproject.toml) and fails with
# "package directory 'app' does not exist" if app/ isn't there yet (this used to be just
# COPY pyproject.toml + RUN pip install — the image never actually built that way, reproduced and
# verified locally with a bare "pip install ." missing app/).
# No separate dependency-layer caching is possible either, since pip install . always needs the
# package code — a lockfile-less requirements-only layer wouldn't be reproducible anyway (see the
# comment about the missing lockfile below).
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
# No lockfile in the repo (see backend/pyproject.toml) — pip resolves the pinned lower bounds in
# dependencies at build time. A `uv export`/`pip freeze` lockfile could be added later for
# reproducible rebuilds.
RUN pip install --no-cache-dir .

COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh

# App user — the process doesn't need root, only write access to the mounted data directory (see
# docker-compose.yml, bind mount on LOLA_DATA_DIR:/data). Created here with a default UID/GID
# of 568 — TrueNAS SCALE's fixed UID/GID for its built-in "apps" system user, which owns app data
# directories that TrueNAS itself creates — so existing TrueNAS deployments keep working without
# any extra configuration. On any other host, set PUID/PGID (see .env.example / docker/README.md)
# to whatever owns your bind-mounted data directory; the entrypoint adjusts this user to match at
# container start, before dropping privileges to it (see backend-entrypoint.sh).
# The image itself is therefore not tied to TrueNAS — only the *default* still favors it.
RUN groupadd --gid 568 lola \
    && useradd --create-home --uid 568 --gid 568 lola \
    && mkdir -p /data \
    && chown -R lola:lola /data /app
# Deliberately no "USER lola" here — the entrypoint starts as root so it can fix up the app
# user's UID/GID and the data directory's ownership first, then drops to that user itself.

EXPOSE 8000
ENTRYPOINT ["backend-entrypoint.sh"]
# --proxy-headers: read the real visitor address from X-Forwarded-For instead of reporting the
# reverse proxy's own container address for every request. Which senders are allowed to set that
# header comes from FORWARDED_ALLOW_IPS (see docker/docker-compose.yml) — uvicorn's default there
# is 127.0.0.1, which never matches Caddy's container address, so without it the header is ignored
# and every per-IP rate limit in app/core/rate_limit.py degenerates into one instance-wide bucket.
# (uvicorn enables proxy headers by default; passed explicitly so the intent survives a version bump.)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
