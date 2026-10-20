# Build context is the repo root (same reasoning as docker/backend.Dockerfile) so COPY can reach
# both local-tts/ and this Dockerfile's own entrypoint script.
FROM python:3.12-slim

WORKDIR /app/local-tts

# gosu: same reasoning as backend.Dockerfile — lets the entrypoint start as root to fix up the
# app user's UID/GID against the bind-mounted data directory, then drop to that user to run.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

# CPU-only torch, installed BEFORE pocket-tts and pinned to the dedicated CPU wheel index. Plain
# "pip install pocket-tts" resolves torch from PyPI's default index, which is the CUDA build — on a
# CPU-only host that pulls several GB of unused NVIDIA/cuDNN/NCCL libraries for nothing, and (as
# found while validating this service — see the project's local-TTS plan, Phase 0) can exhaust a
# small tmpfs /tmp mid-download. Once this is installed, pocket-tts' own torch requirement is
# already satisfied and pip won't touch it again.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY local-tts/pyproject.toml ./pyproject.toml
COPY local-tts/app ./app
RUN pip install --no-cache-dir .

COPY docker/local-tts-entrypoint.sh /usr/local/bin/local-tts-entrypoint.sh
RUN chmod +x /usr/local/bin/local-tts-entrypoint.sh

# Same default UID/GID convention as backend.Dockerfile (TrueNAS SCALE's built-in "apps" user) —
# this service shares the same bind-mounted data directory (see docker/docker-compose.yml), so it
# needs to write there as the same user.
RUN groupadd --gid 568 tts \
    && useradd --create-home --uid 568 --gid 568 tts \
    && mkdir -p /data \
    && chown -R tts:tts /data /app
# Deliberately no "USER tts" here — the entrypoint adjusts UID/GID first, then drops privileges.

EXPOSE 8080
ENTRYPOINT ["local-tts-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
