// Pocket TTS (Kyutai) speech generation on ONNX Runtime: text preparation, chunking, the
// autoregressive flow-LM loop and streaming Mimi decoding, for the int8 export that
// scripts/fetch-tts-model.sh downloads. Follows Kyutai's own pocket-tts 3.3 (text_chunking.py and
// tts_model.py) step by step, so that browser and server read text the same way; KevinAHM's
// pocket-tts-web (Apache-2.0) was the reference for running it on onnxruntime-web.
//
// Nothing in here touches the DOM, fetch or a specific ONNX Runtime build: the worker
// (workers/pocketWorker.ts) loads the files and hands over sessions, the tokenizer and a tensor
// factory. That keeps the logic checkable against the Python reference outside a browser.

import { Tokenizer } from "@huggingface/tokenizers";

export type TensorType = "float32" | "float16" | "int64" | "bool";
type TensorData = Float32Array | Uint16Array | BigInt64Array | Uint8Array;

/** The slice of onnxruntime's Tensor this module needs (web and node builds both fit). */
export interface PocketTensor {
  readonly type: string;
  readonly data: unknown;
  readonly dims: readonly number[];
}

export interface PocketSession {
  run(feeds: Record<string, PocketTensor>): Promise<Record<string, PocketTensor>>;
}

export interface PocketTokenizer {
  encode(text: string): number[];
  decode(ids: number[]): string;
}

export type MakeTensor = (type: TensorType, data: TensorData, dims: number[]) => PocketTensor;

interface StateEntry {
  input_name: string;
  output_name: string;
  module: string;
  key: string;
  dtype: TensorType;
  shape: number[];
  fill: "zeros" | "ones" | "nan" | "empty";
}

/** bundle.json of the export (schema 2), only the fields used here. */
export interface PocketBundle {
  sample_rate: number;
  samples_per_frame: number;
  frame_rate: number;
  latent_dim: number;
  conditioning_dim: number;
  max_token_per_chunk: number;
  remove_semicolons: boolean;
  pad_with_spaces_for_short_inputs: boolean;
  model_recommended_frames_after_eos: number | null;
  flow_lm_state_manifest: StateEntry[];
  mimi_state_manifest: StateEntry[];
}

export interface PocketSessions {
  textConditioner: PocketSession;
  flowLmMain: PocketSession;
  flowLmFlow: PocketSession;
  mimiDecoder: PocketSession;
}

export type FlowState = Record<string, PocketTensor>;

// Kyutai's defaults (pocket_tts/default_parameters.py, tts_model.py); the export's bundle doesn't
// carry them.
const TEMPERATURE = 0.3;
const EOS_THRESHOLD = -4.0;
const MIN_FRAMES_BEFORE_EOS = 6;
const TOKENS_PER_SECOND_ESTIMATE = 3.0;
const GEN_SECONDS_PADDING = 2.0;
// Frames per Mimi decoder call: a short first call so audio starts early, then larger ones (fewer
// calls, same total work).
const FIRST_DECODE_FRAMES = 3;
const DECODE_FRAMES = 12;

// From Kyutai's config/german.yaml (replace_characters), which the export's bundle leaves out:
// quotes and brackets are dropped, curly apostrophes become straight ones.
const GERMAN_REPLACEMENTS: Record<string, string> = {
  '"': "", "“": "", "”": "", "„": "", "«": "", "»": "",
  "’": "'", "‘": "'", "(": "", ")": "", "[": "", "]": "",
};
const TERMINAL_PUNCTUATION = ".!?…";
const WEAK_PUNCTUATION = ",;:-–—";
const CLOSERS = "\"'”’)]»";

// ==================== TOKENIZER ====================

interface UnigramTokenizerJson {
  model: { type: string; vocab: Array<[string, number]>; unk_id: number; byte_fallback?: boolean };
}

/** Kyutai's tokenizer.json (a SentencePiece Unigram model) on @huggingface/tokenizers, plus the
 * byte fallback that library only implements for BPE: a character missing from the vocabulary
 * becomes its UTF-8 byte tokens ("<0xE2>"…), as in Python, instead of a bare <unk> the model never
 * saw in training. */
