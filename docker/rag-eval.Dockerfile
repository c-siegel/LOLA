# Build context is the repo root (same reasoning as docker/backend.Dockerfile).
#
# The optional evaluation (Ragas) service — see rag-eval/README.md. Like the knowledge service it
# runs as an unprivileged user with a read-only root filesystem (see docker/docker-compose.yml);
# the backend's entrypoint creates and chowns its data directory.
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RAGAS_DO_NOT_TRACK=true

WORKDIR /app/rag-eval

COPY rag-eval/pyproject.toml ./pyproject.toml
COPY rag-eval/app ./app
RUN pip install --no-cache-dir .

RUN groupadd --gid 568 rageval && useradd --create-home --uid 568 --gid 568 rageval
USER 568:568

EXPOSE 8091
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091", "--workers", "1"]
