# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, …) working in this repository. Human contributors are welcome to read it too. Keep it short: link to the READMEs instead of copying them.

## What this is

LOLA lets researchers (primarily education researchers; teachers are a secondary audience until the app has been proof-tested and didactically evaluated) build a talking 3D avatar (persona = system prompt + LLM + voice + 3D face) and publish it as a public link. Visitors — usually students, without an account — chat with it by typing or speaking. Afterwards teachers can review the conversations in an analytics dashboard.

- `backend/` — FastAPI + SQLModel + Alembic (SQLite), litellm for LLM/TTS, faster-whisper/Parakeet for STT. Python ≥ 3.11. Details: [backend/README.md](backend/README.md)
- `frontend/` — React 18 + TypeScript + Vite, three.js via TalkingHead, react-query, i18next. Details: [frontend/README.md](frontend/README.md)
- `local-tts/` — optional self-hosted TTS sidecar. `rag/` — optional knowledge service (RAG): parsing, embeddings, search; see [rag/README.md](rag/README.md). `rag-eval/` — optional Ragas quality evaluation of the knowledge base; see [rag-eval/README.md](rag-eval/README.md). 
- `docker/` — images, Compose, Caddy.

## Commands

```bash
# Backend (from backend/)
pip install -e ".[dev]"
python -m pytest                          # conftest.py sets throwaway secrets; no .env needed
python -m alembic upgrade head
python -m alembic revision --autogenerate -m "Add foo to project"   # then review by hand
uvicorn app.main:app --reload             # http://localhost:8000, needs .env (see README)

# Local TTS sidecar (from local-tts/) — tests fake Pocket TTS, nothing is downloaded
pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -e ".[dev]"
python -m pytest

# Knowledge service (from rag/) — tests fake the embedding model, nothing is downloaded
pip install -e ".[dev]"
RAG_SERVICE_TOKEN=test python -m pytest

# Evaluation service (from rag-eval/) — tests fake the judge LLM at the litellm seam
pip install -e ".[dev]"
RAG_SERVICE_TOKEN=test python -m pytest

# Everything at once (from repo root): set up / start backend, frontend and the services enabled in .env
python scripts/dev.py setup
python scripts/dev.py

# Licence gate (in an environment with one component installed, plus pip-licenses)
sh scripts/check-licences.sh

# Frontend (from frontend/)
npm ci
npm run build                             # tsc -b + vite build — this is the type check
npm run dev                               # http://localhost:5173, proxies /api to :8000
```

There is no linter/formatter config. CI (`.github/workflows/tests.yml`) runs `pytest` and the licence gate for backend, `rag/`, `rag-eval/` and `local-tts/`, plus frontend `npm run build`, on pull requests to `working-branch`/`main` and before tagged releases (`docker-publish.yml`). Direct pushes aren't checked, so still run the ones you touched yourself before you push.

## Where things live