export function createPocketTokenizer(json: UnigramTokenizerJson): PocketTokenizer {
  const tokenizer = new Tokenizer(json, {});
  const ids = new Map(json.model.vocab.map(([piece], id) => [piece, id]));
  const unk = json.model.unk_id;
  const utf8 = new TextEncoder();

  const encode = (text: string): number[] => {
    const encoding = tokenizer.encode(text, { add_special_tokens: false });
    if (!json.model.byte_fallback || !encoding.ids.includes(unk)) return encoding.ids;
    // Every known piece is literally a substring of the text as the tokenizer sees it (word starts
    // marked with "▁"), so the text an <unk> stood for runs up to where the next piece starts —
    // which can't be inside the unknown stretch, since its characters aren't in the vocabulary.
    const seen = "▁" + text.replace(/ /g, "▁");
    const result: number[] = [];
    let position = 0;
    encoding.ids.forEach((id, i) => {
      if (id !== unk) {
        result.push(id);
        const found = seen.indexOf(encoding.tokens[i], position);
        if (found !== -1) position = found + encoding.tokens[i].length;
        return;
      }
      const next = encoding.ids.slice(i + 1).findIndex((other) => other !== unk);
      const end = next === -1 ? seen.length : seen.indexOf(encoding.tokens[i + 1 + next], position);
      const unknown = seen.slice(position, end === -1 ? seen.length : end);
      for (const byte of utf8.encode(unknown)) {
        result.push(ids.get(`<0x${byte.toString(16).toUpperCase().padStart(2, "0")}>`) ?? unk);
      }
      position += unknown.length;
    });
    return result;
  };

  return { encode, decode: (tokenIds) => tokenizer.decode(tokenIds) };
}

// ==================== TEXT ====================

function stripEnd(text: string, chars: string): string {
  let end = text.length;
  while (end > 0 && chars.includes(text[end - 1])) end--;
  return text.slice(0, end);
}

function ensureTerminalPunctuation(text: string): string {
  const core = stripEnd(text, CLOSERS + " ");
  const closers = text.slice(core.length).trim();
  if (!core || TERMINAL_PUNCTUATION.includes(core[core.length - 1])) return text;
  if (WEAK_PUNCTUATION.includes(core[core.length - 1])) return stripEnd(core, WEAK_PUNCTUATION + " ") + "." + closers;
  return text + ".";
}

/** pocket-tts' prepare_text_prompt: the text the model gets, and how many frames to let run on
 * after the end-of-speech signal (more for very short texts, which otherwise get cut off). */
export function prepareText(text: string, bundle: PocketBundle): { text: string; framesAfterEos: number } {
  let prepared = text.trim();
  prepared = [...prepared].map((ch) => GERMAN_REPLACEMENTS[ch] ?? ch).join("").split(/\s+/).filter(Boolean).join(" ");
  // Deleted quotes leave '"Hi?", she said' as 'Hi?, she said'; keep the sentence mark only.
  prepared = prepared.replace(/([.!?…])\s*[,;:]/g, "$1");
  if (!prepared) throw new Error("Text prompt cannot be empty");
  prepared = prepared.replace(/[\n\r]/g, " ").replace(/ {2}/g, " ");
  if (bundle.remove_semicolons) prepared = prepared.replace(/;/g, ",");
  const words = prepared.split(/\s+/).filter(Boolean).length;
  let framesAfterEos = words <= 4 ? 3 : 1;
  if (prepared[0] !== prepared[0].toUpperCase()) prepared = prepared[0].toUpperCase() + prepared.slice(1);
  prepared = ensureTerminalPunctuation(prepared);
  if (bundle.pad_with_spaces_for_short_inputs && prepared.split(/\s+/).filter(Boolean).length < 5) {
    prepared = " ".repeat(8) + prepared;
  }
  framesAfterEos += 2; // tts_model.py adds 2 to the guess
  return { text: prepared, framesAfterEos: bundle.model_recommended_frames_after_eos ?? framesAfterEos };
}

