// Voice activity detection (VAD) with Silero VAD, off the main thread: turns a live stream of
// microphone audio into "speech started" / "speech ended" events. See lib/vad.ts, its only caller,
// for the message protocol. Chosen over Parakeet's own VAD model after a comparison (fewer false
// alarms on steady background noise, a faster end-of-speech, a sixth of the CPU) — see
// docs/stt-plan.md, phase 3 step 0.
//
// Silero is a small recurrent model: it scores 32 ms of 16 kHz audio at a time and carries its
// state from one chunk to the next, so it runs on the CPU (WASM) on every device, WebGPU or not.

import * as ort from "onnxruntime-web/webgpu";
// The same WASM binary the speech recognition worker uses (see parakeetWorker.ts), so the browser
// downloads and caches it only once.
import ortWasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url";
import { loadModelFile, openModelCache, pruneModelCache, type ManifestFile } from "@/lib/modelFiles";

ort.env.wasm.wasmPaths = { wasm: ortWasmUrl };
// Single-threaded for the same reason as parakeetWorker.ts: no cross-origin isolation (Tally iframe).
ort.env.wasm.numThreads = 1;

const SAMPLE_RATE = 16000;
const CHUNK_SAMPLES = 512; // 32 ms, the window Silero is trained on at 16 kHz
const CONTEXT_SAMPLES = 64; // the end of the previous chunk, prepended to the next (as upstream does)
// Hysteresis as in Silero's own get_speech_timestamps: speech starts above SPEECH_ON and only
// counts as over once the probability stays below SPEECH_OFF.
const SPEECH_ON = 0.5;
const SPEECH_OFF = 0.35;

const MODEL_CACHE = "lola-vad-models";

interface Manifest {
  version: string;
  files: Record<"vad", ManifestFile>;
}

export type VadWorkerInbound =
  | { type: "init"; modelUrl: string }
  /** `minSilenceMs`: how long the probability must stay low before speech counts as ended. */
  | { type: "start"; minSilenceMs: number }
  | { type: "audio"; samples: Float32Array; sampleRate: number }
  | { type: "stop" };

export type VadWorkerOutbound =
  | { type: "ready" }
  | { type: "initError"; message: string }
  /** Times are in ms of audio since "start", so they don't depend on message delays. */
  | { type: "speechStart"; atMs: number }
  | { type: "speechEnd"; atMs: number }
  | { type: "error"; message: string };

function post(message: VadWorkerOutbound) {
  postMessage(message);
}

let session: ort.InferenceSession;

async function init(modelUrl: string) {
  const manifestResponse = await fetch(new URL("manifest.json", modelUrl), { cache: "no-cache" });
  if (!manifestResponse.ok) throw new Error(`VAD manifest not found (HTTP ${manifestResponse.status})`);
  const manifest = (await manifestResponse.json()) as Manifest;
  const cache = await openModelCache(MODEL_CACHE);
  const url = new URL(manifest.files.vad.path, modelUrl).href;
  const bytes = await loadModelFile(url, manifest.files.vad.bytes, cache, () => undefined);
  session = await ort.InferenceSession.create(bytes, { executionProviders: ["wasm"] });
  await pruneModelCache(cache, [url]);
}

/** Averages (or repeats) samples to 16 kHz — the same simple resampler as parakeetWorker.ts. For
 * speech detection, the aliasing a proper low-pass filter would remove doesn't matter. */
class Downsampler {
  private ratio: number;
  private sum = 0;
  private count = 0;
  private position = 0;

  constructor(inputRate: number) {
    this.ratio = inputRate / SAMPLE_RATE;
  }

  process(input: Float32Array): Float32Array {
    if (this.ratio === 1) return input;
    const out = new Float32Array(Math.ceil(input.length / this.ratio) + 1);
    let n = 0;
    for (const sample of input) {
      this.sum += sample;
      this.count++;
      this.position++;
      while (this.position >= this.ratio) {
        out[n++] = this.sum / this.count;
        this.position -= this.ratio;
        if (this.position < this.ratio) {
          this.sum = 0;
          this.count = 0;
        }
      }
    }
    return out.subarray(0, n);
  }
}

/** One recording: its own model state, so recordings never inherit each other's. */
class Stream {
  downsampler: Downsampler | null = null;
  private pending = new Float32Array(0);
  private context = new Float32Array(CONTEXT_SAMPLES);
  private state: ort.Tensor = new ort.Tensor("float32", new Float32Array(2 * 128), [2, 1, 128]);
  private readonly sr = new ort.Tensor("int64", BigInt64Array.from([BigInt(SAMPLE_RATE)]), []);
  private processedSamples = 0;
  private speaking = false;
  private quietSinceSample: number | null = null;

  constructor(private readonly minSilenceSamples: number) {}

  async push(samples: Float32Array) {
    const merged = new Float32Array(this.pending.length + samples.length);
    merged.set(this.pending);
    merged.set(samples, this.pending.length);
    let offset = 0;
    while (merged.length - offset >= CHUNK_SAMPLES) {
      await this.score(merged.subarray(offset, offset + CHUNK_SAMPLES));
      offset += CHUNK_SAMPLES;
    }
    this.pending = merged.slice(offset);
  }

  private async score(chunk: Float32Array) {
    const input = new Float32Array(CONTEXT_SAMPLES + CHUNK_SAMPLES);
    input.set(this.context);
    input.set(chunk, CONTEXT_SAMPLES);
    const out = await session.run({
      input: new ort.Tensor("float32", input, [1, input.length]),
      state: this.state,
      sr: this.sr,
    });
    this.state = out.stateN;
    this.context = chunk.slice(-CONTEXT_SAMPLES);
    this.processedSamples += CHUNK_SAMPLES;
    const probability = (out.output.data as Float32Array)[0];
    const atMs = (this.processedSamples / SAMPLE_RATE) * 1000;

    if (!this.speaking) {
      if (probability >= SPEECH_ON) {
        this.speaking = true;
        this.quietSinceSample = null;
        post({ type: "speechStart", atMs });
      }
      return;
    }
    if (probability >= SPEECH_OFF) {
      this.quietSinceSample = null;
      return;
    }
    this.quietSinceSample ??= this.processedSamples - CHUNK_SAMPLES;
    if (this.processedSamples - this.quietSinceSample >= this.minSilenceSamples) {
      this.speaking = false;
      // Reported at the moment the speech ended, not when the pause was confirmed.
      post({ type: "speechEnd", atMs: (this.quietSinceSample / SAMPLE_RATE) * 1000 });
      this.quietSinceSample = null;
    }
  }
}

let current: Stream | null = null;
// Audio is scored strictly in arrival order; a chunk's model call awaits the previous one's state.
let queue: Promise<void> = Promise.resolve();

onmessage = (event: MessageEvent<VadWorkerInbound>) => {
  const message = event.data;
  switch (message.type) {
    case "init":
      init(message.modelUrl).then(
        () => post({ type: "ready" }),
        (error: unknown) => post({ type: "initError", message: error instanceof Error ? error.message : String(error) }),
      );
      break;
    case "start":
      current = new Stream(Math.round((message.minSilenceMs / 1000) * SAMPLE_RATE));
      break;
    case "audio": {
      const stream = current;
      if (!stream) break;
      stream.downsampler ??= new Downsampler(message.sampleRate);
      const samples = stream.downsampler.process(message.samples);
      queue = queue
        .then(() => (stream === current ? stream.push(samples) : undefined))
        .catch((error: unknown) => {
          current = null;
          post({ type: "error", message: error instanceof Error ? error.message : String(error) });
        });
      break;
    }
    case "stop":
      current = null;
      break;
  }
};
