<p align="center">
  <a href="../README.md">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="../docs/lola-icon-svgs/lola-wordmark-dark-bg.svg">
      <img src="../docs/lola-icon-svgs/lola-wordmark.svg" alt="LOLA" width="140">
    </picture>
  </a>
</p>

# LOLA local TTS (optional)

Self-hosted text-to-speech for projects that leave the TTS key unset, so no cloud key is needed to hear an avatar speak. Backed by Kyutai's [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (MIT code, CC BY 4.0 weights): a ~100M-parameter model, several times faster than real time on one CPU core.

This file covers running the sidecar; for the overview see the [root README](../README.md#optional-services).

## Why a separate service?

The main backend (`backend/`) stays lightweight on purpose — no PyTorch, no multi-hundred-MB
model download. This sidecar is its own process (and its own optional Docker container) so a
deployment that doesn't want the extra CPU/RAM/disk footprint never pays for it: the backend only
calls out to this service over HTTP, and only when `LOCAL_TTS_ENABLED` (backend setting; see root
README) is turned on.

In production it runs as the optional `tts-local` service in `docker/docker-compose.yml` (image
`ghcr.io/c-siegel/lola:local-tts`, enabled with the `local-tts` Compose profile — see
[docker/README.md](../docker/README.md#local-text-to-speech-optional)); in development, run it by
hand as below.

## Running it

```bash
cd local-tts
python3 -m venv .venv

# CPU-only build — the default PyPI torch wheel pulls several GB of unused CUDA/NVIDIA
# libraries even on a machine with no GPU.
./.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
./.venv/bin/pip install -e ".[dev]"

export TTS_MODEL_CACHE_DIR="$PWD/.cache"
export TTS_CUSTOM_VOICES_DIR="$PWD/.data/custom-voices"
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --app-dir .
```

At startup every language in `TTS_LANGUAGES` loads in the background (first start: ~220 MB
download per language from Hugging Face into `TTS_MODEL_CACHE_DIR`, about 20 s each; later starts
read the cache). Once loaded, a sentence starts after ~0.1 s and is generated at about four times
real time on one laptop core (measured on a Ryzen 5 5625U).

Then point the backend at it (see the [root README](../README.md#optional-services)):

```bash
export LOCAL_TTS_ENABLED=true
export LOCAL_TTS_URL=http://127.0.0.1:8080
```

Tests fake the model, nothing is downloaded: `./.venv/bin/python -m pytest`.

## Languages and voices

Pocket TTS has one model per language: German, English, French, Italian, Spanish, Portuguese and
Dutch (`de en fr it es pt nl`). `TTS_LANGUAGES` picks which ones this instance loads, ~0.5–0.7 GB
of RAM each. LOLA projects currently speak German or English.

The weights are pinned to Kyutai's October 2026 retrains (`app/synthesis.py`: `POCKET_REV`,
`GATED_REV`), newer than what `pocket-tts` 3.3.0 loads by default. The browser engine
(`scripts/fetch-tts-model.sh`) runs the same German weights.

Voices (`GET /voices` lists them, `app/voices.py` has the details):

- **Presets** — Kyutai's precomputed voices, available for every language without further setup:
  alba, anna, vera, fantine, charles, paul, eponine, azelma, george, mary, jane, michael, eve, daan
  (CC BY 4.0), marius, javert, bill_boerst, peter_yearsley, stuart_bell, caro_davy, estelle,
  giovanni, lola (CC0). Left out: cosette and jean (non-commercial licences), juergen and rafael
  (source not documented). Default per language: alba (de, en, pt), estelle (fr), giovanni (it),
  lola (es), daan (nl).
- **Donated voices** — the 228 CC0 recordings of Kyutai's
  [Unmute Voice Donation Project](https://huggingface.co/kyutai/tts-voices/tree/main/voice-donations),
  named by their donors. Only with voice cloning (below); `TTS_DONATED_VOICES` narrows the list.
- **Teachers' own clips** (Dashboard → Voices) — only with voice cloning. The backend stores them
  and sends one here the first time a project or preview needs it — see "API" below.

### Voice cloning

Cloning (teachers' clips and donated voices) needs the gated weights of
[`kyutai/pocket-tts`](https://huggingface.co/kyutai/pocket-tts): accept Kyutai's terms on Hugging
Face (among them: no voice cloning without the speaker's consent — LOLA's voice upload asks for
it) and set that account's token as `TTS_HF_TOKEN`. Without it — or if the token has no access —
the service speaks with presets only: a request for a clip or a donated voice uses the requested
preset or the language's default instead of failing, and `GET /voices` reports
`"cloning": false`, which the dashboard shows. A cloned voice is stored as a voice state in
`TTS_CUSTOM_VOICES_DIR/states/`, so each voice is cloned once per language.

## API

- `GET /health` — liveness check, always fast (doesn't wait for the models).
- `GET /voices` — `{"languages": [...], "cloning": bool, "voices": [{"id", "label", "kind",
  "licence"}]}`.
- `POST /synthesize` — `{"text": "...", "language": "de", "voice": null, "voice_sha256": null}`
  → WAV audio bytes (24 kHz). `voice` is a preset or `donation:<name>`; anything else falls back
  to the language's default. With `voice_sha256` (and cloning), the voice is cloned from that
  stored clip. Returns `404 VOICE_NOT_STORED` for a clip this service doesn't have (yet), `400`
  for a language not in `TTS_LANGUAGES`, `503` if the concurrency limit
  (`TTS_MAX_CONCURRENT_SYNTHESIS`) is hit and no slot frees up in time.
- `PUT /voices/{sha256}` — raw WAV body; stores a clip under its SHA-256 hash (checked). The
  backend calls this after a `404 VOICE_NOT_STORED` and retries, so the two services share no
  files and this one can run on another machine.
- `DELETE /voices/{sha256}` — forgets a stored clip and its cloned voice states (sent when a
  teacher deletes it).

## Settings

All read from environment variables prefixed `TTS_` (see `app/config.py` for the full list and
defaults): `TTS_LANGUAGES`, `TTS_HF_TOKEN`, `TTS_DONATED_VOICES`, `TTS_MODEL_CACHE_DIR`,
`TTS_CUSTOM_VOICES_DIR` (teachers' clips and cloned voice states — safe to clear),
`TTS_TORCH_THREADS`, `TTS_MAX_CONCURRENT_SYNTHESIS`, `TTS_SYNTHESIS_QUEUE_TIMEOUT_SECONDS`,
`TTS_MAX_VOICE_BYTES`.
