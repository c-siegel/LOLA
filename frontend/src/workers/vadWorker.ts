// Voice activity detection (VAD) with Silero VAD, off the main thread: turns a live stream of
// microphone audio into "speech started" / "speech ended" events, and — for hands-free voice
// input — into "turn ended" events with Smart Turn. See lib/vad.ts, its only caller, for the
// message protocol. Both models were chosen after measurements, see docs/stt-plan.md (phase 3,
// steps 0 and 2).
//
// Silero is a small recurrent model: it scores 32 ms of 16 kHz audio at a time and carries its
// state from one chunk to the next, so it runs on the CPU (WASM) on every device, WebGPU or not.
// Smart Turn (via @micdrop/smart-turn, which builds its Whisper-style features as the audio
// arrives) is asked once per pause whether the turn sounds finished.

import * as ort from "onnxruntime-web/webgpu";
// The same WASM binary the speech recognition worker uses (see parakeetWorker.ts), so the browser
// downloads and caches it only once.
import ortWasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url";
import { FEATURE_SHAPE, SmartTurn, type SmartTurnModel } from "@micdrop/smart-turn";
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

// Separate caches: each prunes everything in its own cache that isn't its current file.
const VAD_CACHE = "lola-vad-models";
const TURN_CACHE = "lola-turn-models";

interface Manifest<K extends string> {
  version: string;
  files: Record<K, ManifestFile>;
}

/** Hands-free turn detection, see Stream.endOfSpeech. */
export interface TurnConfig {
  /** After this much silence the turn ends, whatever Smart Turn said. */
  maxSilenceMs: number;
  /** Without Smart Turn (failed to load, or too slow here): end the turn after this much silence. */
  fallbackSilenceMs: number;
  /** A prediction slower than this switches Smart Turn off for the rest of the visit. */
  slowPredictMs: number;
}

export type VadWorkerInbound =
  /** `turnModelUrl`: also load Smart Turn (hands-free projects only). */
  | { type: "init"; modelUrl: string; turnModelUrl: string | null }
  /** `minSilenceMs`: how long the probability must stay low before speech counts as ended.
   * `turn`: also report the end of the student's turn (hands-free). */
  | { type: "start"; minSilenceMs: number; turn: TurnConfig | null }
  | { type: "audio"; samples: Float32Array; sampleRate: number }
  | { type: "stop" };

export type TurnEndReason = "complete" | "maxSilence" | "silence";

export type VadWorkerOutbound =
  | { type: "ready"; turnDetection: boolean }
  | { type: "initError"; message: string }
  /** Times are in ms of audio since "start", so they don't depend on message delays. */
  | { type: "speechStart"; atMs: number }
  | { type: "speechEnd"; atMs: number }
  /** `speechEndMs`: when the last speech ended; `atMs`: when the turn was declared over. */
  | { type: "turnEnd"; atMs: number; speechEndMs: number; reason: TurnEndReason; probability: number | null }
  | { type: "error"; message: string };

function post(message: VadWorkerOutbound) {
  postMessage(message);
}

let session: ort.InferenceSession;
// Smart Turn, run on this worker's own ONNX runtime (the package's web loader would bring a second
// copy). Null when not wanted, not loaded, or switched off as too slow for this device.
let turnModel: SmartTurnModel | null = null;

async function fetchManifest<K extends string>(baseUrl: string): Promise<Manifest<K>> {
  const response = await fetch(new URL("manifest.json", baseUrl), { cache: "no-cache" });
  if (!response.ok) throw new Error(`Model manifest not found at ${baseUrl} (HTTP ${response.status})`);
  return (await response.json()) as Manifest<K>;
}

async function loadSession(baseUrl: string, file: ManifestFile, cacheName: string) {
  const cache = await openModelCache(cacheName);
  const url = new URL(file.path, baseUrl).href;
  const bytes = await loadModelFile(url, file.bytes, cache, () => undefined);
  const created = await ort.InferenceSession.create(bytes, { executionProviders: ["wasm"] });
  await pruneModelCache(cache, [url]);
  return created;
}

async function loadTurnModel(baseUrl: string): Promise<SmartTurnModel> {
  const manifest = await fetchManifest<"turn">(baseUrl);
  const turnSession = await loadSession(baseUrl, manifest.files.turn, TURN_CACHE);
  return {
    async predict(features) {
      const out = await turnSession.run({
        input_features: new ort.Tensor("float32", features, [...FEATURE_SHAPE]),
      });
      return Number((Object.values(out)[0].data as Float32Array)[0]);
    },
    release: () => turnSession.release(),
  };
}