- `backend/app/features/<area>/` — one folder per domain (`auth`, `projects`, `chat`, `media`, `analytics`, `api_keys`, `users`, `site_settings`, `ai`), each split into `router.py`, `service.py`, `schemas.py`, `models.py`. Routers stay thin; logic goes in the service.
- `backend/app/features/ai/{llm,tts,stt}/` — provider implementations behind a `base.py` interface. Adding a provider: [backend/README.md#adding-an-ai-provider](backend/README.md#adding-an-ai-provider)
- `backend/app/core/` — config, auth deps, ownership checks, rate limiting, errors. `backend/alembic/versions/` — migrations.
- `frontend/src/` — `api/` (one module per backend area), `components/`, `layouts/`, `hooks/`, `lib/`, `types/`, `i18n/locales/{de,en}.json`.

## Rules

**Data and security**
- Teachers only ever see their own projects, keys, voice clips and conversations. Load a teacher's resource through the existing ownership checks (`get_owned_project`, or `get_owned`/`find_owned` from `app/core/ownership.py`) on every route; admin routes check the admin role.
- The public chat is unauthenticated: only published projects, password protection and rate limits must hold.
- Provider API keys are stored encrypted and are never returned to the client, logged, or exported.
- Conversations are student data: nothing personal in logs; exports and retention must keep working.

**Database**
- Every model change needs an Alembic migration in the same change. It must work on SQLite (use `op.batch_alter_table` for alter/drop, give new NOT NULL columns a `server_default`) and have a real `downgrade()`. Keep a single head.

**Frontend**
- Every user-visible string goes through `t()` and into **both** `de.json` and `en.json` (German is the fallback language).

**Tests**
- Backend tests use the fixtures in `backend/tests/conftest.py`, which fake providers at the litellm / model-loader seam. Never call a real provider or download a model in tests.
- Fixing a bug: add a regression test that fails without the fix.

**Dependencies**
- Only permissive licences (MIT, BSD, Apache-2.0, ISC, PSF, MPL-2.0 unmodified) — no GPL/LGPL/AGPL,  SSPL or non-commercial licences, also for model weights (CC BY 4.0 weights and voices are fine when credited on the Credits page, as for Pocket TTS). `scripts/check-licences.sh` enforces this in CI; local embedding models are allowlisted in `rag/app/embedding.py`.

**Docs**
- New env variable → `.env.example` (with a comment saying which deploy path uses it) and the matching README. New feature/provider/route → update the relevant README section.

**Code style**
- Code comments are always in English. When you come across a German comment, translate it.
- Comments explain *why*, not *what*. Match the naming and structure of the surrounding code.
- Don't add dependencies without a reason; pin a version cap with a comment when a newer major is known to break (see `av<19` in `backend/pyproject.toml`).

**Never commit** `.env`, `uv.lock`, `*.db`, `backend/uploads/`, `models/` or other generated files.

## Git and pull requests

- Before developing, ask on which branch it shall be committed.
- Pull requests always target `working-branch`.
- `.github/workflows/sync-working-branch.yml` merges `main` back into `working-branch` after every push to `main`.
- Dependency updates come from Renovate (`renovate.json`, overview in the "Dependency dashboard" issue). When you add or remove a version cap in a `pyproject.toml`, update the matching `packageRules` entry there.
- Commit subjects: imperative, sentence case, no prefix, no trailing period — e.g.
  "Add avatar-only and chat-only layouts for the public chat". The body explains why.
- Every commit message and PR description ends with one `Co-Authored-By:` line per model that contributed, e.g. `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Nothing comes after these lines: no session link, no `Claude-Session:` line, no session ID, no "Generated with" footer.
- One topic per commit; don't mix refactors with behaviour changes.

## Subagents

Project subagents live in `.claude/agents/`:

| Agent | Use it when |
|---|---|
| `backend-test-writer` | backend code changed or a bug is being fixed |
| `migration-reviewer` | a `models.py` changed or a migration was added |
| `i18n-checker` | user-visible frontend text changed |
| `frontend-verifier` | frontend changed — build + Playwright screenshots |
| `security-privacy-auditor` | auth, public chat, API keys, uploads, analytics/export changed |
| `docs-sync` | settings, env variables, providers or features changed |

## Skills

Project skills live in `.claude/skills/`:

- `ui-ux-pro-max` — searchable UI/UX guidance (styles, palettes, typography, UX/accessibility rules, React and three.js stack tips). Use it when designing or reviewing frontend UI. Vendored from [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) (MIT, see its `LICENSE`); only the runtime files are copied. Its suggestions don't override the rules above (e.g. every string still goes through `t()`). With `--persist`, pass `--output-dir` explicitly; the generated `design-system/` folder is not committed by default.

## Known pitfalls

- `av` (PyAV) is capped below 19: faster-whisper still passes an argument PyAV 19 removed.
- The 3D avatar (WebGL) and audio often don't work in headless browsers — verify the rest of the page instead of treating that as a bug.
- The knowledge service's parser subprocess (`rag/app/parsing/worker.py`) runs with `python -I` and must not import `app.config` — it gets no environment and only sees the file's bytes.
- On-device STT model files come from `scripts/fetch-stt-model.sh` (writes `./models`, ~380 MB, git-ignored). Without them the browser falls back to server-side STT.
- On-device TTS model files (Pocket TTS, German) come from `scripts/fetch-tts-model.sh` (~126 MB, git-ignored, CC BY 4.0). Pinned to a community export whose repo may move files — keep the checksums; don't commit the files or bake them into an image without the `NOTICE.md` it writes.
- The settings module validates `JWT_SECRET` / `API_KEY_ENCRYPTION_SECRET` at import time; the app refuses to start with placeholder values.