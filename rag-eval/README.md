<p align="center">
  <a href="../README.md">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/lola-icon-svgs/lola-wordmark-dark-bg.svg">
      <img src="../docs/lola-icon-svgs/lola-wordmark.svg" alt="LOLA" width="140">
    </picture>
  </a>
</p>

# LOLA evaluation service (optional)

Measures how well an avatar answers from its knowledge base, with
[Ragas](https://github.com/explodinggradients/ragas) (Apache-2.0). Teachers keep a set of test
questions, optionally with reference answers, per knowledge base; the backend asks the avatar
each question, sends the answers and retrieved passages here, and this service has a "judge" LLM
score them. It can also draft test questions from the material. The design is in
[docs/rag-plan.md](../docs/rag-plan.md); this file only covers running the service.

## Why a separate service?

Ragas pulls in a large dependency tree (LangChain, instructor, NumPy …) that neither the backend
nor the knowledge service needs. As its own image it only runs where an operator enables the
`rag-eval` Compose profile, and a Ragas upgrade can't break chats.

The service keeps no data of its own: the backend sends everything with each request — the test
material and the judge's API key (one of the teacher's own keys, held in memory for that
request) — and stores the results itself. Only cached prompt translations are written to disk.
Only the backend talks to it: it publishes no port, and every route except `/health` requires the
shared `RAG_SERVICE_TOKEN`.

## Metrics

| Metric | Question it answers | Needs |
|---|---|---|
| `faithfulness` | Is every claim in the answer backed by the retrieved passages? | passages |
| `answer_relevancy` | Does the answer address the question? | the knowledge base's embedding model |
| `context_precision` | Are the retrieved passages relevant, the relevant ones first? | passages |
| `context_recall` | Did retrieval find what the reference answer needs? | reference answer |
| `factual_correctness` | Does the answer agree with the reference answer? | reference answer |

| `coverage` | Do the retrieved passages contain what a question about the subject needs? | topic questions |
| `restraint` | Does the avatar admit what the material doesn't cover instead of making things up? | off-topic questions |

`coverage` and `restraint` (`app/checks.py`) are reference-free and one judge call each; for
other question kinds they're left out as `NOT_APPLICABLE`.

Scores run from 0 (bad) to 1 (good). A metric that doesn't apply to a question (no passages were
retrieved, no reference answer) is left out with a reason instead of counting as 0. A failed judge
call costs one cell, not the run. Answer relevancy embeds through the knowledge service's `/embed`,
so the knowledge base's own model is used and this service needs no model of its own.

For German material, the examples in Ragas' prompts are translated once per judge model and
endpoint (by that judge) and cached in `$RAG_EVAL_DATA_DIR/prompt-cache/`; the instructions stay
English. The cache isn't shared between endpoints, because the examples steer every later score
and a teacher can point a judge at an endpoint of their own.

Test questions come in three kinds that test different things:

- **grounded**: drafted one per sampled passage, one judge call each, with a reference answer.
  Tests whether retrieval finds the passage. They are easy by construction (the answer is in the
  material), so faithfulness and factual correctness come out high — that isn't a finding.
- **topic**: realistic student questions about the subject, written from the avatar's
  instructions, the descriptions and the document *titles*, without any passage text, so what the
  material is missing can show up (scored with `coverage`). One call for all of them, no reference
  answer: the judge's own knowledge could contradict the course material.
- **offtopic**: questions the material certainly doesn't cover (scored with `restraint`).

This replaces Ragas' own test set generator, which builds a knowledge graph over all documents
first and costs many calls before the first question. The teacher reviews every draft before it
is used.

## Running it

```bash
cd rag-eval
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"

export RAG_SERVICE_TOKEN=...            # the same value as for the backend and knowledge service
export RAG_SERVICE_URL=http://127.0.0.1:8090
export RAG_EVAL_DATA_DIR="$PWD/.data"
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8091
```

Then set `RAG_EVALUATION_ENABLED=true` and `RAG_EVAL_SERVICE_URL=http://127.0.0.1:8091` for the
backend.

Tests (no provider call — the judge is faked at the litellm seam, see `tests/conftest.py`):

```bash
RAG_SERVICE_TOKEN=test ./.venv/bin/python -m pytest
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `RAG_SERVICE_TOKEN` | — (required) | Shared secret with the backend and the knowledge service. |
| `RAG_SERVICE_URL` | `http://rag:8090` | The knowledge service, for `/embed`. |
| `RAG_EVAL_DATA_DIR` | `/data` | Cached prompt translations. |
| `RAG_EVAL_CONCURRENCY` | 4 | Judge calls in flight at once; providers rate-limit. |
| `RAG_EVAL_LLM_TIMEOUT_SECONDS` | 120 | Per judge call. |

Ragas' anonymous usage telemetry is switched off (`RAGAS_DO_NOT_TRACK=true`, set in
`app/__init__.py`).

## Internal API

| Route | Purpose |
|---|---|
| `GET /health` | Liveness and the Ragas version (no token). |
| `POST /score` | Score up to 20 question/answer/passages items with the requested metrics. |
| `POST /generate-testset` | Draft up to 50 questions: `kind: grounded` from sampled `chunks`, with reference answers; `topic` / `offtopic` from a `topic` context (instructions, descriptions, document titles, objectives, existing questions), without. |

Errors are `{"detail": {"code": "..."}}`; a judge that fails every call while drafting gives
`502 JUDGE_FAILED`. Validation errors list only the offending fields, never the input (it can
contain the judge's key).

## Upgrading Ragas

Ragas is pinned to a minor version (`>=0.4.3,<0.5`) because its API changed with every minor
release. All Ragas calls live in `app/scoring.py`, `app/judge.py` and `app/language.py`.
`langchain-community` is capped below 0.4: Ragas 0.4 still imports a module that 0.4 removed.
