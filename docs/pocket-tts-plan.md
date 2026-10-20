# Plan: Pocket TTS as a second local TTS engine (Latency Lab only)

Status: **browser engine done (phase 4); Pocket replaces Sopro everywhere — see
["Decided 2026-10-20"](#decided-2026-10-20-pocket-replaces-sopro).** Written 2026-10-19. The original
goal below (Pocket next to Sopro, Latency Lab only) is kept for the record; phases 1–3 are
superseded by that section.

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
- ~~Decision for phase 4: export the current weights ourselves — no community export is good
  enough, they're all April weights.~~ Wrong: that only covered the three exports linked from model
  cards. See the market check below.

### Market check (2026-10-19): community exports of current weights, and other TTS models

**All 189 Pocket TTS derivatives on Hugging Face** (base model + name search) were fingerprinted by
tokenizer hash. Three ship German built from weights newer than April:

| Repo (pinned rev) | German weights | Precision | German files | Notes |
|---|---|---|---|---|
| [Alias-Alias/pocket-3-3-0-tts-onnx](https://huggingface.co/Alias-Alias/pocket-3-3-0-tts-onnx) | Sept 24 (`pocket-tts-without-voice-cloning@4e1e0a3`, = pocket-tts 3.3.0) | all int8 | 178 MB incl. 8 voice states (52 MB) | validated vs PyTorch **and in ONNX Runtime Web/WASM** (`validation.json`), checksums, provenance; own bundle schema 3; no encoder; exporter offered to Kyutai (issue #345), not public |
| [Lauro00000/k6-onnx-int8](https://huggingface.co/Lauro00000/k6-onnx-int8) (`b79d4c1`) | **Oct 1** (`pocket-tts@3e82814`, = current `main`) | all int8, fp16 KV cache | 126 MB (+39 MB fp32 encoder) | "internal build artifact, files may move"; KevinAHM-style schema 2; built with the public [lookbe/pocket-tts-onnx-export](https://github.com/lookbe/pocket-tts-onnx-export) (MIT, active) |
| [justdataplz/pocket-tts-web](https://huggingface.co/justdataplz/pocket-tts-web) | not documented | int8, 400-row fp16 cache | ~116 MB | in production on two websites; browser loop not published |

Kyutai retrained German again on **2026-10-01** (`pocket-tts@3e82814`); `pocket-tts` 3.3.0 on PyPI
still pins the Sept 24 weights, so the server test (A) didn't run the newest model either.

The browser loop of [KevinAHM/pocket-tts-web](https://huggingface.co/spaces/KevinAHM/pocket-tts-web)
(`inference-worker.js`, `onnx-streaming.js`, SentencePiece in JS, audio worklet) is **Apache-2.0**
(`CODE-LICENSE`) and runs the same schema-2 bundles as Lauro's export — a starting point for our
runtime instead of writing it from scratch.

**Benchmark, same setup as above** (alba cloned from Kyutai's public `alba-mackenna/casual.wav`):

| Variant | WER | Clips with errors | Model files | Peak RAM | First audio | RTF |
|---|---|---|---|---|---|---|
| F: MOSS-TTS-Nano-100M, official ONNX fp32 | 9.2 % | 11/24 | 762 MB | 3779 MB | – | **1.12** |
| G: Lauro, Oct weights, all int8 | 9.2 % | 10/24 | **126 MB** | 1236 MB | 163 ms | **0.22** |

- **G** is as good as A/C on 10 of 12 sentences (some cleaner — A clips "Bastille", "Mitochondrien").
  Its errors are systematic: it stops at a spaced en dash ("… zwölf Euro fünfzig – also etwa dreizehn
  Euro" loses the second half, both seeds) and garbles "Eichhörnchen". The dash is fixable in
  `normalize_for_speech` (a spaced en dash between words is an aside → comma; digit ranges are
  already the verbalizer's). Fastest and smallest variant; int8 for flow net and decoder shows no
  measurable harm here.
- **F (MOSS)** is slower than real time on this CPU, four to six times the files, adds words that
  aren't in the text ("wurde Deutschland wieder verehrt. Nein, nicht."). Not an alternative.

**Other TTS models checked** (German, small enough for a browser, permissive licence incl. weights):

| Model | Why not |
|---|---|
| MOSS-TTS-Nano-100M (Apache-2.0, 20 languages, cloning) | measured above: slower than real time, 762 MB fp32 (community int8 ~240 MB, untested) |
| Supertonic 3 (99M, 31 languages, browser runtime) | weights OpenRAIL-M (use restrictions, not on our allowlist); repo archived 2026-09; open weights have fixed voices only |
| NeuTTS-Nano-German | NeuTTS Open License (paid above $5M revenue), gated, GGUF only |
| Kokoro-82M German fine-tunes (kikiri, crane-local-ai) | single fixed voices, no cloning; German G2P usually via espeak-ng (GPL) |
| Qwen3-TTS 0.6B (Apache-2.0, German, cloning) | ~2 GB as int8 ONNX — server-only, not browser |
| Chatterbox Multilingual V3 (MIT, 0.5B, German, cloning) | 0.5B, ~0.8 GB even as q4 ONNX — server-only |
| Piper | engine now GPL-3.0 (piper1-gpl), espeak-ng G2P, no cloning |
| Kitten TTS | English only |
| Voxtral TTS, XTTS-v2, F5-TTS, Fish S2, IndexTTS-2.5, OmniVoice | non-commercial weights |

**Decision for phase 4 (revised):** no new model beats Pocket TTS for this use. Start from Lauro's
October export (pin `b79d4c1`, mirror the German files with checksums like
`scripts/fetch-stt-model.sh` does for Parakeet, since the repo says files may move) and
KevinAHM's Apache-2.0 browser loop. Being able to rebuild the export ourselves with the lookbe
exporter (MIT) covers the next weight update; a fully own export is no longer the first step.

**Done 2026-10-19:**
- `scripts/fetch-tts-model.sh` downloads Lauro's German files (`b79d4c1`, sha256-pinned, no
  encoder) to `models/pocket-tts-de/v1/` with gzip copies (93 MB per device), a `manifest.json`
  and the CC BY 4.0 `NOTICE.md`. Committing the files or baking them into the Docker image would
  be allowed by CC BY 4.0 (with that notice travelling along), but AGENTS.md forbids committing
  `models/`, the 77 MB file is close to GitHub's 100 MB limit, and every image and release would
  grow by 126 MB — so they're downloaded like the Parakeet files. The gated repo's access terms
  bind whoever accepted them, not downstream users (CC BY forbids added restrictions); the encoder,
  the part the gate protects, isn't redistributed.
- `normalize_for_speech` turns a spaced en dash between words into a comma. Re-tested: Lauro's
  model now speaks "… zwölf Euro fünfzig, also etwa dreizehn Euro." completely (2/2 seeds; before,
  it stopped at the dash every time).

- The maintainer mirrored the German and English bundles unchanged to
  [c-siegel/pocket-tts-en-de-onnx-int8](https://huggingface.co/c-siegel/pocket-tts-en-de-onnx-int8)
  (`5378417`); all German files verified against the pinned checksums. The script tries the
  mirror first, Lauro's repo second (fallback tested).

**Done 2026-10-19 — preset voice "alba":**
- Kyutai's gated and ungated October German weights differ only in the voice encoder (42 tensors,
  zeroed in the ungated release); flow LM, flow net and Mimi decoder are bit-identical. So
  Kyutai's official, ungated voice states fit Lauro's export — and the files we ship are
  bit-identical to the ungated release, which settles the gate question for them.
- `fetch-tts-model.sh` downloads `languages/german/embeddings/alba.safetensors` unchanged from
  `kyutai/pocket-tts-without-voice-cloning@ab24877` (sha256-pinned, 6.2 MB) into `voices/`, and
  credits Alba MacKenna (CC BY 4.0) in `NOTICE.md`. Transfer size per device now 99 MB.
- Conversion to the export's state layout (to be done in the browser, see `manifest.json`):
  per layer split Kyutai's `cache` float32 `[2,1,T,16,64]` into `cache_k` / `cache_v` float16
  `[1,capacity,16,64]` (rows past T zero) and set `step = offset = T` (T = 127 for alba).
- Verified with the benchmark setup (Python reference conversion, same 12 sentences after the
  verbalizer and the dash fix, 2 seeds):

  | Variant | WER | Clips with errors | Model files | Peak RAM | First audio |
  |---|---|---|---|---|---|
  | H2: Lauro export + official alba | **5.0 %** | 8/24 | 126 MB + 6 MB voice | 482 MB | 92–156 ms |
  | (A: server, PyTorch, Sept weights, alba) | 6.2 % | 9/24 | – | 1039 MB | 301 ms |

  The best variant so far. Remaining errors: "Eichhörnchen", "Bitte lies" run together, one
  "wiedervereinigt" split by Whisper. RTF ~0.25 when the machine is idle.

**Done 2026-10-20 — phase 4, browser engine in the Latency Lab:**
- `frontend/src/lib/pocketTtsCore.ts`: our own TypeScript port of pocket-tts 3.3 (text
  preparation incl. Kyutai's German `replace_characters`, sentence chunking, flow-LM loop with
  `MIN_FRAMES_BEFORE_EOS = 6`, streaming Mimi decode in 3- then 12-frame batches, voice
  conversion). ONNX Runtime and file loading are injected, so it also runs under Node. KevinAHM's
  web demo was only a reference — not copied: its bundled `sentencepiece.js` has no clear licence
  and the Space's `ONNX-LICENSE` says CC BY-NC.
- Tokenizer: `@huggingface/tokenizers` 0.2.0 (Apache-2.0) on Kyutai's `tokenizer.json` (added to
  the download script). It has no byte fallback for Unigram models; the core adds it, so
  characters outside the vocabulary become byte tokens as in Python (35/35 test texts identical,
  emoji, CJK, German quotes included).
- `workers/pocketWorker.ts` + `lib/pocketTts.ts` (same protocol as Sopro), behind a small
  `BrowserTts` interface that the turn runner now uses for any in-browser engine. Download helpers
  shared with Parakeet in `lib/modelFiles.ts` (separate cache `lola-tts-models`).
- Latency Lab: speech output option "Pocket TTS in this browser (German)", engine panel with
  load button, status, voice credit, German-only notice. Credits page: Pocket TTS (Kyutai, Lauro's
  conversion, the mirror, Alba MacKenna) and Tokenizers.js.
- Pitfall found: onnxruntime-common turns float16 data into `Float16Array` when the runtime has
  one (Node ≥ 24, recent browsers). onnxruntime-web handles that; onnxruntime-node's native binding
  doesn't (reads it as empty) — the Node test hides `Float16Array`.

Verified (same 12 sentences after the verbalizer and dash fix, alba, 2 runs each):

| Where | WER | Clips with errors | First audio | RTF |
|---|---|---|---|---|
| Core under onnxruntime-node, 1 thread | 2.9 % | 5/24 | 145 ms | 0.36 |
| **Real worker in headless Chromium, WASM, 1 thread** | **2.1 %** | 4/24 | 482 ms | **0.86** |

Remaining "errors" are mostly Whisper writing "wieder vereinigt" as two words. Chunking is
identical to Python, the voice state within one fp16 ulp. In the browser it runs at 0.86 RTF on
one laptop core — faster than real time, but with little headroom: LOLA isn't cross-origin
isolated, so ONNX Runtime Web stays single-threaded (COOP/COEP would break the Tally survey
iframe, see `parakeetWorker.ts`).

**Next:** measure on the target iPads in the Latency Lab (time to first audio, gaps during
playback, and whether Safari keeps the tab alive with Parakeet + Pocket + avatar loaded).

## Decided 2026-10-20: Pocket replaces Sopro

The maintainer compared both engines in the Latency Lab: Pocket TTS sounds clearly better than
Sopro and the int8 model runs faster than real time in the browser. Sopro goes away:

- **Step 1 — done 2026-10-20:** Sopro removed as an in-browser engine (worker, wrapper,
  `@soprotts/onnx-web`, Latency Lab panel and strings). A stored `"browser"` setting maps to
  `"browser-pocket"`.
- **Step 2 — this section:** Pocket becomes *the* engine of the `local-tts/` sidecar, the
  production path for every project without a cloud TTS key. Sopro is removed from the sidecar,
  its image and the docs in the same change. No engine switch, no side-by-side operation.

### What Kyutai ships (checked 2026-10-20)

`kyutai/pocket-tts-without-voice-cloning@a3055dc` (CC BY 4.0, ungated) and `pocket-tts` 3.3.0 on
PyPI:

| Language | Model (6 layers) | Native preset voice (Kyutai's default) | Its source and licence |
|---|---|---|---|
| English | `english` (+ dated, 24-layer and small 2M/7M/18M variants) | — (fallback `alba`) | — |
| German | `german`, `german_24l` | `juergen` | `de-DE-juergen.mp3`, **source undocumented** |
| French | `french`, `french_24l` | `estelle` | Kyutai's own recording, CC0 |
| Italian | `italian`, `italian_24l` | `giovanni` | Mozilla Common Voice, CC0 |
| Spanish | `spanish`, `spanish_24l` | `lola` | Mozilla Common Voice, CC0 |
| Portuguese | `portuguese`, `portuguese_24l` | `rafael` | a YouTube clip (`g-Vi8PgmSY0`), **source undocumented** |
| Dutch | `dutch`, `dutch_24l` | `daan` | CML-TTS, CC BY 4.0 |

- Dutch is new since the plan was written. Each language is its own ~219 MB model; voices are
  precomputed states, and **every one of the 27 presets exists for all seven languages** (the
  speaker keeps their voice, the model supplies the language).
- Voice licences (from `pocket_tts/utils/utils.py` → `kyutai/tts-voices`): CC0 — marius, javert,
  bill_boerst, peter_yearsley, stuart_bell, caro_davy, estelle, giovanni, lola; CC BY 4.0 — alba,
  anna, vera, fantine, charles, paul, eponine, azelma, george, mary, jane, michael, eve, daan;
  **non-commercial, never offered** — cosette (Expresso), jean (EARS); **undocumented, not offered
  until Kyutai answers** — juergen, rafael.
- So German and Portuguese have no native-speaker preset we may use yet. German with `alba` is
  what we measured (5.0 % WER, H2 above) and what the browser uses; keep it as the German default
  so server and browser sound alike. Ask Kyutai about `juergen` and `rafael` (GitHub issue).
- Kyutai retrained German on 2026-10-01; PyPI 3.3.0 still loads the 2026-09-24 weights. The
  browser runs the October weights.

### Decision: teachers' own voices (decided 2026-10-20: A)

Sopro clones every voice clip (Dashboard → Voices; the consent checkbox already exists). Pocket only
clones with the **gated** weights (`kyutai/pocket-tts`, Hugging Face token whose account accepted
Kyutai's terms); the ungated release has the voice encoder zeroed.

- **A. Presets plus optional cloning (recommended).** `TTS_HF_TOKEN` set → clips are cloned as
  today. Not set → a project with a clip speaks with the language's default preset, the dashboard
  says so (Voices page and configurator), nothing fails. Deployments keep working either way.
- **B. Presets only.** No token handling, no encoder; the Voices page is hidden when the only TTS is
  local. Simpler, but removes a feature teachers use today.

Both variants add a **preset voice picker** for local TTS in the configurator (today the voice
field is ignored for local TTS), limited to the allowlist above.

### Done 2026-10-20 — variant A implemented

The maintainer chose **A** and pointed at the CC0 voices in `kyutai/tts-voices` (`voice-zero/`,
`voice-donations/`). Commits on `pocket-tts-implementation`: sidecar, backend, frontend.

**Phase 0 results** (Ryzen 5 5625U, one torch thread, ungated October weights, preset alba):

- Weights: the ungated repo at `a3055dc` carries the October retrains (German, French, Italian,
  Spanish, Portuguese 2026-10-01, English 2026-10-11, Dutch 2026-09-27) with unchanged tokenizers;
  `pocket-tts` 3.3.0 pins `4e1e0a3` (September). The sidecar rewrites pocket-tts' configs to the
  pinned revisions (ungated `a3055dc`, gated `a72dba0` — its content couldn't be checked without a
  token). Voice states are taken from the same revision, since a state only fits its weights.
- Speed: first audio ~80 ms, RTF ~0.25, for German and English alike. Loading ~20 s per language.
- Memory: ~670 MB (German) + ~490 MB (English) on top of the process, ~1.6 GB peak RSS together.
- Two threads generating on one model at once: both clips clean (Whisper round trip), 1.45 s wall
  time for both — one model per language is enough, no pool.
- **English needs a verbalizer too.** Plain sentences and abbreviations ("Dr.", "St.", "e.g.") are
  fine, but numbers break: "fell on November 9, 1989" → "…November 9." (year dropped), "$12.50,
  that's about 13 dollars" → "12 dollars", "pages 47 to 52 by Monday" → "pages 47-" (cut off),
  "3² is 9" cut off. `verbalizer.py` only has German rules.

**What was built**

- Sidecar (`local-tts/`): Pocket replaces Sopro; `TTS_LANGUAGES` (default `de,en`) load at startup
  in the background; `TTS_HF_TOKEN` enables cloning; `GET /voices` lists languages, cloning and
  voices; `POST /synthesize` takes an optional `voice`. Presets: 23 allowlisted (the four
  voice-zero voices are among them, so they work without a token). Donated voices: all 228 from
  `voice-donations` at `323332d`, cloned from the `_enhanced.wav` on first use, `TTS_DONATED_VOICES`
  narrows the list. Without cloning, clip and donation requests fall back to a preset instead of
  failing. Cloned states are cached as files. First tests (24), CI job with the licence gate
  (`pocket-tts` listed as reviewed: MIT file, no metadata).
- Backend: forwards `tts_voice` to the sidecar; `/providers/local-tts-status` adds `reachable`,
  `cloning`, `languages`, `voices` (cached 60 s).
- Frontend: one local voice picker (presets, donated voices, own clips) with notes for cloning off,
  sidecar down and missing language; Voices page and pronunciation test box respect cloning;
  Credits list the voice sources.

**Still open**

- Real cloning with a token (teacher clip and a donated voice) — not testable here.
- The Docker image wasn't built here (no Docker); release CI builds it.
- English verbalizer (numbers, years, money, ranges, ordinals, superscripts) before English
  projects rely on local TTS.
- Curate the donated voices by ear: the names are the donors' own handles, some recordings may be
  noisy or in other languages. `TTS_DONATED_VOICES` takes the curated list.
- Ask Kyutai about the sources of `juergen` and `rafael` (native German and Portuguese presets).
- Browser engine: other languages than German need their ONNX bundles.

### Phase 0: checks (half a day) — done, results above

1. Does `TTSModel.load_model` accept a weights revision, so the server can pin the October German
   weights (`ab24877` or newer) that the browser uses? Otherwise install `pocket-tts` from a pinned
   git commit until 3.4 ships.
2. English: does Pocket read digits, dates and abbreviations in English, or does English need a
   verbalizer like German? Same 12-sentence WER round trip as above, in English.
3. Memory and speed of the sidecar with German + English loaded, 2 concurrent requests, on the
   reference laptop (RTF, first audio, peak RAM). Decides the default for `TTS_LANGUAGES`.
4. With a token: clone one teacher clip, compare to `alba` by ear and by WER.

### Phase 1: sidecar (`local-tts/`)

- `app/synthesis.py` rewritten for Pocket: one `TTSModel` per language, loaded lazily on first use
  and cached; `torch.set_num_threads(settings.torch_threads)` set explicitly (importing
  `pocket_tts` sets it to 1 process-wide).
- Language codes `en de fr it es pt nl` → Pocket's names. A language outside
  `settings.languages` → `UnsupportedLanguageError` (400), as today.
- Voices: the request's `voice` (a preset name, checked against the allowlist) or
  `voice_sha256` (a stored clip, variant A only) or the language's default preset. Precomputed
  states are cached per (language, voice); cloned clips per (language, sha256).
- API unchanged except an optional `"voice"` field in `POST /synthesize`; `GET /health` additionally
  returns `{"languages": [...], "voices": [...], "cloning": bool}` so the backend can report it.
- Output: WAV at Pocket's 24 kHz with the existing 0.4 s silent tail (check whether Pocket still
  needs it — its `frames_after_eos` already lets speech ring out).
- Settings: drop `TTS_MODEL_REPO`, `TTS_QUANTIZATION`; `TTS_VOICES_DIR` becomes optional (an
  operator's own default clip per language still works in variant A). New: `TTS_LANGUAGES`
  (default `de,en`), `TTS_HF_TOKEN` (variant A), `TTS_TORCH_THREADS`.
- `pyproject.toml`: replace `sopro` by `pocket-tts>=3.3,<4`; add `pytest` dev extra.
- **New:** `local-tts/tests/` (the sidecar has none today), with the model faked at
  `TTSModel`: language mapping, allowlist (cosette/jean/juergen/rafael refused), clip storage and
  hash checks, 404 `VOICE_NOT_STORED`, cloning disabled without a token.
- `docker/local-tts.Dockerfile`: same CPU-torch step, then `pip install .`. Compose: rename the
  `sopro-voices` mount, add the new variables.

### Phase 2: backend

- `LocalTTSClient` sends the project's preset voice (`tts_voice`, reused for local TTS) as
  `voice`; a clip still goes as `voice_sha256`.
- Variant A without a token: don't send the clip (read `cloning` from the sidecar's `/health`,
  cached briefly), so the request uses the preset instead of failing.
- `/local-tts-status` grows `languages`, `voices`, `cloning`.
- Tests in `backend/tests/` with the sidecar faked at httpx; OpenAPI snapshot update. No model
  change, so no migration: `Project.tts_voice` and `tts_voice_clip_id` already exist.

### Phase 3: frontend

- Configurator: preset voice picker when the project's TTS is local; clip picker only when
  `cloning` is true, otherwise a hint that clips need cloning to be enabled on the server.
- Voices page: same hint when cloning is off (variant A) or hidden (variant B).
- Latency Lab: "Local TTS on the server (Sopro)" → "(Pocket TTS)".
- Credits page: Pocket TTS on the server (weights CC BY 4.0) and the credited voices; drop Sopro.
- All strings in `de.json` and `en.json`.

### Phase 4: docs and licences

- Root README (the local-TTS section and the licence table), `local-tts/README.md`,
  `local-tts/voices/README.md`, `docker/README.md`, `.env.example` (new/removed `TTS_*` variables
  with their deploy path), `docs/latency-test.md`.
- `pocket-tts` has an MIT licence file but no licence metadata: add a reviewed entry if the
  sidecar gets the licence gate (worth adding now that it's the production TTS).

### More languages for LOLA (separate change, after step 2)

LOLA projects speak only German and English (`SPOKEN_LANGUAGE_VALUES`, several backend
`Literal["de", "en"]`). The sidecar will be able to speak French, Italian, Spanish, Portuguese and
Dutch as soon as it lists them in `TTS_LANGUAGES`, but offering them to teachers touches more than
TTS:

- speech input: Whisper handles all of them; check on-device Parakeet's languages;
- the verbalizer is German only — every new language needs its own number/abbreviation rules
  (Pocket can't read digits, at least in German);
- the browser engine has German (and English in our mirror) only;
- UI language lists, pronunciation dictionary and evaluation (`Literal["de", "en"]`).

Start with one language (French: native CC0 preset `estelle`, own Kyutai recording) as a pilot.

## Phase 1: sidecar (`local-tts/`) — superseded, see "Decided 2026-10-20"

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

## Phase 2: backend (latency route only) — superseded

- `LatencyTestMessageIn.tts_mode` gains `"local-pocket"`.
- `ChatContext.local_tts_engine: str = "sopro"`, passed through `synthesize_speech` to
  `LocalTTSClient(engine=...)`. Only `features/chat/latency_router.py` ever sets `"pocket"`.
- The local-TTS status endpoint (`useLocalTtsStatus`) reports Pocket availability from `/health`.
- Tests (`tests/test_routes_latency_test.py`, sidecar faked at httpx): `local-pocket` sends
  `engine: "pocket"`; public/preview chat send no engine; 400 when Pocket isn't enabled. Update the
  OpenAPI snapshot.

## Phase 3: frontend, server option — superseded

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
- A consent checkbox for voice uploads — required before Pocket leaves the Latency Lab, where only
  the logged-in teacher uses their own clips.

## Open question

- Which iPad models to test on — decides whether the 24-layer German variant is worth offering in
  the browser.
