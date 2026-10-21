// Runs Parakeet Redux speech recognition off the main thread (so the avatar animation and chat UI
// never stutter) and turns a live stream of microphone audio into growing partial text. See
// lib/parakeetStt.ts, its only caller, for the message protocol.
//
// The model isn't natively streaming, so "live text" works like this: the audio since the last
// pause (the "open segment") is decoded again every STEP_SECONDS, which yields an updated partial
// transcript each time. Once the model's own voice-activity head reports a pause, that segment's
// text is committed and the window starts fresh, so each decode stays short.

import * as ort from "onnxruntime-web/webgpu";
// Imported as a URL so Vite emits the file and we can tell onnxruntime-web exactly where it is —
// its own lookup (relative to the bundle) breaks once Vite has moved the bundle around.
import ortWasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url";
import { loadModelFile, openModelCache, pruneModelCache, type ManifestFile } from "@/lib/modelFiles";

ort.env.wasm.wasmPaths = { wasm: ortWasmUrl };
// Multi-threaded WASM needs SharedArrayBuffer, which browsers only allow on a cross-origin-isolated
// page (COOP/COEP headers site-wide). Those headers would break the Tally survey iframe (see
// components/SurveyEmbed), so we stay single-threaded — the heavy part (the encoder) runs on the
// GPU anyway.
ort.env.wasm.numThreads = 1;

const SAMPLE_RATE = 16000;
// One encoder output frame covers 8 feature frames of 10 ms each (see the model's config.json).
const FRAME_SAMPLES = 1280;
const BLANK_TOKEN = 8192;
const VOCAB_LOGITS = 8193;
const TDT_DURATIONS = [0, 1, 2, 3, 4];
const MAX_SYMBOLS_PER_FRAME = 10;

const STEP_SECONDS = 0.5; // how much new audio triggers the next re-decode
const SPEECH_PROBABILITY = 0.5;
const PAUSE_FRAMES = 6; // 480 ms of non-speech ends a segment
const KEEP_SILENCE_FRAMES = 2; // left at the end of a committed segment, so no word gets clipped
// The model was trained on segments of at most 30 s. A student who never pauses gets cut at the
// quietest moment of the last part of the segment instead.
const MAX_SEGMENT_SECONDS = 20;
const MIN_DECODE_SECONDS = 0.3;
const SILENT_SEGMENT_KEEP_SECONDS = 0.5;

const MODEL_CACHE = "lola-stt-models";

interface Manifest {
  version: string;
  files: Record<"preprocessor" | "vad" | "decoder" | "encoder" | "vocab", ManifestFile>;
}

export type WorkerInbound =
  | { type: "init"; modelUrl: string }
  | { type: "start" }
  | { type: "audio"; samples: Float32Array; sampleRate: number }
  | { type: "stop" };

export type WorkerOutbound =
  | { type: "progress"; loadedBytes: number; totalBytes: number }
  | { type: "ready" }
  | { type: "initError"; message: string }
  | { type: "partial"; text: string }
  | { type: "final"; text: string; finalizeMs: number }
  | { type: "streamError"; message: string }
  | { type: "stats"; decodeMs: number; audioSeconds: number };

function post(message: WorkerOutbound) {
  postMessage(message);
}

// ==================== Model loading ====================

let preprocessor: ort.InferenceSession;
let vad: ort.InferenceSession;
let encoder: ort.InferenceSession;
let decoder: ort.InferenceSession;
let vocab: string[] = [];

