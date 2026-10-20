# LOLA backend

LOLA's backend uses FastAPI as framework for building APIs with Python: user auth, avatar/project management, and calls out to LLM, text-to-speech (TTS), and speech-to-text (STT) providers.

See the [root README](../README.md) for what the app does and how to run it end to end — this
file only covers backend-specific details.

## Stack

- **FastAPI** — the web framework serving the API.
- **SQLModel** (SQLAlchemy + Pydantic) — database models, backed by **SQLite**.
- **Alembic** — database schema migrations (`alembic/versions/`).
- **litellm** — a single client that talks to different LLM/TTS providers, so the app isn't
  locked to one vendor.
- **faster-whisper** — runs speech-to-text directly inside the backend process (no separate
  service needed). Optionally replaced by **Parakeet Redux** on ONNX Runtime (`STT_ENGINE=parakeet`),
  the same model the browsers use for on-device recognition.
- **JWT (JSON Web Tokens) + bcrypt** — authentication and password hashing.

## Folder map

The code is organised by feature: each folder under `app/features/` holds one area's routes
(`*router.py`), its logic (`service.py` and friends), its database tables (`models.py`) and its
request/response shapes (`schemas.py`). Routers only deal with HTTP; services hold the logic and
raise `DomainError`s (`app/core/errors.py`) instead of HTTP exceptions.

| Path | Contents |
|---|---|
| `app/main.py` | Builds the app (`create_app()`): middleware, error handling, lifespan, `/health` |
| `app/api_router.py` | Mounts every feature's router under `/api/v1` (`app/core/urls.py`); the order matters for overlapping paths — see [API Reference](#api-reference) below |
| `app/core/` | Cross-cutting setup: settings (`config.py`), auth dependencies (`deps.py`), ownership checks (`ownership.py`), cookies, domain errors, middleware, public URLs (`urls.py`), the LLM/TTS/STT provider registry (`providers.py`), rate limiting, security helpers |
| `app/db/` | Database session/engine setup; `base.py` imports every feature's models for Alembic |
| `app/storage/` | Shared upload handling: content sniffing, saving/deleting files, cached file responses |
| `app/tasks/` | Background work: the periodic data-retention purge, and retrying deletes the knowledge service missed while it was down |
| `app/features/auth/` | Register, login, logout, password reset |
| `app/features/users/` | Own profile (`/me`), account deletion, admin account management (`/admin/users`) |
| `app/features/site_settings/` | Instance-wide settings: public (`/settings/public`) and admin (`/admin/settings`) |
| `app/features/pages/` | Admin-editable public pages — landing page texts, imprint, privacy policy, custom pages — per language with a change history: public (`/pages`) and admin (`/admin/pages`) |
| `app/features/projects/` | Project CRUD, publishing, YAML export/import, cached start-prompt audio |
| `app/features/chat/` | Public chat (`/public/{slug}`) and the configurator's preview chat; `pipeline.py` combines LLM → save → TTS for both |
| `app/features/ai/` | One package each for LLM, TTS and STT, with one module per provider behind a small interface (`get_llm_client`, `get_tts_client`, `get_stt_client`) — including the local-TTS sidecar and local Whisper fallbacks |
| `app/features/api_keys/` | The user's stored provider keys (`/api-keys`), the provider registry and speech-option status routes (`/providers`), key resolution and encryption |
| `app/features/media/` | Avatar model and background image libraries |
| `app/features/pronunciation/` | Each teacher's TTS word list (`/pronunciation`), preset packs (`presets/*.json`), import/export and the test box; the matching engine itself is `app/features/ai/tts/pronunciation.py` |
| `app/features/analytics/` | Dashboard stats, the saved-conversation list, CSV/ZIP export |
| `app/features/knowledge/` | Knowledge bases (RAG): metadata, quotas and upload checks here; parsing, embedding and search in the optional knowledge service (`rag/`), reached through `rag_client.py`. `retrieval.py` and `prompt.py` add the passages to each chat turn. Source metadata of documents (`metadata.py`, `sources.py`, BibTeX import in `bibtex.py`) only goes into the prompt, never into the index |
| `app/features/evaluation/` | Quality evaluation of knowledge-base answers: test sets, and runs that answer them through a project (`runner.py`, one background worker) and have the optional evaluation service (`rag-eval/`, Ragas) score them through `eval_client.py` |
| `alembic/` | Database migrations; `alembic/versions/` holds one file per schema change |
| `tests/` | Unit tests plus route-level tests (`test_routes_*.py`) and an OpenAPI contract snapshot (`test_openapi_contract.py`) that pins the HTTP interface |

## API Reference

Every route below is mounted under the `/api/v1` prefix (`app/core/urls.py`) — the paths in the
tables leave it out, so `GET /projects` means `GET /api/v1/projects`. The only exception is
`GET /health`, which is also reachable at the bare path. Each table names the router file that
defines its routes; request/response shapes are Pydantic models in the same feature's
`schemas.py`, and the route's `response_model=` names the one it returns.

URLs the API hands out for a browser to load directly (avatar and background files, thumbnails,
start-prompt audio, the profile picture) are absolute paths that already include `/api/v1`, so
the frontend uses them as-is.

**Auth column values:**

| Value | Meaning |
|---|---|
| Public | No login needed. |
| Public, rate-limited | No login, but capped per IP/email (`app/core/rate_limit.py`) against brute-forcing or spam. |
| Login required | Needs the `access_token` cookie set by `/auth/login` or `/auth/register` (`app/core/deps.py::get_current_user`). |
| Login required, own resource | Same, but also 404s (not 403) if the resource belongs to someone else — this hides whether it even exists, an IDOR (Insecure Direct Object Reference) defense. Checked by `app/core/ownership.py::get_owned` (or `find_owned`, which returns `None` instead). |
| Admin only | Login required, and the account's `is_admin` flag must be set (`get_current_admin`). |
| Owner, or public if published | 404 unless you own the resource — *or* it's attached to a published project, in which case anonymous visitors can fetch it too (e.g. an avatar file the public chat needs to display). |
| Visitor, rate-limited | No login — a published project's public chat. Visitors are tracked by an anonymous `ah_visitor_id` cookie, not a `User` account, and every route here is rate-limited per visitor. |