async function init(modelUrl: string, turnModelUrl: string | null) {
  const manifest = await fetchManifest<"vad">(modelUrl);
  session = await loadSession(modelUrl, manifest.files.vad, VAD_CACHE);
  if (turnModelUrl) {
    try {
      turnModel = await loadTurnModel(turnModelUrl);
    } catch (error) {
      // Hands-free still works without it, just with a fixed silence (see TurnConfig).
      console.error("Smart Turn unavailable, using a silence timeout instead.", error);
    }
  }
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

const msToSamples = (ms: number) => Math.round((ms / 1000) * SAMPLE_RATE);
const samplesToMs = (samples: number) => (samples / SAMPLE_RATE) * 1000;

/** One recording (push-to-talk) or one turn (hands-free): its own model state, so neither
 * inherits what the previous one heard. */
class Stream {
  downsampler: Downsampler | null = null;
  private pending = new Float32Array(0);
  private context = new Float32Array(CONTEXT_SAMPLES);
  private state: ort.Tensor = new ort.Tensor("float32", new Float32Array(2 * 128), [2, 1, 128]);
  private readonly sr = new ort.Tensor("int64", BigInt64Array.from([BigInt(SAMPLE_RATE)]), []);
  private processedSamples = 0;
  private speaking = false;
  private quietSinceSample: number | null = null;
  // Hands-free only.
  private readonly smartTurn: SmartTurn | null;
  private heardSpeech = false;
  // Set at the end of speech while Smart Turn judged the turn unfinished (or isn't available):
  // the turn then ends after this many samples of continued silence.
  private endTurnAtSilence: number | null = null;
  private turnOver = false;

  constructor(
    private readonly minSilenceSamples: number,
    private readonly turn: TurnConfig | null,
  ) {
    this.smartTurn = turn && turnModel ? new SmartTurn({ model: turnModel }) : null;
  }

  async push(samples: Float32Array) {
    this.smartTurn?.push(samples, SAMPLE_RATE);
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

    if (!this.speaking) {
      if (probability >= SPEECH_ON) {
        this.speaking = true;
        this.heardSpeech = true;
        this.quietSinceSample = null;
        this.endTurnAtSilence = null; // the student went on: the turn isn't over
        post({ type: "speechStart", atMs: samplesToMs(this.processedSamples) });
      } else if (this.endTurnAtSilence !== null && this.quietSinceSample !== null) {
        if (this.processedSamples - this.quietSinceSample >= this.endTurnAtSilence) {
          this.endTurn(this.endTurnReason(), null);
        }
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
      post({ type: "speechEnd", atMs: samplesToMs(this.quietSinceSample) });
      if (this.turn && this.heardSpeech && !this.turnOver) await this.endOfSpeech(this.turn);
    }
  }

  /** Hands-free: at a pause, ask Smart Turn whether the turn sounds finished. It may only make
   * the app wait longer, never shorter than this pause — it judges prosody, so a sentence that
   * just stops can sound finished (see docs/stt-plan.md, phase 3 step 2). */
  private async endOfSpeech(turn: TurnConfig) {
    if (this.smartTurn && turnModel) {
      const result = await this.smartTurn.predict();
      if (result.duration > turn.slowPredictMs) {
        // Too slow on this device to answer at a pause; a fixed silence from now on.
        console.warn(`Smart Turn took ${result.duration} ms here; using a silence timeout instead.`);
        turnModel = null;
      }
      if (result.complete) {
        this.endTurn("complete", result.probability);
        return;
      }
      this.endTurnAtSilence = msToSamples(turn.maxSilenceMs);
      return;
    }
    this.endTurnAtSilence = msToSamples(turn.fallbackSilenceMs);
  }

  private endTurnReason(): TurnEndReason {
    return this.smartTurn && turnModel ? "maxSilence" : "silence";
  }

  private endTurn(reason: TurnEndReason, probability: number | null) {
    this.turnOver = true;
    this.endTurnAtSilence = null;
    post({
      type: "turnEnd",
      atMs: samplesToMs(this.processedSamples),
      speechEndMs: samplesToMs(this.quietSinceSample ?? this.processedSamples),
      reason,
      probability,
    });
  }
}

let current: Stream | null = null;
// Audio is scored strictly in arrival order; a chunk's model call awaits the previous one's state.
let queue: Promise<void> = Promise.resolve();

onmessage = (event: MessageEvent<VadWorkerInbound>) => {
  const message = event.data;
  switch (message.type) {
    case "init":
      init(message.modelUrl, message.turnModelUrl).then(
        () => post({ type: "ready", turnDetection: turnModel !== null }),
        (error: unknown) => post({ type: "initError", message: error instanceof Error ? error.message : String(error) }),
      );
      break;
    case "start":
      current = new Stream(msToSamples(message.minSilenceMs), message.turn);
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
