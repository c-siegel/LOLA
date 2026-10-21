<p align="center">
  <a href="../README.md">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/lola-icon-svgs/lola-wordmark-dark-bg.svg">
      <img src="../docs/lola-icon-svgs/lola-wordmark.svg" alt="LOLA" width="140">
    </picture>
  </a>
</p>

# LOLA knowledge service (optional)

Parses the documents teachers upload, indexes them and finds the passages that best match a
student's message, so the avatar's answers can be grounded in the teacher's own material — with
any LLM provider. The design is in [docs/rag-plan.md](../docs/rag-plan.md); this file only covers
running the service.

## Why a separate service?

- **Off by default, no cost when off.** The embedding model (several hundred MB of RAM) and the
  parsers only run when an operator enables the `rag` Compose profile and `RAG_ENABLED=true` in
  the backend.
- **Untrusted files stay away from the main app.** Uploads are parsed here, in a container that has
  no access to the main database, the uploads or the API-key encryption secret — and inside that
  container, every file is parsed in a separate, resource-limited subprocess.
- **Indexing can't slow down chats.** It runs on its own CPU budget.

Only the backend talks to this service. It publishes no port, and every route except `/health`
requires the shared `RAG_SERVICE_TOKEN`. It knows knowledge bases and documents only by the
backend's IDs; ownership, quotas and the dashboard live in the backend
(`backend/app/features/knowledge/`).

## How it works

| Step | Where | What |
|---|---|---|
| Upload checks | `app/parsing/checks.py` | Extension and content must agree (`%PDF-`, ZIP header), DOCX archives are checked for bombs, path tricks and macros before anything is decompressed. |
| Parsing | `app/parsing/sandbox.py`, `worker.py` | pypdf / lxml / plain text in a child interpreter with memory, CPU, file and time limits. Optional: Docling (`app/parsing/docling.py`). |
| Chunking | `app/chunking.py` | ~1400-character passages with overlap, never across a page. |
| Embedding | `app/embedding.py` | Local ONNX model (fastembed) by default; API models via litellm with the teacher's key. |
| Index | `app/store.py` | One SQLite file: chunks, FTS5 keyword index, sqlite-vec vectors. |
| Search | `app/search.py` | Vector + keyword search, merged with reciprocal rank fusion. |

A metadata header at the top of a Markdown or text file (front matter, `---` … `---`, as Arcana,
Obsidian or static site generators write it) is split off before chunking (`app/parsing/header.py`):
it describes the source and isn't part of the material. Its fields are reported in the document
status (`metadata`) for the backend, which keeps the ones it knows as source metadata; nothing of
it is embedded or searchable. Only a flat subset of YAML is read (`key: value`, quoted strings,
lists, `|`/`>` blocks), at most 8 KB, without a YAML library. A `---` line followed by anything
else (a horizontal rule) stays content.

Original files are deleted as soon as they've been indexed; only the extracted text, chunks and
vectors are kept. If indexing fails, the original stays for `RAG_FAILED_UPLOAD_RETENTION_HOURS`
(24 by default) so the teacher can retry with one click — possibly with Docling — and is deleted
after that, or as soon as the document is deleted.

## Running it

```bash
cd rag
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"

export RAG_SERVICE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export RAG_DATA_DIR="$PWD/.data"
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090
```

Then set the same `RAG_SERVICE_TOKEN`, `RAG_ENABLED=true` and
`RAG_SERVICE_URL=http://127.0.0.1:8090` for the backend. The first upload downloads the local
embedding model from Hugging Face into `$RAG_DATA_DIR/model-cache` (about 600 MB for the default).

On Windows the service runs too, but without the parser's memory and CPU limits (they rely on
POSIX `resource` limits, which Windows doesn't have; only the time limit applies). That's fine for
trying it out with your own files — the service logs a warning at startup — but real deployments
should use the Linux container.

Tests (no model download, no provider calls — see `tests/conftest.py`):

```bash
RAG_SERVICE_TOKEN=test ./.venv/bin/python -m pytest
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `RAG_SERVICE_TOKEN` | – | Shared secret, same value as the backend's. Required. |
| `RAG_DATA_DIR` | `/data` | Index database and in-flight uploads. |
| `RAG_MODEL_CACHE_DIR` | `<RAG_DATA_DIR>/model-cache` | Where the local model is downloaded to. |
| `RAG_LOCAL_EMBEDDING_MODEL` | `jinaai/jina-embeddings-v2-base-de` | One of the allowlisted models below. |
| `RAG_EMBEDDING_THREADS` | all cores | ONNX threads for the local model. |
| `DOCLING_URL` | unset | e.g. `http://docling:5001`; enables the "Docling" parser option. |
| `DOCLING_OCR_ENGINE` | `easyocr` | OCR engine Docling uses for scanned pages. |
| `RAG_HARD_MAX_UPLOAD_MB` / `RAG_HARD_MAX_PAGES` / `RAG_HARD_MAX_CHARS` | 100 / 2000 / 10,000,000 | Ceilings for the limits an admin sets in the dashboard. |
| `RAG_PARSE_MEMORY_MB` / `RAG_PARSE_TIMEOUT_S` | 1024 / 120 | Limits for one parser subprocess. |
| `RAG_INGEST_WORKERS` | 1 | Parallel indexing jobs. |
| `RAG_FAILED_UPLOAD_RETENTION_HOURS` | 24 | How long a failed document's original is kept for a retry. |
| `RAG_MAX_VECTOR_DISTANCE` | 0.75 | Vector hits further away than this (cosine distance) are dropped. |

Local embedding models (only these are accepted — each one's licence is permissive):

| Model | Licence | Notes |
|---|---|---|
| `jinaai/jina-embeddings-v2-base-de` | Apache-2.0 | German/English, long input. Default. |
| `intfloat/multilingual-e5-large` | MIT | Best multilingual quality, ~3× slower on a CPU. |
| `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | Apache-2.0 | Many languages, short chunks (128-token input). |

Changing the model later doesn't convert existing knowledge bases: they have to be re-created
(the dashboard says so), because vectors from different models can't be compared.

## Internal API

| Route | Purpose |
|---|---|
| `GET /health` | Liveness (no token). |
| `GET /capabilities` | Local model, file types, hard limits, whether Docling is configured. |
| `POST /documents` | Multipart `file` + JSON `meta`; checked synchronously, indexed in the background (202). |
| `POST /documents/status` | Status of several documents. |
| `POST /documents/{id}/retry` | Index a failed document again from its kept original (parser may change). |
| `DELETE /documents/{id}`, `DELETE /knowledge-bases/{id}` | Remove everything derived from them. Idempotent. |
| `POST /query` | Hybrid search over some knowledge bases. |
| `POST /embedding-test` | Embed one word with an API config (the dashboard's key test). |
| `POST /embed` | Embed up to 64 texts with a knowledge base's embedding config (used by the evaluation service, [rag-eval/](../rag-eval/)). |
| `GET /knowledge-bases/{id}/chunks?sample=30` | A random sample of a knowledge base's passages, to draft test questions from. |

Errors come back as `{"detail": {"code": "..."}}` with the codes in `app/errors.py`.