Every error response is `{"detail": "<CODE>"}` (a few routes nest it as
`{"detail": {"code": "<CODE>", "message": "..."}}` to add provider error text) — see
`app/core/error_codes.py`. Services raise these as `DomainError`s (`app/core/errors.py`); the
frontend translates the code into user-facing text, and the backend never sends prose directly,
except for two fixed German success messages on the forgot-password and reset-password routes.

### Health

| Method & path | Auth | Description |
|---|---|---|
| `GET /health` (also `GET /api/v1/health`) | Public | Liveness check for load balancers, monitoring, and the Docker healthcheck. |

### Auth — `app/features/auth/router.py` (prefix `/auth`)

Registration, login/logout, and the password-reset flow. Login sets an httponly cookie holding a
signed JWT (JSON Web Token); there's no bearer token for the frontend to manage. The current-user
check is `GET /me` (see [Me](#me--appfeaturesusersprofile_routerpy-prefix-me)).

| Method & path | Auth | Description |
|---|---|---|
| `GET /auth/registration-status` | Public | Whether self-registration is currently enabled. |
| `POST /auth/register` | Public, rate-limited | Create an account and log in, unless registration is disabled. |
| `POST /auth/login` | Public, rate-limited | Authenticate with email + password; sets the auth cookie. |
| `POST /auth/logout` | Public | Clear the auth cookie. |
| `POST /auth/forgot-password` | Public, rate-limited | Start a password reset for an email, if an account with it exists. |
| `POST /auth/reset-password` | Public | Complete a password reset using the token from the reset email. |

### Me — `app/features/users/profile_router.py` (prefix `/me`)

The logged-in user's own account: basic fields, a profile picture, password changes, signing out
of all sessions, and account deletion.

| Method & path | Auth | Description |
|---|---|---|
| `GET /me` | Login required | The currently authenticated user (what the dashboard checks on load). |
| `PUT /me` | Login required | Update profile fields. |
| `POST /me/picture` | Login required | Upload or replace the profile picture. |
| `DELETE /me/picture` | Login required | Remove the profile picture. |
| `GET /me/picture` | Login required | Serve the current user's own profile picture. |
| `PUT /me/password` | Login required | Change password; other sessions are signed out, this one stays signed in. |
| `POST /me/logout-everywhere` | Login required | Sign out of every session while keeping this one signed in. |
| `DELETE /me` | Login required | Permanently delete the account and everything belonging to it. |

### Projects — `app/features/projects/` (prefix `/projects`)

A project is one configured AI persona — avatar, system prompt, LLM, optionally TTS/STT — see
[Public chat](#public-chat--appfeatureschatpublic_routerpy-prefix-public) for the routes its
visitors use once published. Its avatar is referenced by `avatarModelId` (one of the user's
library models) or `builtinAvatar` (a bundled default like `"julia"`), its background by
`avatarBackgroundId`; responses also carry the ready-to-load `avatarModelUrl`/`avatarBackgroundUrl`.

| Method & path | Router | Auth | Description |
|---|---|---|---|
| `POST /projects` | `router.py` | Login required | Create a new project. |
| `GET /projects` | `router.py` | Login required | List the current user's projects. |
| `GET /projects/{project_id}` | `router.py` | Login required, own resource | A single project. |
| `PUT /projects/{project_id}` | `router.py` | Login required, own resource | Update a project. |
| `DELETE /projects/{project_id}` | `router.py` | Login required, own resource | Delete a project, its saved conversations, and its access logs. |
| `POST /projects/import` | `transfer_router.py` | Login required | Create a new draft project from a previously exported YAML file (format v2; v1 files still import). |
| `GET /projects/{project_id}/export` | `transfer_router.py` | Login required, own resource | Download the project's config as a YAML file (no API keys, publish state, or chat password). |
| `PUT /projects/{project_id}/publication` | `publication_router.py` | Login required, own resource | Publish a project under a fresh public share link. |
| `DELETE /projects/{project_id}/publication` | `publication_router.py` | Login required, own resource | Unpublish a project; the old link is never reused. |
| `POST /projects/{project_id}/start-audio` | `start_audio_router.py` | Login required, own resource | Synthesize and store the start-prompt audio once (MP3 from a cloud key, WAV from the local-TTS sidecar). |
| `GET /projects/{project_id}/start-audio` | `start_audio_router.py` | Owner, or public if published | Serve the pre-generated start-prompt audio file. |
| `POST /projects/{project_id}/chat/messages` | `app/features/chat/preview_router.py` | Login required, own resource | Send a message to the project's LLM and return the reply, for the in-app preview chat. |
| `POST /projects/{project_id}/chat/transcriptions` | `app/features/chat/preview_router.py` | Login required, own resource | Transcribe a voice message for the in-app preview chat. |
| `POST /projects/{project_id}/latency-test/messages` | `app/features/chat/latency_router.py` | Login required, own resource | Latency test: answer a message as SSE with per-module timings, optionally with another of the owner's LLM keys (`llmApiKeyId`), another TTS path (`ttsMode`: `project`/`local`/`none`) or without streaming (`streaming: false`). Never saved. |
| `POST /projects/{project_id}/latency-test/transcriptions` | `app/features/chat/latency_router.py` | Login required, own resource | Latency test: transcribe a recording on the server (`engine`: `project`/`whisper`/`parakeet`) and return `{text, sttMs, engine}`. |

### Public chat — `app/features/chat/public_router.py` (prefix `/public`)

The unauthenticated endpoints a published project's visitors actually use. See
[Latency monitoring](#latency-monitoring) below for the timing fields `/messages`,
`/messages/stream`, and `/transcriptions` ride along in their responses. `GET /public/{slug}`
also returns `browserSttModelUrl` (where the on-device speech recognition model lives) unless the
project or deployment opted out of transcribing in the visitor's browser. With the project's
`motionEnabled` on, replies (and each SSE `chunk`) also carry `motions`: the avatar gestures the
model marked, already removed from the text (see `features/chat/motion.py`). With a
conversation plan, `GET /public/{slug}` returns `checklist` (`{ordered, items: [{id, title}]}`)
when the teacher made it visible to students, never with the notes. Replies (each SSE `chunk`, the
`done` event, and `/messages`) carry `checks: [{id, done}]`: the plan steps ticked off or taken
back. The browser sends them back on its history entries, so the server can replay the progress
(see `features/chat/checklist.py`).

| Method & path | Auth | Description |
|---|---|---|
| `GET /public/{slug}` | Visitor, rate-limited | Load a published project's public info and record the visit. |
| `POST /public/{slug}/unlock` | Visitor, rate-limited | Verify a chat password and issue an unlock token for this project + visitor. |
| `POST /public/{slug}/messages` | Visitor, rate-limited | Send a chat message and get the full reply (text + optional audio) back at once. With `speakOnDevice: true` (only honoured where the project offers on-device speech output, see `browserTtsModelUrl` in `GET /public/{slug}`) the server skips its TTS and returns the reply's spoken form as `spoken` instead. |
| `POST /public/{slug}/messages/stream` | Visitor, rate-limited | Same, but streamed as SSE (Server-Sent Events): sentence-sized chunks, each with its own audio — or, with `speakOnDevice`, its spoken form as `spoken` — as soon as ready. |
| `POST /public/{slug}/transcriptions` | Visitor, rate-limited | Transcribe a visitor's voice message. |

### Avatar library — `app/features/media/avatars_router.py` (prefix `/avatars`)

Upload, list, and delete a user's 3D avatar models (`.glb` files, the glTF binary format the
frontend's three.js renderer loads) and their thumbnails. Uploads are checked by their actual
file content (magic bytes), not just the extension.

| Method & path | Auth | Description |
|---|---|---|
| `GET /avatars` | Login required | List the current user's avatar models. |
| `POST /avatars` | Login required | Upload a new `.glb` avatar model. |
| `GET /avatars/{avatar_id}/file` | Owner, or public if used by a published project | Serve the `.glb` model file. |
| `POST /avatars/{avatar_id}/thumbnail` | Login required, own resource | Attach a thumbnail image to an avatar model. |
| `GET /avatars/{avatar_id}/thumbnail` | Login required, own resource | Serve an avatar's thumbnail image. |
| `DELETE /avatars/{avatar_id}` | Login required, own resource | Delete an avatar model and its thumbnail. |

### Background library — `app/features/media/backgrounds_router.py` (prefix `/backgrounds`)

Upload, list, and delete background images shown behind the avatar in a project's chat. Same
content-sniffing validation as the avatar library.

| Method & path | Auth | Description |
|---|---|---|
| `GET /backgrounds` | Login required | List the current user's background images. |
| `POST /backgrounds` | Login required | Upload a new background image (PNG/JPEG). |
| `GET /backgrounds/{background_id}/file` | Owner, or public if used by a published project | Serve the background image file. |
| `DELETE /backgrounds/{background_id}` | Login required, own resource | Delete a background image. |

### Voice library — `app/features/media/voices_router.py` (prefix `/voice-clips`)

Each teacher's private voice clips for local voice cloning (local TTS only — a project picks one
via `ttsVoiceClipId`). Cloning needs the sidecar's `TTS_HF_TOKEN` (see
[local-tts/README.md](../local-tts/README.md#voice-cloning)); without it the sidecar speaks with
the project's preset voice (`ttsVoice`) instead. Uploads are content-sniffed, need a consent confirmation, must be 3–30 s
long, and are stored as normalized 24 kHz mono WAV whatever format came in.

| Method & path | Auth | Description |
|---|---|---|
| `GET /voice-clips` | Login required | List the current user's voice clips. |
| `POST /voice-clips` | Login required | Add a clip (multipart: `file`, `name`, `consent=true`) — an uploaded file or a browser recording. |
| `GET /voice-clips/{clip_id}/file` | Login required, own resource | Serve the clip's WAV file (never public). |
| `POST /voice-clips/{clip_id}/preview` | Login required, own resource | Speak `{text, language}` in the clip's cloned voice via the local-TTS sidecar; returns WAV. |
| `DELETE /voice-clips/{clip_id}` | Login required, own resource | Delete a clip; projects using it go back to the default voice and lose their start audio. |

### Pronunciation word list — `app/features/pronunciation/router.py` (prefix `/pronunciation`)

Each teacher's own list of terms the TTS should say differently, per spoken language (`de`/`en`).
It applies to everything their projects speak in that language — chat replies (whole and
streamed), the start audio and voice-clip previews — but never changes the displayed text. Rules
are plain text, not regular expressions: a term matches literally (a space matches any
whitespace), `{number}` stands for a number and is carried over into the spoken form, all rules
apply in one longest-first pass, and they run after Markdown is stripped and before numbers,
units and math become words. That built-in normalization is done by
[normt2s](https://github.com/c-siegel/normt2s), configured in `app/features/ai/tts/normalizer.py`;
the output of a rule is said as written, except that a rule whose spoken form contains a digit of
its own ("1 s" → "1 Sekunde") is normalized again, so the digit is read as a word (see
`compile_rules`). At most 500 terms per language.

| Endpoint | Auth | Description |
|---|---|---|
| `GET /pronunciation/entries?language=` | Login required | List the current user's terms (optionally for one language). |
| `POST /pronunciation/entries` | Login required | Add a term `{language, term, spoken, wholeWord, caseSensitive, spellOut}`; `spellOut` generates the spoken form letter by letter. 409 on a duplicate term. |
| `PUT /pronunciation/entries/{entry_id}` | Login required, own resource | Change a term (the language stays); an edited preset entry becomes the user's own. |
| `DELETE /pronunciation/entries/{entry_id}` | Login required, own resource | Delete a term. |
| `POST /pronunciation/import` | Login required | Import `{language, text, overwrite, dryRun}` — lines `term = spoken`, `term;spoken[;whole_word;case_sensitive]` or tab-separated; `!spell` as the spoken form spells the term out; `#` starts a comment. Invalid lines are reported, the rest is imported. |
| `GET /pronunciation/export?language=` | Login required | The list as semicolon CSV (the format the import reads), with spreadsheet formulas escaped. |
| `POST /pronunciation/preview` | Login required | The test box: `{text, language}` → the exact text the TTS gets plus the terms that matched; with `synthesize: true` also audio (base64) from one of the user's own TTS keys + voice, or the local sidecar + one of their voice clips. Audio previews are limited to 10 per minute per user. |
| `GET /pronunciation/presets?language=` | Login required | The shipped preset packs with their entries and how many of them the user has. |
| `POST /pronunciation/presets/{pack_id}/apply?language=` | Login required | Copy a pack into the user's list; terms they already have are kept. |
| `DELETE /pronunciation/presets/{pack_id}?language=` | Login required | Remove the entries a pack added that the user hasn't edited. |

A preset pack is a JSON file `app/features/pronunciation/presets/<id>.<language>.json` (see
`presets.py` for the format); its display name and description go into the frontend locales under
`pronunciation.presets.packs.<id>`. Every pack is validated by `tests/test_pronunciation_presets.py`.

### Analytics — `app/features/analytics/stats_router.py` (prefix `/analytics`)

Read-only numbers for the teacher-facing dashboards, scoped to the current user's own projects.

| Method & path | Auth | Description |
|---|---|---|
| `GET /analytics/stats` | Login required | Aggregate stats (session/message counts, ...), optionally filtered by project/period/model. |
| `GET /analytics/timeseries` | Login required | Session/message counts over time, bucketed by day/week/month, for charts. |
| `GET /analytics/overview` | Login required | Summary for the overview page: project/published counts, sessions/messages in the last 7 days. |

### Conversations — `app/features/analytics/conversations_router.py` (prefix `/conversations`)

The saved conversations behind the analytics table. One conversation (a "session" in the UI) is
one visitor's chat with a published project, not an HTTP/login session.

| Method & path | Auth | Description |
|---|---|---|
| `GET /conversations` | Login required | Paginated list of saved conversations, optionally filtered by project/period/model. |
| `GET /conversations/ids` | Login required | Every conversation id matching the current filters, ignoring pagination — backs "select all" before a bulk export. |
| `GET /conversations/{conversation_id}` | Login required, own resource | Full message-by-message transcript of one saved conversation. |
| `POST /conversations/export` | Login required | Export the given conversations as CSV (a single file) or a ZIP of CSVs (multiple). |
| `POST /conversations/batch-delete` | Login required | Permanently delete the given saved conversations. |

### API keys and providers — `app/features/api_keys/` (prefixes `/api-keys`, `/providers`)

Store, edit, test, and delete a user's own LLM/TTS/STT/embedding provider API keys — the "bring your own
key" feature — plus the provider registry so the frontend can build its key form without
duplicating that data. Keys are encrypted at rest.

| Method & path | Router | Auth | Description |
|---|---|---|---|
| `GET /providers` | `providers_router.py` | Login required | List all supported providers and their config. |
| `GET /providers/local-tts-status` | `providers_router.py` | Login required | Whether this deployment's local-TTS sidecar is enabled, and — if it answers — whether it clones voices, its languages and its voices (presets, plus donated voices with cloning) for the configurator's picker. Cached for a minute. |
| `GET /providers/browser-stt-status` | `providers_router.py` | Login required | Whether on-device (WebGPU) transcription is enabled for this deployment. |
| `GET /providers/browser-tts-status` | `providers_router.py` | Login required | Whether on-device speech output is offered (`BROWSER_TTS_ENABLED`) and the language of its model. |
| `GET /providers/server-stt-status` | `providers_router.py` | Login required | The deployment's default server STT engine and whether a project may pick Parakeet instead (its model files are present). |
| `GET /api-keys` | `router.py` | Login required | List the current user's stored keys, with how many projects use each one. |
| `POST /api-keys` | `router.py` | Login required | Store a new API key. |
| `PUT /api-keys/{key_id}` | `router.py` | Login required, own resource | Update a stored key; re-syncs any projects using it as their LLM source. |
| `DELETE /api-keys/{key_id}` | `router.py` | Login required, own resource | Delete a key; projects using it fall back to "no key configured". |
| `POST /api-keys/{key_id}/test` | `router.py` | Login required, own resource | Try the stored key against its provider and record whether it works. |

### Knowledge bases — `app/features/knowledge/router.py` (prefixes `/knowledge-bases`, `/knowledge-documents`)

A teacher's knowledge bases (RAG) and their documents. Every route answers `404
KNOWLEDGE_DISABLED` unless `RAG_ENABLED` is set; the work behind them happens in the optional
knowledge service (`rag/`, see [rag/README.md](../rag/README.md) and
[docs/rag-plan.md](../docs/rag-plan.md)). Projects attach knowledge bases through `PUT
/projects/{id}` (`knowledgeMode`, `knowledgeTopK`, `knowledgeBaseIds`).

| Method & path | Auth | Description |
|---|---|---|
| `GET /providers/rag-status` | Login required | Whether this deployment offers knowledge bases, the service's state, the upload limits and the user's storage use. |
| `GET /knowledge-bases` | Login required | The user's knowledge bases with document counts and how many projects use each. |
| `POST /knowledge-bases` | Login required | Create one, embedded with the local model or one of the user's embedding keys (fixed afterwards). |
| `PATCH /knowledge-bases/{kb_id}` | Login required, own resource | Rename or re-describe it. |
| `DELETE /knowledge-bases/{kb_id}` | Login required, own resource | Delete it with all documents, including their indexed text in the knowledge service. |
| `GET /knowledge-bases/{kb_id}/documents` | Login required, own resource | Its documents with their current indexing status. |
| `POST /knowledge-bases/{kb_id}/documents` | Login required, own resource | Upload one document (multipart `file`, `parser`, `consent`); indexed in the background. Rate-limited, size- and quota-checked. |
| `POST /knowledge-documents/{document_id}/retry` | Login required, own resource | Index a failed document again (optionally with Docling) while the knowledge service still keeps its original. Counts against the upload rate limit. |
| `DELETE /knowledge-documents/{document_id}` | Login required, own resource | Delete one document and its indexed text. |
| `POST /knowledge-bases/{kb_id}/search` | Login required, own resource | The passages a question would retrieve — the teacher's test search. |
| `PATCH /knowledge-documents/{document_id}/metadata` | Login required, own resource | Replace the teacher's own source details (title, author, year, container, url, citation, sourceType, priority, note, bibtexKey). Empty fields fall back to the linked bibliography entry, then to the file's header. Never re-indexes. |
| `POST /knowledge-bases/{kb_id}/bibliography` | Login required, own resource | Replace the knowledge base's bibliography with a BibTeX file (≤ 1 MB, ≤ 2,000 entries) and link documents to entries by key (from the file's header, the entry's `file` field, or the file name). Counts against the upload rate limit. |
| `GET /knowledge-bases/{kb_id}/bibliography`, `DELETE …` | Login required, own resource | List the entries (for the key picker) / delete them; documents keep their keys. |

### Evaluation — `app/features/evaluation/router.py` (prefixes `/test-sets`, `/test-cases`, `/evaluation/runs`)

Measuring how well a project answers from its knowledge bases, with Ragas as LLM judge (see
[rag-eval/README.md](../rag-eval/README.md) and [docs/rag-plan.md §7](../docs/rag-plan.md)).
Every route answers `404 EVALUATION_DISABLED` unless both `RAG_ENABLED` and
`RAG_EVALUATION_ENABLED` are set. The judge is one of the user's own LLM keys (not Arcana); its
decrypted key only goes to the evaluation service with each request. Runs answer through the
project's real retrieval and prompt, without speech and without saving a conversation, and keep
copies of questions, answers and passages — so they are deleted with the knowledge base, the
project or the account.

| Route | Access | Purpose |
|---|---|---|
| `GET /providers/evaluation-status` | Login required | Whether this deployment offers evaluation, the service's state, the per-run question cap and the judge calls per metric (for the cost estimate). |
| `GET /knowledge-bases/{kb_id}/test-sets` | Login required, own resource | The knowledge base's test sets with question counts. |
| `POST /knowledge-bases/{kb_id}/test-sets` | Login required, own resource | Create one (`name`, `language`: `de`/`en`). |
| `GET /test-sets` | Login required | All the user's test sets (for the run form). |
| `PATCH /test-sets/{id}`, `DELETE /test-sets/{id}` | Login required, own resource | Rename / delete it with its questions and every run that used it. |
| `GET /test-sets/{id}/cases`, `POST /test-sets/{id}/cases` | Login required, own resource | List / add questions with optional reference answers and a `kind` (`grounded`, `topic` (default) or `offtopic`; at most 500 per set). |
| `POST /test-sets/{id}/cases/import` | Login required, own resource | Add questions from a CSV (`question`/`frage`, optional `reference`/`referenz` and `kind`/`art`; `,` or `;`; at most 1 MB). |
| `GET /test-sets/{id}/cases/export` | Login required, own resource | The approved questions as CSV (formula-guarded for spreadsheet apps). |
| `POST /test-sets/{id}/generate` | Login required, own resource | Draft up to 10 questions with a judge key (`kind`): `grounded` from a sample of the material, with reference answers; `topic` / `offtopic` (need `projectId`, optional `objectives`) from the project's instructions, the knowledge base's description and the document titles only — the judge never sees passage text — without reference answers. Drafts start unapproved. |
| `DELETE /test-sets/{id}/drafts` | Login required, own resource | Discard the unapproved drafts. |
| `PATCH /test-cases/{id}`, `DELETE /test-cases/{id}` | Login required, own resource | Edit or approve / delete one question. |
| `GET /evaluation/runs` | Login required | The user's runs (optionally `?projectId=`). |
| `POST /evaluation/runs` | Login required, own resources | Start a run (`projectId`, `testSetId`, `judgeApiKeyId`, `metrics`) in the background. One active run per user; capped by the admin's questions-per-run setting. |
| `GET /evaluation/runs/{id}` | Login required, own resource | Progress, configuration snapshot, summary (overall and per question kind) and per-question results. |
| `POST /evaluation/runs/{id}/cancel` | Login required, own resource | Stop after the current question or batch; what's scored is kept. |
| `DELETE /evaluation/runs/{id}` | Login required, own resource | Delete it. |
| `GET /evaluation/runs/{id}/export?format=csv\|json` | Login required, own resource | Download the results. |

### Admin — `app/features/users/admin_users_router.py` and `app/features/site_settings/admin_router.py` (prefix `/admin`)

Account management and instance-wide settings for the admin dashboard. There's deliberately no
admin-initiated delete endpoint — disabling (`User.enabled`) is how an admin removes access; the
only way an account is actually deleted is the self-service `DELETE /me` above. A disabled
account's published chats go offline with it (404, including their avatar, background and greeting
audio) and come back unchanged when it's re-enabled.

| Method & path | Auth | Description |
|---|---|---|
| `GET /admin/users` | Admin only | List every account on this instance. |
| `POST /admin/users` | Admin only | Create an account with a temporary password the new user must change on first login. |
| `PUT /admin/users/{user_id}` | Admin only | Promote/demote or enable/disable an account. |
| `POST /admin/users/{user_id}/reset-password` | Admin only | Set a user's password on their behalf; they must change it on next login. |
| `GET /admin/settings` | Admin only | The instance-wide site settings (contact email, self-registration toggle, retention, knowledge-base limits, questions per evaluation run). |
| `PUT /admin/settings` | Admin only | Update the instance-wide site settings; knowledge limits are checked against the knowledge service's ceilings. |
| `GET /admin/settings/knowledge-ceilings` | Admin only | The highest values the knowledge limits may be set to. |

### Site settings — `app/features/site_settings/public_router.py` (prefix `/settings`)

| Method & path | Auth | Description |
|---|---|---|
| `GET /settings/public` | Public | The public-facing site settings (imprint details, whether self-registration is open) — see `/admin/settings` for the full, admin-only version. |

### Editable pages — `app/features/pages/` (prefixes `/pages`, `/admin/pages`)

Admins can replace the built-in texts of the landing page, the imprint (`impressum`) and the privacy
policy (`datenschutz`) per language, and add pages such as an accessibility statement, served by the
frontend at `/p/{slug}`. Imprint, privacy and custom pages are Markdown (rendered without raw HTML);
the landing page has fixed fields (headline, hero text, feature cards) plus an optional Markdown
section. A visitor sees their language, then English, then the built-in template; the landing page
falls back field by field. Saving a language empty resets it. Every change is recorded with its
author and time; deleting an account keeps the entries but drops the author.

| Method & path | Auth | Description |
|---|---|---|
| `GET /pages` | Public | The published custom pages linked from the site footer, with their title per language. |
| `GET /pages/{slug}` | Public | A page's texts in every language it has; no texts means the built-in template. Unpublished or empty custom pages are 404. |
| `GET /admin/pages` | Admin only | Every page (built-in ones first) with its texts and last change. |
| `POST /admin/pages` | Admin only | Create a custom page (slug, published, footer link and order). |
| `GET /admin/pages/{id}` | Admin only | One page with its texts. |
| `PATCH /admin/pages/{id}` | Admin only | Change a custom page's slug, published state or footer link; built-in pages can't be changed here. |
| `DELETE /admin/pages/{id}` | Admin only | Delete a custom page with its texts and history. |
| `PUT /admin/pages/{id}/translations/{locale}` | Admin only | Save the text for `de` or `en`; an empty text resets that language. |
| `DELETE /admin/pages/{id}/translations/{locale}` | Admin only | Reset one language to the fallback. |
| `GET /admin/pages/{id}/history` | Admin only | Who changed the page when, newest first. |

## Service-layer functions

Routers stay thin — they read the request, check auth, and delegate the actual work to a service
function, which raises a `DomainError` (rendered as `{"detail": "<CODE>"}`) instead of an HTTP
exception. These are the functions to call when extending the backend or writing a script/test
against it directly, without going through HTTP. Every function below is public (no leading
underscore); helpers named `_like_this` are file-private and left out. Paths are relative to
`app/`.

| File | Purpose | Key functions |
|---|---|---|
| `features/ai/llm/` | LLM chat completion (litellm, plus a direct integration for GWDG Arcana) | `get_llm_client(api_key_record)` → `.complete(request)`, `.stream(request)`, `.test()`.<br>`complete(api_key_record, ChatRequest(...))` — one-shot reply.<br>`stream(api_key_record, ChatRequest(...))` — text deltas, falling back to a plain call if streaming fails before the first delta. |
| `features/ai/tts/` | Text-to-speech (TTS) synthesis | `synthesize_speech(text, tts_voice, api_key_record, language, pronunciation=None)` — normalizes the text for speech (with the teacher's word list, if given), routes to the right provider, or the local-TTS sidecar for `api_key_record=None`, and returns `(audio_bytes, content_type)`. Raises `VoiceRequiredError` if the provider needs a voice that wasn't given.<br>`get_tts_client(api_key_record)` — the provider client itself. |
| `features/ai/tts/normalizer.py` | Making text speakable (via normt2s) | `normalize_for_speech(text, language, pronunciation=None, applied=None)` — Markdown stripped, numbers, units and math as words, the teacher's word list applied.<br>`speech_stream(language, pronunciation=None)` — a stream for a live LLM reply: `feed_pieces(delta)`/`flush_piece()` release `Piece(raw, spoken)` at sentence ends. |
| `features/ai/tts/pronunciation.py` | Compiling a teacher's word list | `compile_rules(rules)` → a cached normt2s `RuleSet` of `normt2s.Rule(term, spoken, whole_word, case_sensitive)`. `spell_out(term)`, `placeholder_count(text)`. |
| `features/pronunciation/service.py` | Managing the word list | `list_entries(...)`, `create_entry(...)`, `update_entry(...)`, `delete_entry(...)`.<br>`rules_for(session, user_id, language)` — what to pass as `synthesize_speech(..., pronunciation=)`.<br>`import_text(...)`, `export_csv(...)`. |
| `features/pronunciation/presets.py` | Preset packs | `all_packs()`, `get_pack(id, language)`, `apply_pack(...)`, `remove_pack(...)`, `applied_count(...)`. |
| `features/ai/stt/` | Speech-to-text (STT) transcription | `transcribe_audio(audio_bytes, language, initial_prompt, api_key_record)` — locally via faster-whisper or Parakeet (`Settings.stt_engine`), or a cloud provider if configured.<br>`get_stt_client(api_key_record)` → `.transcribe(...)`; a SAIA client also has `.test()`.<br>`capacity.transcription_slot()` — limits concurrent local transcriptions. |
| `features/chat/pipeline.py` | One chat turn for the public and preview chat | `prepare_chat(session, project)` — resolve keys and snapshot the project.<br>`reply_turn(context, turn)` — LLM → save → TTS.<br>`stream_turn(context, turn)` — `(event, data)` pairs for the SSE stream. |
| `features/api_keys/resolve.py` | Which of a project's API keys to use | `resolve_llm_key(session, project)`, `resolve_tts_key(...)`, `resolve_stt_key(...)`.<br>`get_user_api_key(...)`, `get_owned_key_of_type(...)` — lookups.<br>`provider_from_model(llm_model)`, `browser_stt_model_url_for(project)`, `effective_api_base(key)`. |
| `features/api_keys/service.py` | Managing stored keys | `list_keys_with_usage(session, user_id)`, `create_key(...)`, `update_key(...)`, `delete_key(...)`, `run_key_test(session, key)`. |
| `features/api_keys/crypto.py` | Encrypting and masking stored API keys | `store_api_key(plaintext)` / `reveal_api_key(ciphertext)`.<br>`mask_key(plaintext)` — e.g. `"••••••••1234"`.<br>`scrub_key_from_text(text, encrypted_api_key)` — redact a key out of error text before it's shown or logged. |
| `features/auth/service.py` | Authentication helpers | `register_user(session, name, email, password)`, `authenticate_user(session, email, password)`, `user_to_out(user)`. The auth cookie itself is set by `core/cookies.py::set_auth_cookie`. |
| `features/auth/password_reset.py` | Password reset flow | `request_password_reset(session, email)` — issues a token and emails the link.<br>`reset_password(session, raw_token, new_password)`. |
| `features/projects/service.py` | Project CRUD | `create_project(session, user_id, data)`, `apply_update(session, project, data)`, `list_projects(...)`, `delete_project(...)`.<br>`sync_llm_model(session, project)` — derive `llm_model` from the linked key. |
| `features/projects/publish.py` | Publishing projects | `publish_project(session, project)` — assigns a fresh share-link slug.<br>`unpublish_project(session, project)` — the old slug is never reused. |
| `features/projects/start_audio.py` | Cached start-prompt audio | `generate_start_audio(session, project)`, `servable_start_audio(session, project_id, user)`. |
| `features/projects/export.py` | Project YAML export/import | `export_project_yaml(project)`, `parse_project_yaml(raw)`, `import_project(session, user_id, data)`. |
| `features/media/service.py` | Avatar, background and voice libraries | `create_avatar(...)`, `set_avatar_thumbnail(...)`, `delete_avatar(...)`, the background equivalents, and `is_used_by_published_project(session, column, item_id, owner_id)`.<br>`list_voice_clips(...)`, `create_voice_clip(...)`, `delete_voice_clip(session, clip)`, `voice_reference_for_project(session, project)`. |
| `features/media/voice_audio.py` | Voice clip validation | `normalize_voice_clip(content)` → (WAV bytes, duration) — sniffs, decodes and resamples an upload, enforcing 3–30 s. |
| `features/analytics/service.py` | Analytics queries behind the dashboard | `get_stats(...)`, `get_project_overview(...)`, `get_sessions_paginated(...)`, `get_session_ids(...)`, `get_timeseries_data(...)`.<br>`get_conversation_detail(...)`, `get_conversations_for_export(...)`, `delete_conversations(...)`. |
| `features/analytics/csv_export.py` | Conversation CSV/ZIP export | `build_export(rows)`, `build_conversation_csv(conversation, project)`, `conversation_export_filename(...)`. |
| `features/users/service.py` | Own profile and admin account management | `update_profile(...)`, `change_password(...)`, `set_profile_picture(...)`.<br>`create_user_as_admin(...)`, `admin_reset_password(...)`, `admin_update_user(session, admin, target, data)` — guards against self-lockout and removing the last admin. |
| `features/users/account.py` | Account deletion | `delete_user_account(session, user)` — cascades to projects, keys, conversations, the pronunciation word list, and uploaded files; detaches the account from the page history. |
| `features/site_settings/service.py` | Instance-wide site settings | `get_or_create_site_settings(session)`, `update_site_settings(session, data)`. |
| `features/pages/service.py` | Admin-editable public pages | `list_pages(session)` (creates the built-in rows on first use), `get_public_page(session, slug)`, `footer_pages(session)`.<br>`create_page(...)`, `update_page(...)`, `save_translation(session, author, page, locale, data)`, `reset_translation(...)`, `delete_page(...)`, `history(...)` — every change is recorded as a `SitePageRevision`. |
| `features/chat/motion.py` | Avatar gestures the LLM marks as `::name::` in its reply (projects with `motion_enabled`) | `MOTIONS` — the ten offered gestures (must match `frontend/src/vendor/motion-engine/motions.json`).<br>`MOTION_PROMPT` — default gesture instructions, appended to the system prompt; a project's own `motion_prompt` replaces it (`effective_motion_prompt`, `compose_system_prompt`). `GET /projects/{id}/system-prompt` returns the assembled prompt.<br>`extract_markers(text)` — cleaned text plus each gesture's and each plan tick's position. |
| `features/chat/checklist.py` | Conversation plan the LLM ticks off with `::done_N::` / `::undone_N::` (projects with `checklist_enabled`) | `checklist_for(project)` — the plan, or None when it is off or empty.<br>`replay(checks_per_message)` — the step IDs done after the history's ticks.<br>`checklist_prompt(checklist, done_ids)` — the plan for the system prompt, with a one-line status. |
| `features/chat/streaming.py` | SSE framing for streamed replies | `sse_event(event, data)` for one SSE frame. The reply is split into chunks by `normalizer.py::speech_stream`. |
| `features/chat/unlock.py` | Chat password verification | `verify_chat_password(project, password)`, `issue_unlock_token(project, visitor_id)`.<br>`is_unlocked(...)` / `assert_unlocked(...)` — check vs. raise variants. |
| `features/chat/visitor_name.py`, `visitor_log.py` | Visitor name and visit logging | `clean_visitor_name(...)`, `assert_visitor_name_provided(...)`, `log_access(session, project_id, visitor_id)`. |
| `features/auth/email.py` | Transactional email | `send_password_reset_email(to_email, reset_link)`. |
| `tasks/retention.py` | Data-retention cleanup | `purge_expired_data(session)` — deletes conversations/access logs past the configured retention period. |

## Adding an AI provider

[`app/core/providers.py`](app/core/providers.py) is the single source of truth for which LLM/TTS
providers a user can connect with their own API key (see the
[root README](../README.md#supported-ai-providers) for the current list). The frontend's
provider dropdown, model list, and validation are all generated from this one registry — adding
a provider is one `ProviderSpec` entry here, not a change in several places. Most providers go
through [litellm](https://github.com/BerriAI/litellm); a provider with a non-standard API (like
Cartesia or GWDG Arcana) gets its own module in `app/features/ai/tts/` or `app/features/ai/llm/`
instead, picked by that package's `get_*_client()` factory.

## Security & limits

A few backend behaviors worth knowing about if you're deploying or extending this:

- **Startup validation.** `JWT_SECRET` and `API_KEY_ENCRYPTION_SECRET` (`app/core/config.py`) are
  checked at process startup, not just required to be present — a placeholder, too-short, or
  invalid-Fernet-key value fails the boot with a clear error instead of running with a guessable
  or broken secret.
- **Rate limiting** (`app/core/rate_limit.py`) protects every endpoint that doesn't require login:
  public chat messages, voice transcription, chat-password unlock attempts, and login/register/
  password-reset — each limited per visitor/IP (public chat) or per IP/email (auth), sized for a
  school-class-sized burst of traffic. It's an in-memory sliding window, sized for a single
  process — swap it for something shared (e.g. Redis) before running more than one backend worker.
- **Public chat messages are capped** at 8000 characters each (`app/features/chat/schemas.py`),
  including the conversation history a client echoes back on every request — otherwise nothing
  stopped an anonymous visitor from sending arbitrarily large text against the project owner's own
  LLM API key.
- **A stored key's endpoint (`api_base`) is validated** against cloud-metadata addresses and the
  deployment's own Docker service names (`app/features/api_keys/schemas.py`) — see the [root README](../README.md#supported-ai-providers)
  for what this does and doesn't restrict.
- **Data retention** (`app/tasks/retention.py`) is enforced both at startup and
  periodically (every 6 hours, see `app/main.py`) for as long as the process runs, not just once
  per restart.
- **Conversation exports are CSV-injection-safe** (`app/features/analytics/csv_export.py`): a
  visitor name or message starting with `=`, `+`, `-`, or `@` is escaped before being written to
  the exported CSV/ZIP, so it can't turn into a live spreadsheet formula when a teacher opens it.
  The pronunciation word-list export does the same (and its import strips the escape again).
- **Pronunciation rules are never regular expressions.** Teachers' terms are escaped before they
  are compiled (`app/features/ai/tts/pronunciation.py`), so a word list can't inject a pattern or
  cause catastrophic backtracking; the only syntax is the `{number}` placeholder.

## Latency monitoring

The public chat endpoints in `app/features/chat/public_router.py` time themselves with `time.perf_counter()`
and ride the results along as extra fields in their normal JSON responses — there's no separate
metrics endpoint or database, just numbers the frontend console-logs for debugging. `None` always
means a stage didn't run at all (e.g. TTS disabled or no key configured), never "it was instant".

| Endpoint | Field(s) | What it measures |
|---|---|---|
| `POST /{slug}/messages` | `llmMs` | Wall-clock time inside the LLM (large language model) call. |
| | `ttsMs` | Wall-clock time inside the TTS (text-to-speech) call. `null` if TTS didn't run. |
| `POST /{slug}/messages/stream` (SSE `done` event) | `llmMs` | Time from request start to the full LLM reply being assembled. |
| | `firstChunkTextReadyMs` | Time until the first sentence chunk was handed to TTS (isolates LLM/chunking speed from TTS speed). |
| | `firstChunkMs` | Time until that first chunk's TTS synthesis *finished*. |
| | `llmFirstTokenMs` | Time until the LLM's first token arrived. |
| | `ttsMs` | Summed synthesis time across all chunks. |
| (SSE `chunk` events) | `textReadyMs`, `ttsMs`, `sentMs` | Per chunk: when its text was handed to TTS, how long its synthesis took (`null` without TTS), and when it was sent — all since the request started. |
| `POST /{slug}/transcriptions` | `sttMs` | Wall-clock time inside the STT (speech-to-text) call — local Whisper or Parakeet, or the cloud provider. |

How to use: open the public chat page with `?latencyTest=1` appended to the URL — the frontend logs
these numbers to the browser console, combined with client-side timings (network round trip, audio
decode/playback, time to first spoken word). See [frontend/README.md](../frontend/README.md#debugging)
for the full breakdown and what each logged field means. The dashboard's latency test page
(`/dashboard/latency`, routes `/projects/{id}/latency-test/*` above) shows the same numbers per
message and compares configurations; the `chunk` and `done` fields are the same there.

## Tests

`tests/` covers the pure functions and schema validators (text-stream parsers, the `api_base`
SSRF guard, rate limiting, the retention loop) and, through the `test_routes_*.py` files, the HTTP
routes end to end: accounts and admin, projects and their export, the public chat (plain and
streamed), media and voice clips, API keys, pronunciation, analytics, knowledge bases and the
evaluation. AI providers are faked at the litellm / model-loader seam (see `tests/conftest.py`), so
no test calls a real provider or downloads a model. Run them with:

```bash
cd backend && pytest
```

Tests need no setup: `tests/conftest.py` sets throwaway `JWT_SECRET`/`API_KEY_ENCRYPTION_SECRET`
values and keeps the repo root `.env` out (`LOLA_ENV_FILE=""` for the settings,
`LITELLM_MODE=PRODUCTION` so litellm doesn't load it either), so your local settings can't change
the results. Outside tests, `LOLA_ENV_FILE` (a real environment variable — it can't be set
from inside a `.env`) points the backend at a different `.env` file.

CI (`.github/workflows/docker-publish.yml`) runs this suite, plus the frontend build, before a
tagged release's Docker images are published.