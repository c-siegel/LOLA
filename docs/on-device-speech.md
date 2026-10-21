# On-device speech

By default, voice input is transcribed in the visitor's browser, from model files the app hosts itself. Speech output can run on the device too (Pocket TTS, currently used by the latency test page).

## Model files

| Model | Size | Fetch (Deploy A) | Deploy B |
|---|---|---|---|
| Speech recognition (Parakeet Redux) | ~380 MB | `scripts/fetch-stt-model.sh` → `./models`, served at `/models/` | `stt-model` service, automatic |
| Speech output (Pocket TTS, German, int8 ONNX, preset voice "alba") | ~132 MB | `scripts/fetch-tts-model.sh` → `./models/pocket-tts-de` | `tts-model` service, `browser-tts` profile |

The Pocket TTS files are CC BY 4.0; the attribution is in the `NOTICE.md` written next to them.

Without the STT files, voice input still works: the browser fails to load the model and falls back to transcribing on the server (no live text while speaking, a bit more delay).

## When on-device recognition is used

All of the following must hold, otherwise the chat shows a short notice and uses the server.

**Server**
- The model files are in place. Check: `/models/parakeet-redux/v1/manifest.json` loads.
- `BROWSER_STT_ENABLED` is not `false`, and the project has voice input enabled.
- The site is served over **HTTPS** (or `localhost`). Browsers disable WebGPU and the microphone on plain HTTP.

**Visitor's device**
- **A browser with WebGPU that finds a usable GPU.** The API being present is not enough; the app asks for a GPU adapter.
  - iPad / iPhone: **iOS / iPadOS 26 or newer** (first Safari with WebGPU on by default). Older iPads always use the server. Lockdown Mode also turns WebGPU off.
  - Windows, macOS, ChromeOS: current Chrome or Edge; Safari 26 on macOS.
  - Android: current Chrome on Android 12 or newer; some GPUs are still left out.
  - Firefox: only on Windows so far.
- **Enough memory.** ~350 MB of GPU memory next to the 3D avatar. iPadOS reloads tabs that use too much, which hits older iPads first.
- **~400 MB of free storage** for the browser cache. Private browsing keeps nothing, so the model downloads on every visit.
- **Microphone permission** for the site.

## Network load

A device's first visit downloads ~175 MB. 25 iPads opening the chat at once pull ~4.4 GB through the school network, so let the devices open the chat link once before the lesson; afterwards the model comes from the browser cache. Safari can delete a site's data after 7 days without a visit, so expect occasional re-downloads on iPads used weekly. Behind Cloudflare, let it cache the model: [docker/README.md](../docker/README.md#deploying).

To check a specific device, follow [stt-device-test.md](stt-device-test.md).
