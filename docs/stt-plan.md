# Plan: STT rework — Parakeet only, Moonshine evaluation, VAD and hands-free input

Status: **phases 1–2 done**, phase 3 planned. Phase 1 ([stt-evaluation.md](stt-evaluation.md))
decided on 2026-11-09: Moonshine is not adopted for now; Parakeet runs on both the server and the
device. Written 2026-10-22 on branch `stt-rework` (all commits go there; the PR to
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
- **VAD: Parakeet's own VAD model or Silero VAD** (MIT, ~2 MB ONNX), decided by a short
  comparison at the start of phase 3. **End of turn: evaluate
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
server needs no changes for it. The VAD and Smart Turn are small enough for WASM on the CPU, so
they work on devices without WebGPU too.

0. **Which VAD.** Parakeet ships its own VAD model (`vad-model.onnx`, plus `preprocessor.onnx`
   for its mel features). The server already uses it to skip silence, and the WebGPU worker uses
   it to commit segments. Using it everywhere would mean no new dependency and one VAD on both
   paths. Measured so far (native ONNX Runtime, 1 thread): it has no state between calls, so it
   re-scores a window each time: 3.7 ms for 0.5 s of audio, 12 ms for 2 s. Its resolution is
   80 ms; Silero's is 32 ms. Download: 10.6 MB + 0.75 MB gzip, against ~2 MB for Silero. That
   matters only for devices without WebGPU, which today load no model at all.
   Compare both on short answers, background noise and children's voices (Silero v6.2 improved
   the last), the delay in detecting the end of speech, and speed in WASM without WebGPU. Pick
   Parakeet's VAD unless Silero is clearly better. The steps below say "VAD" for whichever wins.

   **Result (2026-11-09): Silero.** Test: 300 German Common Voice clips (CC0; 3.7 s of speech on
   average, 18 under 1.5 s), each as 1 s pause + clip + 2 s pause, run as a live stream. Both used
   the same rule: speech from p ≥ 0.5, end once p < 0.35 for 300 ms. Silero ran in 32 ms chunks
   with its state; Parakeet's VAD re-scored the last 1.5 s (or 3 s) every 160 ms.

   | Condition | VAD | Missed | False start in the pause | End not found | Onset p50 | End delay p50 / p90 |
   |---|---|---|---|---|---|---|
   | clean | Silero | 0.3 % | 0.3 % | 0 % | 176 ms | 364 / 446 ms |
   | clean | Parakeet 1.5 s | 0 % | 10.3 % | 0 % | 230 ms | 500 / 650 ms |
   | white noise, 10 dB SNR | Silero | 1.3 % | 0 % | 0 % | 218 ms | 350 / 518 ms |
   | white noise, 10 dB SNR | Parakeet 1.5 s | 0 % | 91.3 % | 11.7 % | 120 ms | 500 / 670 ms |
   | 4-voice babble, 10 dB SNR | Silero | 0 % | 92.0 % | 64.3 % | 16 ms | 820 / 2140 ms |
   | 4-voice babble, 10 dB SNR | Parakeet 1.5 s | 0 % | 93.0 % | 53.0 % | 80 ms | 760 / 2030 ms |

   - Parakeet's VAD fires on steady noise (a fan, a hissing mic). Stricter thresholds only reduce
     this: 0.9/0.7 still false-starts in 22 % of white-noise pauses and detects speech ~365 ms
     late. A 3 s window doesn't change the picture.
   - Silero notices the end of speech ~130 ms sooner.
   - Neither tells the student from other people talking nearby (babble): both treat it as
     speech. That's a limit of any VAD, not a difference between them. It argues for keeping
     push-to-talk as the default and for Smart Turn's turn-completion check in hands-free mode.
   - Compute on one thread, WASM (onnxruntime-web 1.31 in Node, Ryzen laptop): Silero 36 ms per
     second of audio, Parakeet 227 ms (1.5 s window). Natively: 5 vs. 52 ms. Parakeet's VAD would
     take about a quarter of a laptop core just for listening, more on an old iPad.
   - Not tested: children's voices (no freely licensed German children's speech found).

   Silero (`silero_vad.onnx`, v6.2, MIT, 2.3 MB) goes into `lib/vad.ts`. The server keeps using
   Parakeet's VAD to skip silence before decoding; that's an offline check on a whole segment,
   which it handles well.
