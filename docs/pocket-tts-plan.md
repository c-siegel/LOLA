# Plan: Pocket TTS as a second local TTS engine (Latency Lab only)

Status: **plan only, nothing implemented.** Written 2026-10-19. Statements about Pocket TTS and its
ONNX exports come from the model cards and READMEs and still have to be verified in phase 0.

## Goal

Offer Kyutai's [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) next to the current local
engine (sopro-v2-turbo) in two places, both selectable **only in the Latency Lab**
(`frontend/src/pages/Dashboard/LatencyLab`):

- **Server:** in the `local-tts/` sidecar, full precision (no quantization).
- **On device:** in the browser via `onnxruntime-web`, with int8 quantization where it doesn't hurt
  quality.

Public chat, project settings and the default local engine (sopro) stay unchanged. Pocket only
leaves the Latency Lab if the device measurements show it is better.

## Why consider it, and why not just switch

| | sopro-v2-turbo (today) | Pocket TTS |
|---|---|---|
| Size | 120M | ~100M |
| Languages | en, pt-PT, fr, de | en, fr, de, pt, it, es (+ slower 24-layer variants) |
| CPU speed | RTF ~0.21–0.24 on an M3, ~300 ms to first audio | ~6× real time on 2 cores (M4), ~200 ms to first audio |
| Voice cloning | yes, reference clip | yes, WAV clip; voices exportable to safetensors |
| Default voices | none shipped (`local-tts/voices/` is empty) | preset voices, incl. German ("juergen") |
| Code licence | Apache-2.0 | MIT |
| Weights licence | Apache-2.0, not gated | CC-BY-4.0, **gated** on Hugging Face |
| Use restrictions | none | no cloning without consent, no deceptive/harmful use |
| Browser runtime | npm package `@soprotts/onnx-web` | community ONNX exports only, no JS library |

Gains: speed and footprint, preset voices, two more languages, a larger maintainer. Costs: gated
weights (`HF_TOKEN`), CC-BY attribution, and our own JS inference code for the browser. German
quality is unverified for both engines.

## Decisions taken

- **CC-BY-4.0 is acceptable** for model weights, with attribution on the Credits page (see phase 5).
- **Without `HF_TOKEN`, preset voices only is acceptable:** the sidecar still starts Pocket, but it
  speaks only with Kyutai's preset voices; a request with a teacher's clip gets a clear error.
- Quantization: none on the server; int8 only for `flow_lm_main` in the browser (see phase 4).

## Phase 0: checks before coding

1. **German quality on the server:** generate ~20 typical German avatar replies (numbers,
   abbreviations, a cloned teacher voice) with `pocket-tts` and sopro, listen side by side. Stop if
   Pocket is clearly worse.
2. **Dependency licences:** `pip-licenses` over a `pocket-tts` install — `local-tts/` is not covered
   by the CI licence gate.
3. **Gating:** confirm cloning needs the gated `kyutai/pocket-tts` weights + `HF_TOKEN`, and preset
   voices work from `kyutai/pocket-tts-without-voice-cloning` without one. Check each preset voice's
   licence (`kyutai/tts-voices`).