async function init(modelUrl: string) {
  const manifestResponse = await fetch(new URL("manifest.json", modelUrl), { cache: "no-cache" });
  if (!manifestResponse.ok) throw new Error(`Model manifest not found (HTTP ${manifestResponse.status})`);
  const manifest = (await manifestResponse.json()) as Manifest;
  const cache = await openModelCache(MODEL_CACHE);

  const entries = Object.values(manifest.files);
  const totalBytes = entries.reduce((sum, file) => sum + file.bytes, 0);
  let loadedBytes = 0;
  let lastReport = 0;
  const onBytes = (n: number) => {
    loadedBytes += n;
    const now = performance.now();
    if (now - lastReport > 100 || loadedBytes === totalBytes) {
      lastReport = now;
      post({ type: "progress", loadedBytes, totalBytes });
    }
  };
  const urlOf = (file: ManifestFile) => new URL(file.path, modelUrl).href;
  const load = (file: ManifestFile) => loadModelFile(urlOf(file), file.bytes, cache, onBytes);

  const wasm: ort.InferenceSession.SessionOptions = { executionProviders: ["wasm"] };
  // Small files first, so a broken setup fails fast instead of after the big download.
  const vocabText = new TextDecoder().decode(await load(manifest.files.vocab));
  vocab = parseVocab(vocabText);
  preprocessor = await ort.InferenceSession.create(await load(manifest.files.preprocessor), wasm);
  vad = await ort.InferenceSession.create(await load(manifest.files.vad), wasm);
  decoder = await ort.InferenceSession.create(await load(manifest.files.decoder), wasm);
  // WebGPU only, deliberately no WASM fallback: on the CPU the encoder is far too slow to re-decode
  // every half second, so the caller falls back to server transcription instead.
  encoder = await ort.InferenceSession.create(await load(manifest.files.encoder), {
    executionProviders: ["webgpu"],
  });

  // Drop files from an older model version, so a version bump doesn't leave ~380 MB behind.
  await pruneModelCache(cache, entries.map(urlOf));
}

function parseVocab(text: string): string[] {
  const tokens: string[] = [];
  for (const line of text.split("\n")) {
    const split = line.lastIndexOf(" ");
    if (split <= 0) continue;
    tokens[Number(line.slice(split + 1))] = line.slice(0, split);
  }
  return tokens;
}

// ==================== Inference ====================

interface Features {
  features: ort.Tensor;
  length: ort.Tensor;
}

async function computeFeatures(audio: Float32Array): Promise<Features> {
  const out = await preprocessor.run({
    waveforms: new ort.Tensor("float32", audio, [1, audio.length]),
    waveforms_lens: new ort.Tensor("int64", BigInt64Array.from([BigInt(audio.length)]), [1]),
  });
  return { features: out.features, length: out.features_lens };
}

/** Speech probability per encoder frame (80 ms each). */
async function speechProbabilities({ features, length }: Features): Promise<Float32Array> {
  const out = await vad.run({ audio_signal: features, length });
  return out.probabilities.data as Float32Array;
}

/** Encoder + greedy TDT (token-and-duration transducer) decoding: at every encoder frame, the
 * joint network picks a token and how many frames to skip ahead. */
