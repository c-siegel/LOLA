# Plan: STT rework — Parakeet only, Moonshine evaluation, VAD and hands-free input

Status: **planned.** Written 2026-10-22 on branch `stt-rework` (all commits go there; the PR to
`working-branch` comes later).

## Goal

1. **Remove faster-whisper.** Parakeet Redux becomes the only local engine on the server, as it
   already is on the device.
2. **Evaluate Moonshine Voice** (language-specific streaming models) against Parakeet: accuracy,
   latency, size, and whether it runs in the browser. Evaluation only; adoption is a separate
   decision.
3. **Streaming transcription with VAD**, plus a hands-free mode next to push-to-talk, chosen per
   project by the teacher.

## Where we start

| | Device (public chat) | Server |
|---|---|---|
| Engine | Parakeet Redux, onnxruntime-web, **WebGPU only** | faster-whisper (default) or Parakeet (`STT_ENGINE=parakeet`, falls back to Whisper without the model files); a cloud key (SAIA) wins over both |
| Live text | Pseudo-streaming: the open segment is re-decoded every 0.5 s; Parakeet's own VAD head commits a segment at a pause (`workers/parakeetWorker.ts`) | Pause-cut segments over HTTP; pauses detected by an RMS threshold (`useVoiceInput.ts::watchForSpeechPauses`) |
| Language hint | none (Parakeet detects it) | Whisper/SAIA: `spoken_language`; Parakeet: none |
| Turn taking | Push-to-talk only | Push-to-talk only |

The same Parakeet files (`scripts/fetch-stt-model.sh`, ~380 MB) serve both sides. Spoken languages
are `de` and `en` (`frontend/src/lib/speechOptions.ts`).

## Decisions taken

- **No WebSockets.** Streaming stays on the device; the server keeps per-segment HTTP requests.
  Reasons: rate limiting and the password-unlock header are built around single requests, an open
  stream would hold one of the few server STT slots for a whole utterance, and proxies in front of
  other deployments may block upgrades.
- **Server model files are required.** Without them, server STT is unavailable (unless a cloud STT
  key is set). There is no silent fallback engine anymore.
- **VAD: Silero VAD** (MIT, ~2 MB ONNX). **End of turn: evaluate
  [Smart Turn v3](https://github.com/pipecat-ai/smart-turn)** (BSD-2-Clause, ~8 MB int8 ONNX,
  German among its languages).
- **Push-to-talk and hands-free both stay**, per project.
- **Evaluation audio:** public, freely licensed sets only (see phase 2).
- Hands-free details (barge-in while the avatar speaks, pause length) are decided later; phase 3
  ships the simplest safe default.

## Phase 1: evaluate Moonshine Voice

Deliverable: `docs/stt-evaluation.md` (results + recommendation) and a reproducible benchmark
script under `scripts/stt-benchmark/` with its own requirements. Nothing is added to the backend's
or frontend's dependencies in this phase.

**Candidates.** Parakeet Redux (as shipped) vs. Moonshine streaming Tiny and Small for `de` and
`en`. Only the streaming models are MIT; the older non-streaming non-English Moonshine models are
under a non-commercial licence and are out.

