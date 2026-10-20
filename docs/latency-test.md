# Latency test: runbook

The dashboard's **Latency test** page (`/dashboard/latency`) measures how long students wait for
the avatar on a real device, and where that time goes. It sends simulated student messages to one
of your own projects — typed, recorded, or replayed from audio clips — and times every step:

```
student stops speaking ─► transcript ─► LLM ─► first sentence ─► TTS ─► avatar starts speaking
        t0                   STT          first token   ready        synth      "First audio"
```

**First audio (end to end)** is the number students feel: from the moment they finished (recording
stopped, clip ended, text sent) until the avatar starts to answer. The page's "What each number
means" section explains every other column.

Test messages run with your own API keys, like the configurator's preview chat, but are never
saved: they don't show up in analytics or conversation exports. Results stay in the browser tab
until you export them (CSV or JSON, with the device in every row).

## What you can switch

| Setting | Options |
|---|---|
| Speech recognition | On this device (Parakeet via WebGPU — load it first under "Device"), or on the server: as the project is set up, Whisper, or Parakeet |
| Language model | As the project is set up, or any of your other LLM keys |
| Streaming | On: the avatar starts after the first sentence. Off: after the whole reply |
| Speech output | As the project is set up, the local TTS server (Sopro), Pocket TTS in this browser, or none |
| Avatar | The 3D avatar (also measures its frame rate and lost WebGL contexts), or audio only |

**Pocket TTS in the browser** loads Kyutai's German Pocket TTS model (int8, ~126 MB) from this
server — fetch it first with `scripts/fetch-tts-model.sh` — and speaks with the preset voice
"alba". German only, no voice cloning; it runs on the CPU (WASM, one thread). This is an experiment
to see whether in-browser speech output is fast enough on a device — the public chat doesn't use
it.

## Comparable runs

1. Record a few short student questions once (e.g. 3–8 s, German and English) and add them under
   "Single message → Add audio clips" on every device. Clips play at real-time speed, so on-device
   speech recognition hears them exactly as if a student were speaking.
2. Write a script that uses them, e.g.

   ```
   # one turn per line; "clip: <file name>" speaks an added clip
   clip: frage1.wav
   Kannst du das noch einmal einfacher erklären?
   clip: frage2.wav
   ```

3. Set repeats (3–5 is enough to see a median) and run it. Every repeat starts a new conversation.
4. Change one setting at a time and run the script again. The summary groups results by
   configuration and shows median / 90th percentile for each.
5. Export CSV on every device and merge the files to compare devices.

## Reading the results

| Column | Look at it when … |
|---|---|
| STT until final text | the transcript takes long after speaking ends (device: finalizing; server: upload + transcription) |
| LLM first token | the model itself is slow to start — try another model or provider |
| TTS first sentence | speech synthesis is slow — compare server TTS, local TTS and Pocket TTS in the browser |
| Request → first chunk | network + LLM + server TTS together, as this device sees it |
| Pauses in the speech | the next sentence wasn't synthesized before the previous one finished — streaming can't keep up |
| Avatar FPS / context losses | the GPU is overloaded (e.g. on-device speech recognition plus the avatar on an iPad) |

Headless browsers usually can't render the avatar or play audio, so measure on the real devices.