async function transcribeFeatures({ features, length }: Features): Promise<string> {
  const encoded = await encoder.run({ audio_signal: features, length });
  const frames = Number((encoded.encoded_lengths.data as BigInt64Array)[0]);
  const hidden = encoded.outputs.dims[1];
  const stride = encoded.outputs.dims[2];
  const data = encoded.outputs.data as Float32Array;

  let state1: ort.Tensor = new ort.Tensor("float32", new Float32Array(2 * 640), [2, 1, 640]);
  let state2: ort.Tensor = new ort.Tensor("float32", new Float32Array(2 * 640), [2, 1, 640]);
  let lastToken = BLANK_TOKEN;
  const tokens: number[] = [];
  const frame = new Float32Array(hidden);
  let t = 0;
  let symbolsThisFrame = 0;
  while (t < frames) {
    // Encoder output is [1, hidden, frames], so one frame is a strided column.
    for (let c = 0; c < hidden; c++) frame[c] = data[c * stride + t];
    const out = await decoder.run({
      encoder_outputs: new ort.Tensor("float32", frame.slice(), [1, hidden, 1]),
      targets: new ort.Tensor("int32", Int32Array.from([lastToken]), [1, 1]),
      target_length: new ort.Tensor("int32", Int32Array.from([1]), [1]),
      input_states_1: state1,
      input_states_2: state2,
    });
    const logits = out.outputs.data as Float32Array;
    const token = argmax(logits, 0, VOCAB_LOGITS);
    const duration = TDT_DURATIONS[argmax(logits, VOCAB_LOGITS, logits.length) - VOCAB_LOGITS];
    if (token !== BLANK_TOKEN) {
      tokens.push(token);
      lastToken = token;
      // The prediction network only advances on a real token, not on a blank.
      state1 = out.output_states_1;
      state2 = out.output_states_2;
      symbolsThisFrame++;
    }
    if (duration > 0) {
      t += duration;
      symbolsThisFrame = 0;
    } else if (token === BLANK_TOKEN || symbolsThisFrame >= MAX_SYMBOLS_PER_FRAME) {
      t += 1;
      symbolsThisFrame = 0;
    }
  }
  return detokenize(tokens);
}

function argmax(values: Float32Array, from: number, to: number): number {
  let best = from;
  for (let i = from + 1; i < to; i++) if (values[i] > values[best]) best = i;
  return best;
}

function detokenize(tokens: number[]): string {
  return tokens
    .map((id) => vocab[id] ?? "")
    .filter((piece) => !/^<.*>$/.test(piece)) // <unk>, <|nospeech|>, ...
    .join("")
    .replace(/▁/g, " ") // SentencePiece marks a word start with "▁"
    .trim();
}

async function transcribe(audio: Float32Array): Promise<string> {
  return transcribeFeatures(await computeFeatures(audio));
}

// ==================== Streaming ====================

/** Append-only audio buffer that can also drop audio from the front. */
class AudioBuffer16k {
  private data = new Float32Array(SAMPLE_RATE * 4);
  private start = 0;
  private end = 0;

  get length() {
    return this.end - this.start;
  }

  push(samples: Float32Array) {
    if (this.end + samples.length > this.data.length) {
      const needed = this.length + samples.length;
      const next = new Float32Array(Math.max(needed * 2, this.data.length));
      next.set(this.data.subarray(this.start, this.end));
      this.end -= this.start;
      this.start = 0;
      this.data = next;
    }
    this.data.set(samples, this.end);
    this.end += samples.length;
  }

  copy(from = 0, to = this.length): Float32Array {
    return this.data.slice(this.start + from, this.start + to);
  }

  dropFront(samples: number) {
    this.start += Math.min(samples, this.length);
  }
}

/** Downsamples the AudioContext's rate (usually 44.1 or 48 kHz) to 16 kHz by averaging each
 * output sample's input window — a simple low-pass filter, good enough for speech. */
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
        // Only true when upsampling (ratio < 1): repeat the sample instead of averaging.
        if (this.position < this.ratio) {
          this.sum = 0;
          this.count = 0;
        }
      }
    }
    return out.subarray(0, n);
  }
}

class Session {
  downsampler: Downsampler | null = null;
  // The open segment: everything since the last committed pause.
  segment = new AudioBuffer16k();
  committed: string[] = [];
  // How much of `segment` the last decode already covered.
  decodedLength = 0;
  decodeQueued = false;
}

let current: Session | null = null;
// Every decode runs strictly one after another; new audio keeps arriving meanwhile. On a slow
// device this just means fewer, bigger re-decodes instead of a growing backlog.
let chain: Promise<void> = Promise.resolve();

function joinText(parts: string[]): string {
  return parts.filter(Boolean).join(" ");
}

function onAudio(session: Session, samples: Float32Array, sampleRate: number) {
  session.downsampler ??= new Downsampler(sampleRate);
  const audio = session.downsampler.process(samples);
  session.segment.push(audio);
  scheduleDecode(session);
}

