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

### Phase 0 results (2026-10-19)

Measured with `pocket-tts` 3.3.0, `sopro` 2.2.0, torch 2.14.1+cpu on an AMD Ryzen 5 5625U laptop
(6 cores / 12 threads, Windows). Scripts and audio stayed local; the numbers are from 12 German
test sentences (greetings, school content, dates, prices, abbreviations, numbers).

**1. Speed and quality (both cloning the same 10 s German reference clip, full precision)**

| | Pocket TTS | sopro |
|---|---|---|
| RTF (lower = faster) | **0.54** (2 threads) | 0.87 (6 threads) |
| Preparing the voice | 1.8 s | 0.3 s |
| Time to first audio (streaming) | 280 ms | – (sidecar doesn't stream) |
| Mean WER, faster-whisper medium round trip, raw text | 33.2 % | 8.8 % |
| Same, numbers/abbreviations spelled out (7 sentences) | ≈ 0 % | ≈ 0 % |

- Pocket's RTF stays at ~0.45–0.52 for 1, 2 and 4 threads — more cores don't help a single
  request, they only help concurrency. The advertised "6× real time" doesn't hold on this CPU
  (≈2× here).
- **Pocket can't read digits, symbols or abbreviations.** Plain sentences are fine (0 %), but
  "1990", "12,50 €", "S. 47", "3²", "84 Mio." come out as nonsense ("Das kostet dinn Spaß…").
  Spelled out by hand, all seven problem sentences are recognised correctly, on par with sopro.
  LOLA's `normalize_for_speech` doesn't help today: it handles Markdown, decimals, symbols and
  CO2, but leaves integers, years, "€" and abbreviations untouched.
- So Pocket needs a German verbalizer (numbers, ordinals/dates, years, currency, units, common
  abbreviations) in `backend/app/features/ai/tts/normalizer.py`. sopro would profit too (it also
  stumbled over "S.", "u. a." and "3²"). Watch the licence: `num2words` is LGPL and not allowed —
  write it ourselves (German number words are a small, well-defined grammar).
- WER is an intelligibility proxy, not a quality verdict — the listening test stays with a human.

**2. Licences of the Python dependencies:** all permissive. The only gate failure is `pocket-tts`
itself: MIT licence file, but no licence metadata — needs a `REVIEWED` entry like `tiktoken` if
`local-tts/` ever gets the gate.

**3. Gating — confirmed.** `kyutai/pocket-tts` is gated (`auto`), `kyutai/pocket-tts-without-voice-cloning`
is not (CC-BY-4.0). Without a token, `load_model()` falls back to the ungated weights, preset voices
work, and cloning raises a `ValueError` (map it to a clear sidecar error).

**4. Preset voices — not all usable.** German model presets by source licence (from
`kyutai/tts-voices`):

| Licence | Voices |
|---|---|
| CC0 | marius, javert, bill_boerst, peter_yearsley, stuart_bell, caro_davy, estelle, giovanni, lola |
| CC-BY-4.0 | alba, anna, vera, fantine, charles, paul, eponine, azelma, george, mary, jane, michael, eve, daan |
| **Non-commercial — must be blocked** | **cosette** (Expresso), **jean** (EARS) |
| Undocumented | **juergen**, rafael |

`juergen` is the only native German preset, and its source licence is undocumented — ask Kyutai
before making it the German default; until then use an allowlist of CC0/CC-BY voices.

**5. Server pitfalls found**
- `import pocket_tts` calls `torch.set_num_threads(1)` process-wide — in a shared sidecar it would
  slow sopro down. Set threads explicitly per engine.
- `pocket-tts` opens its YAML configs without an encoding: crashes on Windows unless Python runs in
  UTF-8 mode (`PYTHONUTF8=1` / `-X utf8`). Irrelevant for the Linux image, relevant for local dev.

**6. Browser — the plan's basis is gone**
- The ONNX export (`KevinAHM/pocket-tts-onnx`, rev `58a6d00`, April 2026) is built from **older
  German weights and an older tokenizer** than the server uses (weights and tokenizer changed by
  September; `languages/german/model.safetensors` hash differs). Browser and server would compare
  different models.
- Its export code (`github.com/KevinAHM/pocket-tts-onnx-export`) and `wasm-pocket-tts` both return
  404 now; sherpa-onnx only has an English Pocket model from January 2026.
- Sizes of the April German bundle with the planned precision mix: `flow_lm_main` int8 76 MB,
  `flow_lm_flow` 39 MB, `mimi_decoder` 42 MB, `text_conditioner` 16 MB, `mimi_encoder` 40 MB —
  ~213 MB with cloning, ~173 MB without.
- Tokenizer: current weights ship a `tokenizer.json`, readable by `@huggingface/tokenizers`
  (Apache-2.0, ~360 KB, maintained) — no SentencePiece port needed.

### What phase 0 changes in this plan

- **New phase 1a, before the sidecar — done:** German verbalizer
  (`backend/app/features/ai/tts/verbalizer.py`), applied to every TTS engine, cloud ones included
  (decided 2026-10-19). Each rule is switchable through `TTS_VERBALIZER` (`all` / `none` / a list
  of rule names); tests in `backend/tests/test_speech_verbalizer.py`.
- **Phase 1:** set torch threads per engine; allowlist preset voices (CC0/CC-BY only; `cosette` and
  `jean` never selectable); default German voice from the allowlist until `juergen` is cleared.
- **Phase 4 (browser) needs a decision**, the April ONNX export is the wrong model:
  - **A. Measure with the April export anyway.** Same architecture and size as the current model,
    so latency, memory and download numbers on iPads are valid — quality numbers are not. Its
    tokenizer is SentencePiece; convert it once to `tokenizer.json` and use `@huggingface/tokenizers`.
    Cheapest way to learn whether on-device Pocket is feasible at all.
  - **B. Export the current weights to ONNX ourselves** (`torch.onnx.export` of the flow LM, flow
    net, Mimi encoder/decoder with explicit streaming state; int8 via
    `onnxruntime.quantization`). Matches the server exactly, but it's a project of its own with no
    upstream code left to follow.
  - **C. Defer the browser part** until Kyutai ships official browser support.
- **Checked 2026-10-19: [stephvax/pocket-tts-onnx](https://huggingface.co/stephvax/pocket-tts-onnx)**
  (rev `aace8dc`, October 2026, CC-BY-4.0) is a better export than KevinAHM's (reported 1.4× faster,
  294 vs 723 ms to first audio, 330 MB less memory, fp16 KV cache, same WER) — but **not newer for
  German**: its `german_24l/tokenizer.model` is byte-identical to Kyutai's April revision
  (`d29db79`), and German exists only as the 24-layer variant, with a 303 MB int8 main model
  (~440 MB per bundle, four times the layers). Its language-aware exporter isn't published; the
  public `export_onnx.py` in [VolgaGerm/PocketTTS.cpp](https://github.com/VolgaGerm/PocketTTS.cpp)
  (MIT) is the English-only upstream version, but it exports, quantizes (`quantize_dynamic`, MatMul
  only) and validates against PyTorch, and takes `--config`. That makes option B much cheaper:
  adapt that script to `german.yaml` (6 layers) at the current revision and the `tokenizer.json`.
- **Checked 2026-10-19: [prasadvittaldev/pocket-tts-german-onnx-int4](https://huggingface.co/prasadvittaldev/pocket-tts-german-onnx-int4)**
  (rev `e11ec07`, 106 MB, CC-BY-4.0): KevinAHM's April export re-quantized (same tokenizer hash
  `389079b9`, same state manifests) — main model int4 (MatMulNBits), flow net and Mimi decoder int8,
  Mimi encoder int8 included. No WER published, browser runtime not open source. Useful notes:
  ONNX Runtime Web can't load external `.onnx.data` files (export single-file); a precomputed voice
  state's `step` must equal its cache length or the voice is ignored.

### Precision benchmark (2026-10-19)

Same 12 sentences after the verbalizer, preset voice `alba`, temperature 0.3 (Kyutai's default; the
ONNX runner defaults to 0.7), 2 seeds = 24 clips per variant, ONNX Runtime 1.31 CPU / PyTorch, 4
threads, Ryzen 5 5625U. WER from a faster-whisper medium round trip with reference and transcript
both passed through `normalize_for_speech`, so "3" vs "drei" is no error. RAM is the Python process
(runtime overhead included) — compare variants, don't read it as browser memory.

| Variant | WER | Clips with errors | Model files | Peak RAM | First audio | RTF |
|---|---|---|---|---|---|---|
| A: PyTorch, Sept weights, fp32 (server) | **6.2 %** | 9/24 | – | 1039 MB | 301 ms | 0.45 |
| B: ONNX, April, all fp32 | 11.2 % | 13/24 | 400 MB | 853 MB | 180 ms | 0.46 |
| C: ONNX, April, main int8 + flow/decoder fp32 | 7.5 % | 8/24 | 173 MB | 631 MB | 147 ms | **0.36** |
| D: prasad pack (main int4, flow/decoder int8) | 23.3 % | 15/24 | 90 MB | 568 MB | 158 ms | 0.45 |
| E: main int4 + flow/decoder fp32 | 27.1 % | 14/24 | 138 MB | 597 MB | 190 ms | 0.45 |

- **int4 breaks the main model**, independent of the flow/decoder precision (D and E are equally
  bad): it misses the end of sentences (clips up to 11.7 s instead of ~4.5 s), babbles ("Pelöööö…",
  "100,000,000,000,…"), drops words. Not usable.
- **int8 for the main model costs nothing measurable** (C ≈ B, within the noise of 24 clips) and is
  the fastest variant. This confirms the plan's precision mix.
- **The current weights are clearly better than April's** (A 6.2 % vs B 11.2 %). April garbles long
  number words — exactly what the verbalizer produces ("zweitausendvierundzwanzig" → "2000 wie rund
  20") — and the quoted "Eichhörnchen" sentence runs on in every April variant but not in A.
- **Decision for phase 4:** export the current 6-layer German weights ourselves (main int8,
  flow/decoder fp32, single-file ONNX), ~173 MB without encoder. No community export is good
  enough: they're all April weights.

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