function boundaryIndices(tokens: number[], boundary: Set<number>, tokenizer?: PocketTokenizer): number[] {
  const indices = [0];
  let previousWasBoundary = false;
  tokens.forEach((token, index) => {
    if (boundary.has(token)) {
      previousWasBoundary = true;
      return;
    }
    if (previousWasBoundary) {
      // "1.5" must not be split after the period (text_chunking.py, skip_decimal_periods).
      const decimal =
        tokenizer !== undefined &&
        (() => {
          const prefix = tokenizer.decode(tokens.slice(0, index));
          const suffix = tokenizer.decode(tokens.slice(index));
          return prefix.length >= 2 && prefix.endsWith(".") && /\d/.test(prefix[prefix.length - 2]) && /^\d/.test(suffix);
        })();
      if (!decimal) indices.push(index);
    }
    previousWasBoundary = false;
  });
  indices.push(tokens.length);
  return indices;
}

function segments(tokens: number[], indices: number[], tokenizer: PocketTokenizer): Array<[number, string]> {
  const result: Array<[number, string]> = [];
  for (let i = 0; i < indices.length - 1; i++) {
    result.push([indices[i + 1] - indices[i], tokenizer.decode(tokens.slice(indices[i], indices[i + 1]))]);
  }
  return result;
}

/** pocket-tts' split_into_best_sentences: chunks of whole sentences, at most max_token_per_chunk
 * tokens each where possible (the model skips words in longer ones). */
export function splitIntoChunks(text: string, tokenizer: PocketTokenizer, bundle: PocketBundle): string[] {
  const maxTokens = bundle.max_token_per_chunk;
  const prepared = prepareText(text, bundle).text.trim();
  const tokens = tokenizer.encode(prepared);
  // The first token of these is the word-start marker, not punctuation.
  const sentenceEnds = new Set(tokenizer.encode(".!...?").slice(1));
  const fallback = new Set(tokenizer.encode(",;:").slice(1));

  const refined: Array<[number, string]> = [];
  for (const [count, sentence] of segments(tokens, boundaryIndices(tokens, sentenceEnds, tokenizer), tokenizer)) {
    if (count <= maxTokens) {
      refined.push([count, sentence]);
      continue;
    }
    const subTokens = tokenizer.encode(sentence.trim());
    const sub = segments(subTokens, boundaryIndices(subTokens, fallback), tokenizer);
    if (sub.length > 1) refined.push(...sub);
    else refined.push([count, sentence]);
  }

  const chunks: string[] = [];
  let current = "";
  let currentCount = 0;
  for (const [count, sentence] of refined) {
    if (current === "") {
      current = sentence;
      currentCount = count;
    } else if (currentCount + count > maxTokens) {
      chunks.push(current.trim());
      current = sentence;
      currentCount = count;
    } else {
      current += " " + sentence;
      currentCount += count;
    }
  }
  if (current !== "") chunks.push(current.trim());
  return chunks;
}

// ==================== STATE ====================

const FLOAT16_NAN = 0x7e00;
const FLOAT16_ONE = 0x3c00;

function filled(entry: StateEntry, makeTensor: MakeTensor): PocketTensor {
  const size = entry.shape.reduce((a, b) => a * b, 1);
  const one = entry.fill === "ones";
  const nan = entry.fill === "nan";
  switch (entry.dtype) {
    case "float32":
      return makeTensor("float32", new Float32Array(size).fill(nan ? NaN : one ? 1 : 0), entry.shape);
    case "float16":
      return makeTensor("float16", new Uint16Array(size).fill(nan ? FLOAT16_NAN : one ? FLOAT16_ONE : 0), entry.shape);
    case "int64":
      return makeTensor("int64", new BigInt64Array(size).fill(one ? 1n : 0n), entry.shape);
    case "bool":
      return makeTensor("bool", new Uint8Array(size).fill(one ? 1 : 0), entry.shape);
  }
}

export function initialState(manifest: StateEntry[], makeTensor: MakeTensor): FlowState {
  return Object.fromEntries(manifest.map((entry) => [entry.input_name, filled(entry, makeTensor)]));
}

function nextState(manifest: StateEntry[], outputs: Record<string, PocketTensor>): FlowState {
  return Object.fromEntries(manifest.map((entry) => [entry.input_name, outputs[entry.output_name]]));
}

