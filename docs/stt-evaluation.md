# STT evaluation: Moonshine Voice vs. Parakeet Redux

Status: **done; decided 2026-11-09: Moonshine is not adopted for now, Parakeet stays on the server
and the device** (revisit option B later, see [stt-plan.md](stt-plan.md)). Measured 2026-10-22 on branch `stt-rework` (phase 1 of
[stt-plan.md](stt-plan.md)). Reproduce with [scripts/stt-benchmark](../scripts/stt-benchmark/README.md).

## Summary

- **Accuracy, German:** Parakeet is best. Moonshine Small is close (+1.0 / +0.9 / +1.5 WER points
  on long, medium and short clips). Moonshine Tiny is clearly worse on sentences (+5 to +8 points)
  but about equal on short clips.
- **Accuracy, English:** Parakeet is clearly better than both Moonshine models (about +7 points
  for Small, more for Tiny).
- **Wrong language:** Parakeet answered **4 of 100 short German clips in English** ("Hilf mir mal."
  → "Here is me a moment."). Moonshine never does, since each model knows one language only.
- **Waiting time after the student stops talking:** Moonshine streams, so the final text is there
  after ~5–40 ms (median). Parakeet on the server needs ~1.1–1.9 s (median), up to ~5 s (p90).
- **Size:** German Tiny is 27 MB to download (gzip), German Small 108 MB, Parakeet 175 MB.
  Moonshine needs a quarter or less of Parakeet's RAM.
- **Browser without WebGPU:** works. On one CPU core (WASM), Moonshine streams at 0.26× (Tiny)
  and 0.61× (Small) real time, so even weak devices without WebGPU could transcribe locally.
  Today they fall back to the server.