function scheduleDecode(session: Session) {
  if (session.decodeQueued || session !== current) return;
  if (session.segment.length - session.decodedLength < STEP_SECONDS * SAMPLE_RATE) return;
  session.decodeQueued = true;
  chain = chain.then(async () => {
    session.decodeQueued = false;
    if (session !== current) return; // stopped meanwhile — finish() takes over
    try {
      await decodeOpenSegment(session);
    } catch (error) {
      post({ type: "streamError", message: errorText(error) });
      current = null;
      return;
    }
    scheduleDecode(session);
  });
}

async function decodeOpenSegment(session: Session) {
  const started = performance.now();
  const audio = session.segment.copy();
  if (audio.length < MIN_DECODE_SECONDS * SAMPLE_RATE) return;
  session.decodedLength = audio.length;

  const features = await computeFeatures(audio);
  const probabilities = await speechProbabilities(features);
  const hasSpeech = probabilities.some((p) => p > SPEECH_PROBABILITY);
  if (!hasSpeech) {
    // Nothing said yet: keep only a little lead-in, so silence doesn't make every decode longer.
    const drop = audio.length - SILENT_SEGMENT_KEEP_SECONDS * SAMPLE_RATE;
    if (drop > 0) {
      session.segment.dropFront(drop);
      session.decodedLength -= drop;
    }
    return;
  }

  const text = await transcribeFeatures(features);
  post({ type: "partial", text: joinText([...session.committed, text]) });
  post({ type: "stats", decodeMs: performance.now() - started, audioSeconds: audio.length / SAMPLE_RATE });

  let trailingSilence = 0;
  for (let i = probabilities.length - 1; i >= 0 && probabilities[i] <= SPEECH_PROBABILITY; i--) trailingSilence++;

  if (trailingSilence >= PAUSE_FRAMES) {
    session.committed.push(text);
    const cut = audio.length - (trailingSilence - KEEP_SILENCE_FRAMES) * FRAME_SAMPLES;
    session.segment.dropFront(cut);
    session.decodedLength -= cut;
  } else if (audio.length > MAX_SEGMENT_SECONDS * SAMPLE_RATE) {
    // Cut at the quietest frame in the last 40% of the segment, then decode exactly up to there.
    const from = Math.floor(probabilities.length * 0.6);
    const quietest = argmax(probabilities.map((p) => -p), from, probabilities.length);
    const cut = Math.min(audio.length, quietest * FRAME_SAMPLES);
    session.committed.push(await transcribe(audio.subarray(0, cut)));
    session.segment.dropFront(cut);
    session.decodedLength -= cut;
  }
}

async function finish(session: Session) {
  const started = performance.now();
  const rest = session.segment.copy();
  if (rest.length >= MIN_DECODE_SECONDS * SAMPLE_RATE) {
    const features = await computeFeatures(rest);
    const probabilities = await speechProbabilities(features);
    if (probabilities.some((p) => p > SPEECH_PROBABILITY)) {
      session.committed.push(await transcribeFeatures(features));
    }
  }
  post({ type: "final", text: joinText(session.committed), finalizeMs: performance.now() - started });
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

self.onmessage = (event: MessageEvent<WorkerInbound>) => {
  const message = event.data;
  switch (message.type) {
    case "init":
      init(message.modelUrl).then(
        () => post({ type: "ready" }),
        (error) => post({ type: "initError", message: errorText(error) }),
      );
      break;
    case "start":
      current = new Session();
      break;
    case "audio":
      if (current) onAudio(current, message.samples, message.sampleRate);
      break;
    case "stop": {
      const session = current;
      current = null;
      if (!session) {
        post({ type: "final", text: "", finalizeMs: 0 });
        break;
      }
      chain = chain.then(() => finish(session)).catch((error) => post({ type: "streamError", message: errorText(error) }));
      break;
    }
  }
};