// float32 -> IEEE half bits (round to nearest; overflow to infinity). ORT Web takes float16
// tensors as Uint16Array; Float16Array isn't available in every browser LOLA supports yet.
const f32 = new Float32Array(1);
const u32 = new Uint32Array(f32.buffer);
export function toFloat16(value: number): number {
  f32[0] = value;
  const x = u32[0];
  const sign = (x >>> 16) & 0x8000;
  const rawExp = (x >>> 23) & 0xff;
  let mant = x & 0x7fffff;
  if (rawExp === 0xff) return sign | 0x7c00 | (mant ? 0x200 : 0);
  let exp = rawExp - 127 + 15;
  if (exp >= 0x1f) return sign | 0x7c00;
  if (exp <= 0) {
    if (exp < -10) return sign;
    mant = (mant | 0x800000) >> (1 - exp);
    if (mant & 0x1000) mant += 0x2000;
    return sign | (mant >> 13);
  }
  if (mant & 0x1000) {
    mant += 0x2000;
    if (mant & 0x800000) {
      mant = 0;
      exp += 1;
      if (exp >= 0x1f) return sign | 0x7c00;
    }
  }
  return sign | (exp << 10) | (mant >> 13);
}

interface SafetensorsEntry {
  dtype: string;
  shape: number[];
  data_offsets: [number, number];
}

/** A Kyutai predefined voice (safetensors: per layer `<module>/cache` float32 [2, 1, T, heads,
 * dim] with keys and values stacked, `<module>/offset` = T) as the export's flow-LM state:
 * `cache_k` / `cache_v` float16 [1, capacity, heads, dim] with the first T rows filled, `step` = T.
 * A step of 0 would make the model ignore the voice entirely. */
export function voiceStateFromSafetensors(buffer: ArrayBuffer, bundle: PocketBundle, makeTensor: MakeTensor): FlowState {
  const view = new DataView(buffer);
  const headerLength = Number(view.getBigUint64(0, true));
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 8, headerLength))) as Record<string, SafetensorsEntry>;
  const dataStart = 8 + headerLength;
  const tensor = (name: string) => {
    const entry = header[name];
    if (!entry) throw new Error(`Voice file has no tensor ${name}`);
    return { entry, bytes: buffer.slice(dataStart + entry.data_offsets[0], dataStart + entry.data_offsets[1]) };
  };

  const state = initialState(bundle.flow_lm_state_manifest, makeTensor);
  for (const target of bundle.flow_lm_state_manifest) {
    if (target.key === "step") {
      const offset = tensor(`${target.module}/offset`);
      state[target.input_name] = makeTensor("int64", new BigInt64Array(offset.bytes.slice(0, 8)), target.shape);
      continue;
    }
    if (target.key !== "cache_k" && target.key !== "cache_v") throw new Error(`Unsupported voice state key ${target.key}`);
    const { entry, bytes } = tensor(`${target.module}/cache`);
    if (entry.dtype !== "F32" || entry.shape[0] !== 2) throw new Error(`Unexpected voice cache layout ${entry.dtype} ${entry.shape}`);
    const half = entry.shape.slice(1).reduce((a, b) => a * b, 1); // [1, T, heads, dim]
    const source = new Float32Array(bytes, target.key === "cache_k" ? 0 : half * 4, half);
    const capacity = target.shape.reduce((a, b) => a * b, 1);
    if (half > capacity) throw new Error("Voice prompt is longer than the model's attention cache");
    const data = new Uint16Array(capacity);
    for (let i = 0; i < half; i++) data[i] = toFloat16(source[i]);
    state[target.input_name] = makeTensor("float16", data, target.shape);
  }
  return state;
}

// ==================== GENERATION ====================

export interface GenerateOptions {
  /** Standard normal samples; Box-Muller over Math.random by default. Injectable for tests. */
  randomNormal?: () => number;
  signal?: AbortSignal;
}

function boxMuller(): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

export class PocketTtsModel {
  constructor(
    private readonly sessions: PocketSessions,
    private readonly tokenizer: PocketTokenizer,
    readonly bundle: PocketBundle,
    private readonly makeTensor: MakeTensor,
  ) {}

  get sampleRate(): number {
    return this.bundle.sample_rate;
  }