**Audio.** [FLEURS](https://huggingface.co/datasets/google/fleurs) `de_de` / `en_us` test splits
(CC BY 4.0, read sentences with reference transcripts), plus the German Common Voice test split
(CC0) if its download is straightforward. ~300 utterances per language. Caveat for the report:
adults reading, not students talking in a classroom.

**Measurements (server, CPU).**
- WER after normalisation (lowercase, no punctuation, numbers as written in the reference).
- **Short-utterance subset (< 3 s)** separately: that's where Parakeet's missing language hint
  should hurt most (wrong-language output, anglicised spelling).
- Real-time factor and time-to-final-text after end of speech, at `stt_cpu_threads` 1 and 4.
- Peak RAM, download size.

**Measurements (browser).**
- Does a Moonshine streaming model run in onnxruntime-web, and on **WASM (CPU) without WebGPU**?
  If yes, that's the main argument: Parakeet needs WebGPU, so many school devices currently fall
  back to the server.
- Load time and per-chunk latency on one laptop and, if possible, one iPad / Chromebook.
- Dependency licences of whatever JS runtime it needs.

**Decision criteria** (proposed): adopt Moonshine for a language if its WER is within ~1 point of
Parakeet's (or better on short utterances) **and** it is clearly smaller/faster or runs without
WebGPU. Possible outcomes: keep Parakeet; Moonshine on device only; Moonshine per language on both
sides. You decide after reading the report.

## Phase 2: remove faster-whisper

One topic per commit, roughly in this order.

1. **Own audio decoding.** `parakeet_local.py` imports `decode_audio` from `faster_whisper.audio`.
   Replace it with a small PyAV decode (WebM/Opus → 16 kHz mono float32). Keep `av` as a direct
   dependency; its `<19` cap only existed for faster-whisper, so lift it once a test against
   PyAV 19 passes (and drop the pitfall in `AGENTS.md`).
2. **Engine removal (backend).**
   - Delete `features/ai/stt/whisper_local.py`; `get_stt_client` returns `SaiaClient` for a cloud
     key, else `LocalParakeetClient`.
   - Missing model files: `LocalParakeetClient` raises a dedicated error, the route answers 503
     with a clear message (no Whisper fallback). Speech options / configurator report server STT
     as unavailable, like a missing key.
   - Settings: drop `stt_engine`, `stt_model`, `stt_model_cache_dir`; reword `stt_cpu_threads`.
     `extra="ignore"` means a stale `STT_ENGINE` in an existing `.env` does no harm.
   - Model: drop `Project.stt_server_engine` (+ schemas, Latency Lab engine override `whisper`).
     Alembic migration with `batch_alter_table`; `downgrade()` re-adds the nullable column.
   - `initial_prompt` **stays** in the interface and the public route: SAIA is Whisper-based and
     still uses it. Only the comments that attribute it to faster-whisper change.
3. **Frontend.** Configurator (`Step2Technical.tsx`): remove the server engine choice. Latency
   Lab: remove `server-whisper`. Credits page: drop the Whisper entry. i18n keys in `de.json` and
   `en.json`.
4. **Setup and deploy.** `scripts/dev.py setup` fetches the STT model (today only Docker does).
   Compose: drop `STT_MODEL_CACHE_DIR` / `whisper-cache`; the backend depends on the `stt-model`
   service. Entrypoint: stop creating `whisper-cache`.
5. **Docs.** `.env.example`, `README.md`, `backend/README.md`, `docker/README.md`,
   `ATTRIBUTION.md`, `AGENTS.md`, `docs/latency-test.md`; `backend/scripts/loadtest.py`.
6. **Tests.** Remove Whisper fakes; add: missing model files → 503 (regression for "no silent
   fallback"), PyAV decode of a WebM fixture, migration up/down.

Check: `python -m pytest`, `npm run build`, licence gate for `backend/`, `faster-whisper` and
`ctranslate2` gone from `pip list` after a fresh install.

## Phase 3: VAD, end-of-turn detection, hands-free mode

Turn detection runs **in the browser for both paths** (device STT and server fallback), so the
server needs no changes for it. Silero and Smart Turn are small enough for WASM on the CPU, so they
work on devices without WebGPU too.

1. **Silero VAD in the browser** (one shared module, `lib/vad.ts`, in a worker; onnxruntime-web is
   already a dependency). Replace the RMS-based `watchForSpeechPauses` in the server fallback:
   segments are cut at real speech pauses instead of quiet moments, and leading/trailing silence
   isn't uploaded. Behaviour change for push-to-talk only; ships on its own.
2. **Smart Turn feasibility.** It expects Whisper-style 80-bin log-mel features of the last ≤ 8 s.
   Port the feature extraction to TypeScript, check outputs against the Python reference on a few
   clips, measure inference time on WASM. Confirm the weights' licence on the model card. If it
   doesn't fit, end of turn falls back to a plain silence timeout.
3. **Hands-free mode.**
   - Project field `voice_input_mode: "push_to_talk" | "hands_free"` (server default
     `push_to_talk`) + migration; configurator option; public chat reads it.
   - Flow: the student turns the mic on once → Silero detects speech start → STT runs (device
     streaming or server segments) with live text → at a pause (~300 ms) Smart Turn asks "is the
     turn complete?" → if yes, or after a maximum silence (~2 s), the message is sent
     automatically.
   - Default for now, to avoid echo and barge-in problems: listening pauses while the reply is
     generated and spoken, and resumes afterwards. The mic button stays a visible on/off switch.
   - Engine boundary: the device engine is reached through one interface
     (`startStreaming`/`stop`, as today), so a Moonshine engine from phase 1 drops in without
     touching the turn logic.
4. **Latency Lab.** Report end-of-turn delay (speech end → message sent) so pause and threshold
   values can be tuned with data.

Check per step: `npm run build`, frontend-verifier screenshots of the public chat in both modes,
i18n parity, backend tests for the new project field.

## Open for later

- Barge-in (interrupting the avatar) — needs echo cancellation, and a policy for half-spoken
  replies.
- Pause length / Smart Turn threshold as a teacher setting, or fixed values.
- Whether to replace Parakeet's built-in VAD head inside the worker with Silero, so one VAD runs
  everywhere.
- Adopting Moonshine (after phase 1).