1. **VAD in the browser** (one shared module, `lib/vad.ts`, in a worker; onnxruntime-web is
   already a dependency). Replace the RMS-based `watchForSpeechPauses` in the server fallback:
   segments are cut at real speech pauses instead of quiet moments, and leading/trailing silence
   isn't uploaded. Behaviour change for push-to-talk only; ships on its own.

   **Done (2026-11-09).** `scripts/fetch-stt-model.sh` also fetches Silero (pinned commit +
   checksum, `/models/silero-vad/v6.2/`). `workers/vadWorker.ts` runs it, and `lib/vad.ts` feeds
   it from the microphone. The server fallback loads it on the first recording, cuts a segment
   when speech has ended for 700 ms (once it is ≥ 2 s long), and doesn't upload segments without
   speech. A whole MediaRecorder segment can't be trimmed without re-encoding, so silence inside
   an uploaded segment stays. Without the VAD (still loading, or failed), the recording goes up
   uncut. Checked in Edge with a WAV as the microphone: the events match Silero in Python on the
   same file, and the public chat cut four utterances into four correctly transcribed segments
   and skipped the silent last one.
2. **Smart Turn feasibility** (v3.2, which improved short answers and noise). It expects
   Whisper-style 80-bin log-mel features of the last ≤ 8 s. First check
   [`@micdrop/smart-turn`](https://micdrop.dev/docs/client/turn-detection) (MIT, runs Smart Turn
   in the browser on WebGPU or WASM): which v3.x it ships, whether it needs cross-origin
   isolation (which LOLA can't use, see the Tally iframe), and its licence notice. Otherwise port
   the feature extraction to TypeScript, check outputs against the Python reference on a few
   clips, and measure inference time on WASM. Confirm the weights' licence on the model card. If it
   doesn't fit, end of turn falls back to a plain silence timeout.

   **Result (2026-11-09): feasible, with `@micdrop/smart-turn`; no port of our own.**
   - Package: `@micdrop/smart-turn` 0.3.0 (MIT, no dependencies, onnxruntime-web as a peer,
     which LOLA already has). It runs Smart Turn **v3.2**, weights BSD-2-Clause (model card of
     `pipecat-ai/smart-turn-v3`). It builds the features in TypeScript as the audio arrives
     (Whisper log-mel, last 8 s), so at the pause only the model call is left. One WASM thread
     needs no cross-origin isolation. By default it fetches the model from Hugging Face and the
     runtime from unpkg; both are configurable (`setSmartTurnOptions({ model, wasmPath })`), so
     we self-host the int8 model (`smart-turn-v3.2-cpu.onnx`, 8.3 MB) like Silero. Pre-1.0 and
     two days old: pin the exact version.
   - Accuracy in German: the full German part of Smart Turn's own test set
     (`pipecat-ai/smart-turn-data-v3.1-test`, CC BY 4.0, 1,322 clips, 659 complete), run through
     the package on onnxruntime-web WASM with one thread. Upstream's Python pipeline reports
     96.4 % for German with the same model.

     | Path | Accuracy | Unfinished judged complete | Complete judged unfinished |
     |---|---|---|---|
     | Whole turn at once (`predictOnce`) | 96.1 % | 5.0 % | 2.9 % |
     | Streamed in 100 ms batches, as the app would (`push` + `predict`) | 96.6 % | 3.9 % | 2.9 % |

     So the TypeScript front end is faithful.
   - Limit: it judges from prosody, not words. 150 Common Voice read sentences cut at a random
     point (40–70 % in, often mid-word) were judged "complete" 84–87 % of the time, with digital
     silence or room noise after the cut alike. The same sentences whole were judged complete in
     93–96 %. A student who hesitates sounds unfinished and is caught; a sentence that just stops
     mid-flow sounds finished. So Smart Turn may only *extend* the wait. It must never be the
     only thing that ends a turn early, and the maximum silence stays as the backstop.
   - Speed, at the pause (WASM, one thread, Node on the Ryzen 5 5625U): p50 152 ms, p90 167 ms.
     The package's README reports 194 ms on an M2 and ~0.8–1.2 s on emulated mid-range and slow
     phones, against ~25 ms on WebGPU. Building the features while the student speaks costs
     ~0.5 % of a core.
   - Not tested: real classroom speech and children's voices; a real iPad.

   For step 3 this means:
   - Run Smart Turn inside `workers/vadWorker.ts`, which already has the 16 kHz audio and the
     pause events, so the main thread stays free.
   - Use the int8 model in WASM everywhere (WebGPU is busy with Parakeet on the devices that
     have it).
   - Rule: VAD pause (≥ 300 ms), then Smart Turn. "Complete" sends; "unfinished" waits until
     speech resumes or the maximum silence (~2 s) is reached.
   - If the device is too slow (the first prediction takes over ~600 ms), skip Smart Turn for
     the visit and use a plain ~1 s silence timeout.
3. **Hands-free mode.**
   - Project field `voice_input_mode: "push_to_talk" | "hands_free"` (server default
     `push_to_talk`) + migration; configurator option; public chat reads it.
   - Flow: the student turns the mic on once → the VAD detects speech start → STT runs (device
     streaming or server segments) with live text → at a pause (~300 ms) Smart Turn asks "is the
     turn complete?" → if yes, or after a maximum silence (~2 s), the message is sent
     automatically.
   - Default for now, to avoid echo and barge-in problems: listening pauses while the reply is
     generated and spoken, and resumes afterwards. The mic button stays a visible on/off switch.
   - Engine boundary: the device engine is reached through one interface
     (`startStreaming`/`stop`, as today), so another engine (e.g. Moonshine later) drops in
     without touching the turn logic.

   **Done (2026-11-09).**
   - Backend: `Project.voice_input_mode` (migration `f8a9b0c1d2e3`, server default
     `push_to_talk`), in the project schemas, the export and the public project.
   - Configurator: a radio choice under "Voice input".
   - Models: `fetch-stt-model.sh` also fetches Smart Turn v3.2 int8 (pinned revision + checksum,
     with a BSD-2 `NOTICE.md`). The frontend uses `@micdrop/smart-turn` 0.3.0, exact pin; it runs
     in `workers/vadWorker.ts` on the worker's own ONNX runtime, not the package's web loader.
   - Turn rule: 300 ms pause, then Smart Turn decides. "Unfinished" ends the turn after 2 s of
     silence at most. If the model is missing or too slow (> 600 ms per prediction), a 1 s
     silence ends the turn instead.
   - Listening pauses after each turn and resumes once the reply has been spoken (the page's
     reply-pending state), or 1.5 s after a turn whose message never started a reply.
   - Switching off mid-turn sends what was said; the composer's send ends the current turn.
   - Push-to-talk projects never load Smart Turn.
   - Checked in Edge with a WAV as the microphone, on the server path (no WebGPU there):
     - each utterance became its own turn, sent ~0.4–0.7 s after the speech ended;
     - listening resumed after each reply;
     - switching off sent the turn in progress;
     - push-to-talk behaved as before.

     Not tested: the on-device (WebGPU) path in hands-free mode, a real iPad, real classroom
     noise.
4. **Latency Lab.** Report end-of-turn delay (speech end → message sent) so pause and threshold
   values can be tuned with data.

Check per step: `npm run build`, frontend-verifier screenshots of the public chat in both modes,
i18n parity, backend tests for the new project field.

## Open for later

- Barge-in (interrupting the avatar) — needs echo cancellation, and a policy for half-spoken
  replies.
- Pause length / Smart Turn threshold as a teacher setting, or fixed values.
- Moonshine as the on-device engine for devices without WebGPU (option B in
  [stt-evaluation.md](stt-evaluation.md)). Revisit when upstream publishes a WASM build with
  German and without cross-origin isolation. Release 0.1.7, then in preparation, rebuilds the WASM
  and makes streaming cheaper. A single-threaded build is still source-only.