  /** Speaks `text` with `voice` (see voiceStateFromSafetensors), yielding audio as it's decoded. */
  async *stream(text: string, voice: FlowState, options: GenerateOptions = {}): AsyncGenerator<Float32Array> {
    for (const chunk of splitIntoChunks(text, this.tokenizer, this.bundle)) {
      if (options.signal?.aborted) return;
      yield* this.streamChunk(chunk, voice, options);
    }
  }

  private async *streamChunk(chunk: string, voice: FlowState, options: GenerateOptions): AsyncGenerator<Float32Array> {
    const { bundle, makeTensor, sessions } = this;
    const randomNormal = options.randomNormal ?? boxMuller;
    const { text, framesAfterEos } = prepareText(chunk, bundle);
    const tokens = this.tokenizer.encode(text);
    const maxFrames = Math.ceil((tokens.length / TOKENS_PER_SECOND_ESTIMATE + GEN_SECONDS_PADDING) * bundle.frame_rate);
    const latentDim = bundle.latent_dim;

    const textEmbeddings = (
      await sessions.textConditioner.run({
        token_ids: makeTensor("int64", BigInt64Array.from(tokens, (id) => BigInt(id)), [1, tokens.length]),
      })
    ).embeddings;
    const noText = makeTensor("float32", new Float32Array(0), [1, 0, bundle.conditioning_dim]);
    // Every chunk starts from the voice alone (a copy is implicit: states are replaced, never mutated).
    let flowState = nextState(
      bundle.flow_lm_state_manifest,
      await sessions.flowLmMain.run({
        sequence: makeTensor("float32", new Float32Array(0), [1, 0, latentDim]),
        text_embeddings: textEmbeddings,
        ...voice,
      }),
    );
    let mimiState = initialState(bundle.mimi_state_manifest, makeTensor);

    const pending: Float32Array[] = [];
    let decodedAny = false;
    const decode = async (frames: number): Promise<Float32Array> => {
      const latents = new Float32Array(frames * latentDim);
      pending.splice(0, frames).forEach((latent, i) => latents.set(latent, i * latentDim));
      const outputs = await sessions.mimiDecoder.run({
        latent: makeTensor("float32", latents, [1, frames, latentDim]),
        ...mimiState,
      });
      mimiState = nextState(bundle.mimi_state_manifest, outputs);
      decodedAny = true;
      return new Float32Array(outputs.audio_frame.data as Float32Array);
    };

    let input = makeTensor("float32", new Float32Array(latentDim).fill(NaN), [1, 1, latentDim]);
    let eosStep: number | null = null;
    const std = Math.sqrt(TEMPERATURE);
    for (let step = 0; step < maxFrames; step++) {
      if (options.signal?.aborted) return;
      const outputs = await sessions.flowLmMain.run({ sequence: input, text_embeddings: noText, ...flowState });
      flowState = nextState(bundle.flow_lm_state_manifest, outputs);
      // A scalar in this export, [1, 1] in others.
      const eosLogit = (outputs.eos_logit.data as Float32Array)[0];
      if (eosLogit > EOS_THRESHOLD && eosStep === null && step >= MIN_FRAMES_BEFORE_EOS) eosStep = step;
      if (eosStep !== null && step >= eosStep + framesAfterEos) break;

      // One Euler step of the flow (Kyutai's default sampler_decode_steps = 1), from noise at
      // the model's temperature.
      const x = new Float32Array(latentDim);
      for (let i = 0; i < latentDim; i++) x[i] = randomNormal() * std;
      const flow = await sessions.flowLmFlow.run({
        c: outputs.conditioning,
        s: makeTensor("float32", new Float32Array([0]), [1, 1]),
        t: makeTensor("float32", new Float32Array([1]), [1, 1]),
        x: makeTensor("float32", x, [1, latentDim]),
      });
      const direction = flow.flow_dir.data as Float32Array;
      for (let i = 0; i < latentDim; i++) x[i] += direction[i];

      pending.push(x);
      input = makeTensor("float32", x, [1, 1, latentDim]);
      const batch = decodedAny ? DECODE_FRAMES : FIRST_DECODE_FRAMES;
      if (pending.length >= batch) yield await decode(batch);
    }
    if (pending.length) yield await decode(pending.length);
  }
}
