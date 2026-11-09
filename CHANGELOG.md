# Changelog

Notable changes to LOLA, newest first. Entries are collected under **Unreleased** while work lands on
`working-branch`; when `working-branch` is merged into `main` and tagged, that section becomes the
release. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases up to v0.6.0
are only recorded in the [git tags](https://github.com/c-siegel/LOLA/tags).

## [Unreleased]

### Added

- **Reasoning setting** per project (configurator, technical step): *Model default*, *Off*,
  *Always* or *Only for arguments*, plus an effort level (low, medium, high) where the model takes
  one. Works with OpenAI, Anthropic, Gemini, Ollama, OpenAI-compatible endpoints and GWDG Arcana,
  and is hidden for models that can't reason. See [docs/ai-providers.md](docs/ai-providers.md).
- **Only for arguments** (adaptive reasoning): only messages that argue, object or ask whether
  something is correct get the reasoning model; small talk is answered fast. Offered only where
  the knowledge service runs EmbeddingGemma 2 (`RAG_ENABLED=true`,
  `RAG_LOCAL_EMBEDDING_MODEL=onnx-community/embeddinggemma-2-ONNX`). For now simple word rules
  decide per message; a classifier on the embeddings is to replace them.
- **Model for difficult turns**: an optional second LLM key that answers the reasoning turns, so a
  small model can handle small talk and a larger one the arguments. If it fails, the first model
  answers.
- **Bridge phrase**: a turn that reasons starts with a short neutral sentence ("Einen Augenblick,
  ich denke kurz darüber nach.") in the project's voice, so students aren't left in silence. Can be
  switched off per project; on by default.
- **Hands-free voice input** per project: one click turns listening on, and each turn ends by
  itself at a pause (Smart Turn end-of-turn detection in the browser). Push-to-talk stays the
  default.
- **Reasoning budgets** in the public chat: reasoning replies are limited per visitor, per IP and
  per project and day. Over a budget a turn is answered without reasoning, never refused. Optional
  settings `DEEP_WINDOW_SECONDS`, `DEEP_MAX_PER_VISITOR`, `DEEP_MAX_PER_IP` and
  `DEEP_MAX_PER_PROJECT_PER_DAY` (see `.env.example`).
- **Analytics**: projects that reason record per reply whether it took the reasoning route and
  which model answered — shown in the transcript, in a new "Antwortweg" column of the CSV export
  and in a "Replies with reasoning" tile.
- **Latency test**: prompt, cached, reply and reasoning tokens per turn, the route each turn took,
  and a selector to force the fast or the reasoning route.

### Changed

- **Server speech recognition uses Parakeet only**, the same model as on-device recognition.
  The server now needs the model files: Docker's `stt-model` service downloads them on every
  `up`; for local development run `python scripts/dev.py setup` (or `scripts/fetch-stt-model.sh`).
  Without them, server transcription is unavailable. The same script now also fetches Silero VAD
  and Smart Turn for the browser.
- **Server-transcribed recordings are cut at speech pauses** (Silero VAD) instead of by a volume
  threshold.
- **The "Thinking" setting became "Reasoning"**: existing projects keep their choice (*On* became
  *Always*), and project exports with the old setting still import.
- **Uploaded audio is decoded with PyAV**; the cap `av<19` is gone.
- Dashes that set a phrase apart are spoken as pauses: speech output cut sentences off after a
  spaced dash ("… Kinder – und sogar einige Erwachsene – sehen das genauso" stopped at
  "Erwachsene"). The chat still shows the dashes.
- A reasoning call that fails or stays silent for 60 seconds falls back to a fast answer, and a
  provider that rejects the reasoning setting is retried without it.
- Streamed replies send keep-alive signals while a model is still thinking, and stop their work
  (and are not saved) when the student leaves or presses stop.
- `litellm` needs version 1.97 or newer.
- Four database migrations (`alembic upgrade head`; Docker runs them on start): *Thinking* becomes
  *Reasoning*, the second-model key and the bridge switch are added, the projects' STT engine
  choice is dropped, and the voice input mode is added.

### Removed

- **Whisper** (`faster-whisper`) as a server speech recognition engine, and with it the settings
  `STT_ENGINE`, `STT_MODEL` and `STT_MODEL_CACHE_DIR` (remove them from `.env` if set) and the
  projects' engine choice. Docker no longer uses the `/data/whisper-cache` folder; it can be
  deleted.

### Fixed

- Reasoning text that some local model servers write into the reply (`<think>…</think>`) is no
  longer spoken or shown.
- EmbeddingGemma 2 failed to load in the knowledge service with current onnxruntime versions
  ("External data path escapes model directory").

### Security

- Rate-limit entries now expire after their own time window, so requests with random visitor
  cookies can no longer fill the server's memory.
- Cached bridge-phrase audio, which can be in a teacher's cloned voice, is deleted together with
  its project, voice clip or account.
- Whether a model can reason is looked up in a bundled table only, never by contacting a provider.
