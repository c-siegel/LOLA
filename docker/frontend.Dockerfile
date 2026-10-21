# Build context is the repo root (see .github/workflows/docker-publish.yml, "context:" — this isn't
# built via docker-compose.yml, that only pulls prebuilt images).

# --- Stage 1: build frontend/dist ---
FROM node:20-alpine AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Shown next to the version in the footer; there is no .git directory in the build context.
ARG APP_COMMIT=""
ARG VITE_DOCS_URL=""
ENV APP_COMMIT=$APP_COMMIT VITE_DOCS_URL=$VITE_DOCS_URL
RUN npm run build

# --- Stage 2: static files + reverse proxy in a slim Caddy image ---
# No Node runtime in the final image — only the finished dist/ output is carried over.
FROM caddy:2-alpine
# Unlike backend.Dockerfile's user, this one has nothing to adjust at container start, so a plain
# build-time USER is enough — docker-compose.yml overrides it with PUID/PGID anyway, so Caddy can
# read the speech recognition model wherever only the data directory's owner may. Caddy can still bind port 80 as this
# user: caddy:2-alpine's own Dockerfile already grants the `caddy` binary itself
# cap_net_bind_service via setcap, which isn't tied to running as root.
RUN adduser -D -u 1000 caddyuser
# For the one-off `stt-model` and `tts-model` services (see docker-compose.yml), which reuse this
# image to download the on-device models: curl isn't in the base image, and BusyBox's gzip lacks -k.
RUN apk add --no-cache curl gzip
COPY --chmod=755 scripts/fetch-stt-model.sh /usr/local/bin/fetch-stt-model
COPY --chmod=755 scripts/fetch-tts-model.sh /usr/local/bin/fetch-tts-model
COPY --from=build --chown=caddyuser:caddyuser /app/frontend/dist /srv
COPY --chown=caddyuser:caddyuser docker/Caddyfile /etc/caddy/Caddyfile
USER caddyuser