4. **Browser files:** pin a revision of the ONNX source (preferably
   [KevinAHM/pocket-tts-onnx](https://huggingface.co/KevinAHM/pocket-tts-onnx), the export the
   others mirror), note the German bundle's file sizes, pick a permissively licensed JS SentencePiece
   tokenizer (fallback: port unigram encoding; `tokenizer.model` is 59 KB).

## Phase 1: sidecar (`local-tts/`)

- Split `app/synthesis.py` into `app/engines/sopro.py` and `app/engines/pocket.py` behind one small
  interface (load, prepare reference, synthesize, sample rate). Clip storage and hashing stay shared;
  the reference cache is keyed by engine + clip.
- `POST /synthesize` takes an optional `"engine": "sopro" | "pocket"`, default `"sopro"` — existing
  backend calls are unaffected. `/health` lists the enabled engines.
- Pocket: `TTSModel.load_model(..., quantize=False)`, one model per language loaded on first use;
  map `de/en/fr/it/es/pt` to Pocket's language names; without a clip, the language's preset voice.
- Settings: `TTS_ENGINES` (default `sopro`; Pocket loads only if listed), `TTS_HF_TOKEN`.
- `pyproject.toml`: pin `pocket-tts` with an upper bound. Both engines accept CPU torch ≥ 2.5; the
  Dockerfile only changes through `pip install`.

## Phase 2: backend (latency route only)

- `LatencyTestMessageIn.tts_mode` gains `"local-pocket"`.
- `ChatContext.local_tts_engine: str = "sopro"`, passed through `synthesize_speech` to
  `LocalTTSClient(engine=...)`. Only `features/chat/latency_router.py` ever sets `"pocket"`.
- The local-TTS status endpoint (`useLocalTtsStatus`) reports Pocket availability from `/health`.
- Tests (`tests/test_routes_latency_test.py`, sidecar faked at httpx): `local-pocket` sends
  `engine: "pocket"`; public/preview chat send no engine; 400 when Pocket isn't enabled. Update the
  OpenAPI snapshot.

## Phase 3: frontend, server option

- `TtsMode` (`LatencyLab/metrics.ts`) gains `"local-pocket"`, shown only when the sidecar reports
  Pocket.
- Results and CSV export record a `ttsEngine` field.

## Phase 4: frontend, int8 in the browser

- `workers/pocketWorker.ts` + `lib/pocketTts.ts`, same message protocol as the sopro pair.
- Extract a `BrowserTts` interface (load / setReference / synthesize / dispose) so `turnRunner.ts`
  isn't hard-wired to sopro. New `TtsMode` `"browser-pocket"`; `"browser"` stays sopro so stored
  settings keep working.
- Port the inference from `pocket_tts_onnx.py` (~29 KB Python) to TypeScript: text conditioner,
  autoregressive `flow_lm_main` with state, `flow_lm_flow` sampling steps, streaming
  `mimi_decoder`; `mimi_encoder` for cloning.
- **Precision:** int8 only for `flow_lm_main`; `flow_lm_flow`, `mimi_decoder` and the encoders stay
  fp32 (Kyutai's runtime never quantizes those; an int8 Mimi decoder is reported to sound worse).
- **Runtime:** WASM on phones/tablets (same rule as sopro); measure WebGPU vs WASM on desktop, int8
  is often poorly supported on WebGPU.
- **Download:** a model-URL field defaulting to the pinned Hugging Face revision; only the tested
  language's bundle is fetched. No self-hosting yet.
- **Voices:** reuse the existing picker (teacher's clips / upload) plus the preset voice.
- New engine panel in `LatencyLab/index.tsx`; all strings in `de.json` and `en.json`.

## Phase 5: licence, credits, docs

- `AGENTS.md`: allow CC-BY-4.0 for model weights when credited on the Credits page.
- `frontend/src/pages/Credits/index.tsx`, following the Parakeet entry: Pocket TTS by Kyutai
  (CC BY 4.0), ONNX conversion by KevinAHM, note "converted to ONNX, partly quantized to int8",
  preset voice licences.
- `.env.example` (`TTS_ENGINES`, `TTS_HF_TOKEN`, Docker and local-dev local-TTS paths),
  `local-tts/README.md`, `docker/README.md`, `docs/latency-test.md`.

## Verification

- `python -m pytest` (backend), `npm run build` (frontend), the sidecar run by hand with both
  engines.
- Subagents: `backend-test-writer`, `i18n-checker`, `frontend-verifier`, `docs-sync`,
  `security-privacy-auditor` (sidecar token, voice uploads).
- **On device:** per iPad model, Parakeet + Pocket int8 + avatar loaded together; record time to
  first audio, real-time factor, and whether Safari reloads the tab during a 10-turn script.

## Out of scope for now

- Pocket as a project/public-chat engine.
- Self-hosting the ONNX files (a fetch script like `scripts/fetch-stt-model.sh`).
- A consent checkbox for voice uploads — required before Pocket leaves the Latency Lab, where only
  the logged-in teacher uses their own clips.

## Open question

- Which iPad models to test on — decides whether the 24-layer German variant is worth offering in
  the browser.
