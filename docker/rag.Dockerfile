# Build context is the repo root (same reasoning as docker/backend.Dockerfile).
#
# The optional knowledge (RAG) service — see rag/README.md. Runs as an unprivileged user with a
# read-only root filesystem (see docker/docker-compose.yml), so unlike the backend image there's
# no root entrypoint adjusting UID/GID: Compose starts it directly as PUID:PGID, and the backend's
# entrypoint creates and chowns the shared data directory it writes to.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/rag

COPY rag/pyproject.toml ./pyproject.toml
COPY rag/app ./app
RUN pip install --no-cache-dir .

# Optional: bake the local embedding model into the image for hosts without internet access,
#   docker build --build-arg PRELOAD_EMBEDDING_MODEL=jinaai/jina-embeddings-v2-base-de ...
# and set RAG_MODEL_CACHE_DIR=/opt/rag-model-cache. By default the model downloads into the data
# volume on first use instead (like the speech recognition model), keeping the image small.
# The token only satisfies the settings check on import; nothing runs with it.
ARG PRELOAD_EMBEDDING_MODEL=""
RUN if [ -n "$PRELOAD_EMBEDDING_MODEL" ]; then \
      RAG_SERVICE_TOKEN=build-time-preload python -c "from app.embedding import preload_local_model; preload_local_model('$PRELOAD_EMBEDDING_MODEL', '/opt/rag-model-cache')"; \
    fi

RUN groupadd --gid 568 rag && useradd --create-home --uid 568 --gid 568 rag
USER 568:568

EXPOSE 8090
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