- **But the published browser build can't be used in LOLA yet:**
  1. It needs cross-origin isolation (COOP/COEP), which would break the Tally survey iframe.
  2. Version 0.1.5 has no German models.

  Both are upstream limitations; see [Browser](#browser).
- **Licence:** all streaming models are MIT, German included. The library's "non-commercial
  Community License" warning for non-English models is outdated (see [Licence](#licence)).

**Recommendation:**
- **Server:** keep Parakeet. Phase 2 (remove faster-whisper) goes ahead unchanged.
- **Device:** Moonshine is worth adopting as the **on-device engine for devices without WebGPU**,
  German first, once the two browser blockers are solved. That means either waiting for upstream
  (German in the WASM build, a single-threaded package), or building the WASM ourselves from the
  MIT sources.
- **English:** stays on Parakeet.
- **Small vs. Tiny on the device** is a trade between 108 MB and Small's accuracy, or 27 MB and
  Tiny's. It needs a test on a real iPad.

The decision is yours; the options are in [Options](#options).

## Setup

| | |
|---|---|
| Machine | AMD Ryzen 5 5625U laptop (6 cores / 12 threads), 15 GB RAM, Windows 11 |
| Parakeet | Parakeet Redux int8 ONNX as shipped (`scripts/fetch-stt-model.sh`), run by the backend's own `parakeet_local.py`. Server path, batch: the whole recording at once. |
| Moonshine | `moonshine-voice` 0.1.5 (Python) and `@moonshine-ai/moonshine-wasm` 0.1.5 (browser, Edge 155). The int8 `.ort` builds the runtime downloads: `tiny/small-streaming-de` release 26_08_24, `tiny/small-streaming-en` release 26_08_21 (browser: 26_07_30, see below). |
| Audio | FLEURS test (CC BY 4.0), 200 per language, 10–13 s each. Common Voice 19 test (CC0), 200 per language, half under 3 s. Browser runs: 40 + 40 per language. |
| Streaming | Audio in 100 ms chunks, live-text update every 0.5 s (as `STEP_SECONDS` in the app), then stop and wait for the final text. |
| Scoring | WER after lowercasing and removing punctuation. References with digits are left out (engines write numbers differently). "Wrong language" uses a stopword heuristic, checked by hand. |

## Results

### German (4 cores)

WER in %, lower is better. Moonshine: streaming transcript.

| Engine | FLEURS | CV ≥ 3 s | CV < 3 s | Wrong language (short clips) | Wait after speech, p50 / p90 | RAM | Download (gzip) |
|---|---|---|---|---|---|---|---|
| Parakeet | **6.8** | **11.1** | **17.4** | 4 % | 1143 / 2842 ms | 1345 MB | 175 MB |
| Moonshine Small | 7.8 | 12.0 | 18.9 | 0 % | 33 / 621 ms | 317 MB | 108 MB |
| Moonshine Tiny | 12.2 | 19.0 | 18.2 | 0 % | **31 / 318 ms** | **171 MB** | **27 MB** |

Parakeet's wrong-language cases (all four):

| Said | Parakeet | Moonshine Tiny | Moonshine Small |
|---|---|---|---|
| Hilf mir mal. | Here is me a moment. | Hilf mir mal. | Hilf mir mal. |
| Event erstellen. | I sent erstellt. | Die Welt erstellt. | Event erstellen. |
| Lasset uns beten. | Locitons be | (nothing) | (nothing) |
| Sie lebt in Saint-Dizier. | She lived in a santier. | Sie lebt in einem Saint-Disier. | Sie lebt in Asserne des Yets. |

Short student answers are where this matters most. Parakeet takes no language hint, and with
little audio it sometimes picks English.

### English (4 cores)

| Engine | FLEURS | CV ≥ 3 s | CV < 3 s | Wait after speech, p50 / p90 |
|---|---|---|---|---|
| Parakeet | **5.8** | **14.6** | **18.4** | 1280 / 2169 ms |
| Moonshine Small | 12.6 | 22.0 | 25.5 | 31 / 948 ms |
| Moonshine Tiny | 19.9 | 37.0 | 34.7 | 34 / 570 ms |

### Speed

RTF = compute time ÷ audio length (below 1 = faster than real time). For Moonshine it is the
streaming cost, including the live-text updates.

| Engine | Where | Cores / threads | RTF | Wait after speech, p50 / p90 |
|---|---|---|---|---|
| Parakeet de | server (Python) | 4 | 0.17 | 1143 / 2842 ms |
| Parakeet de | server (Python) | 1 | 0.27 | 1741 / 4947 ms |
| Parakeet en | server (Python) | 1 | 0.30 | 1891 / 3242 ms |
| Moonshine Tiny en | browser (WASM) | 4 | 0.26 | 4 / 161 ms |
| Moonshine Tiny en | browser (WASM) | **1** | **0.26** | 4 / 174 ms |
| Moonshine Small en | browser (WASM) | 4 | 0.57 | 6 / 665 ms |
| Moonshine Small en | browser (WASM) | **1** | **0.61** | 7 / 1028 ms |
| Moonshine Tiny de | native (Python) | 4 | 0.48* | 31 / 318 ms |
| Moonshine Small de | native (Python) | 4 | 0.98* | 33 / 621 ms |

\* Pessimistic. Moonshine's Python runtime has no thread setting and starts ~44 threads even on 4
pinned cores, so it fights itself. The Python runtime also asks for an update every 0.5 s without
throttling. The browser runtime stretches the update interval when a pass takes longer, which is
what the app would use. **The browser rows are the realistic Moonshine numbers.** A native 1-core
Moonshine run was dropped as meaningless for the same reason.

The browser runs use the English 26_07_30 models (see [Browser](#browser)) as a stand-in for
German speed. They have the same architecture and size (Tiny 34M, Small 123M parameters). Their
accuracy is worse than the newer English release (CV ≥ 3 s: 48.1 vs. 37.0 for Tiny). That's a
model-version effect, not a browser effect: the same C++ core runs in both.

Parakeet's on-device speed (WebGPU) isn't measured here; the Latency Lab covers it (see
[stt-device-test.md](stt-device-test.md)). There, Parakeet needs WebGPU, iPadOS 26+, and ~350 MB
of GPU memory next to the avatar ([on-device-speech.md](on-device-speech.md)).

### Batch vs. streaming

Moonshine is consistently *more* accurate in streaming mode than when given the whole recording,
especially in English (Small: 15.8 vs. 22.2 % WER overall). If we adopt it, we use the streaming
API on the server as well.

## Browser

What works:
- `@moonshine-ai/moonshine-wasm` 0.1.5 (MIT, no dependencies; 13 MB WASM, 6 MB gzip) runs the
  streaming models on the CPU. No WebGPU is needed.
- Speed on one core is fine for Tiny and acceptable for Small (table above).
- Model files can be self-hosted (`Transcriber.loadFromUrls` / `baseUrl`), like our Parakeet files.
- It also offers key terms and context biasing (`setKeyterms` / `setContext`), which could take
  subject vocabulary from a project's system prompt. Not evaluated here.

What blocks adoption today:
1. **Cross-origin isolation.** The npm build is compiled with threads and needs
   `SharedArrayBuffer`, i.e. `Cross-Origin-Opener-Policy` / `Cross-Origin-Embedder-Policy` headers
   on the page. LOLA deliberately doesn't send them: they would break the Tally survey iframe (see
   `frontend/src/workers/parakeetWorker.ts`). Upstream documents a single-threaded build
   (`-DMOONSHINE_WASM_SINGLE_THREAD=ON`), but only from source (Emscripten + an ONNX Runtime build).
   Since Tiny is as fast on 1 thread as on 4, a single-threaded build costs little speed.
2. **No German in the WASM build.** Its built-in catalog lists only the English streaming models
   (26_07_30). Its in-memory loader rejects the newer model format all German models use (the
   frontend split into `frontend.model.ort` + `frontend.weights.ort`). The Python package of the
   same version handles both, so the WASM build simply lags behind.

## Licence

- **Code and models: MIT.** The repository's [LICENSE](https://github.com/moonshine-ai/moonshine/blob/main/LICENSE)
  says every streaming model is MIT. The only non-MIT models are the legacy *non-streaming*
  models for languages other than English (Community License, non-commercial). German has none of
  those, and we wouldn't use them.
- The Python library prints "Using a model released under the non-commercial Moonshine Community
  License" for **every** non-English download. That's a blanket check
  (`if wanted_language != "en"` in `download.py`), not per-model metadata. It's misleading but
  harmless. The model cards (`moonshine-ai/moonshine-streaming-tiny-de`, `-small-de`) also say MIT.
- Attribution on the Credits page is good practice; MIT requires only keeping the notice.
- Evaluation data: FLEURS CC BY 4.0, Common Voice CC0. Used locally only, nothing redistributed.

## Caveats

- **Read speech by adults** (FLEURS, Common Voice). Students speaking freely in a noisy classroom
  will score worse with every engine, and the ranking could change. A small recording session with
  real students would be the next step before adopting anything.
- **Moonshine's German training data includes FLEURS** (the training split; the model card lists
  it). Its FLEURS numbers may flatter it slightly; Common Voice is the fairer comparison.
- **int8 models.** We measured the builds we would ship. The German Tiny model card puts the
  quantization cost at ~0.9 WER points versus the float checkpoint.
- **One machine, one browser.** No iPad or Chromebook yet. Edge on a Ryzen laptop is optimistic
  for school iPads.
- **Sample sizes.** 200 + 200 utterances per language for accuracy, 100 short clips. Differences
  under ~1 point are within noise.

## Options

| | Option | Gains | Costs |
|---|---|---|---|
| A | Keep Parakeet everywhere (status quo) | best accuracy; nothing to build | devices without WebGPU keep using the server; ~4 % of short German answers come back in English |
| B | **Moonshine German on devices without WebGPU**, Parakeet everywhere else (recommended, once unblocked) | local STT on old iPads/Chromebooks, 27–108 MB download, instant final text, no wrong-language output | two engines in the frontend; needs a single-threaded WASM build with German (upstream or our own) |
| C | Moonshine Small for German everywhere on the device, replacing Parakeet | one small model (108 MB instead of 175 MB), no WebGPU needed at all | ~1–1.5 points worse WER than Parakeet; same browser blockers |
| D | Moonshine on the server for German | ~¼ of the RAM, no wrong-language output | slightly worse WER; the server keeps per-segment HTTP, so streaming gains nothing there |

For B or C, the next steps would be:
1. Open upstream issues: German in the WASM catalog / new model format, and a published
   single-threaded build.
2. Meanwhile, try building the single-threaded WASM from source in a throwaway branch.
3. Test Tiny and Small on a real iPad (iPadOS < 26, without WebGPU) and a Chromebook.
4. Record ~50 short answers from students (with consent) to check the short-clip numbers on real
   classroom speech.
