# On-device speech recognition: device test runbook

Voice input in the public chat is transcribed on the student's own device by default (Parakeet
Redux on WebGPU, see `frontend/src/lib/parakeetStt.ts`). Its speed depends entirely on the
device's GPU, so it has to be checked on the devices classes actually use. This runbook takes
about 10 minutes per device.

## Before you start

- The model files are in place: `scripts/fetch-stt-model.sh` (dev) or the Docker steps in
  [docker/README.md](../docker/README.md#deploying).
- The device opens the app over **HTTPS or `localhost`** — WebGPU and the microphone are blocked
  on plain HTTP. For a phone/tablet against a dev machine, run `npm run dev -- --host` and accept
  the self-signed certificate once.
- A teacher account to log in with on the device: the check is part of the dashboard's
  **Latency test** page (`/dashboard/latency`), section "Device".
- Two test clips, the same ones on every device: one German, one English, each 15–30 s of natural
  speech with a few pauses. Write down their exact text, so you can compare.

## 1. Load time (first visit)

1. Clear site data for the app (browser settings), log in, and open the latency test.
2. Under "Device", press **Load** next to "Speech recognition on this device" and note
   **"ready after … s"**. On a first visit this is mostly download time (~175 MB).
3. If the status ends as `unsupported` or `error`, note the error message and skip to step 5 —
   the device will use server transcription.

## 2. Load time (repeat visit)

Reload the page and press **Load** again. The model now comes from the browser's cache: note the
ready time again. This is what a student waits on every later visit.

## 3. Known clips

In **"Measure speech recognition only"** (below the model's Load button), for each clip:
**"Play an audio file through the recognizer"**, pick the file, let it play to the end, then press
**"Copy results"** and paste the text into your notes. Compare **"Final text"**
with the clip's real text.

## 4. Live speech

Press **"Record from microphone"**, read a few sentences in a normal classroom voice — ideally
with background noise like in a real lesson — and stop. Copy the results.

## 5. Public chat

Open a published project's chat (`/c/<slug>`):

- Supported device: the loading screen appears, then the chat. Words appear in the message box
  while you speak, and the message is sent right after you stop.
- Unsupported device (or with `BROWSER_STT_ENABLED=false`): a notice in the chat says voice
  messages are processed on the server, and voice input still works.

## What to look for

| Number (from "Copy results") | Good | Problem |
|---|---|---|
| `firstPartialMs` | under ~1500 ms | students talk without seeing anything happen |
| `decodeMs` average | under 500 ms | live text lags further and further behind |
| `decodeMs` max | under ~1500 ms | occasional visible stalls |
| `finalizeMs` | under ~700 ms | adds directly to the wait for the avatar's answer |
| Repeat-visit ready time | a few seconds | students wait at the loading screen every lesson |

Also note: did the tab reload or crash while loading (iPads kill tabs that use too much memory)?
Did the device get noticeably hot or the fan loud?

## Check compression on the server

From any machine:

```bash
curl -sI -H 'Accept-Encoding: gzip' https://<your-site>/models/parakeet-redux/v1/encoder-model.onnx \
  | grep -i -E 'content-encoding|content-length'
```

Expect `content-encoding: gzip` and a `content-length` around 151 MB. Without them, every device
downloads ~380 MB — check for a proxy in front that strips the encoding.
