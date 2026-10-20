// Runs Pocket TTS (Kyutai, German) in the browser off the main thread, so speech generation never
// blocks the avatar's render loop or the chat UI. Only used by the latency test page
// (pages/Dashboard/LatencyLab) through lib/pocketTts.ts, which has the message protocol. The
// generation itself is lib/pocketTtsCore.ts; this file loads the files and talks to the page.
//
// The model files come from scripts/fetch-tts-model.sh (self-hosted under /models/, CC BY 4.0,
// see the NOTICE.md next to them); the voice is Kyutai's predefined "alba".

import * as ort from "onnxruntime-web/wasm";
// Imported as a URL so Vite emits it — onnxruntime-web's own lookup breaks once Vite has moved the
// bundle (see parakeetWorker.ts).
import ortWasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.wasm?url";
import { loadModelFile, openModelCache, pruneModelCache, type ManifestFile } from "@/lib/modelFiles";
import {
  PocketTtsModel,
  createPocketTokenizer,
  voiceStateFromSafetensors,
  type FlowState,
  type MakeTensor,
  type PocketBundle,
} from "@/lib/pocketTtsCore";

ort.env.wasm.wasmPaths = { wasm: ortWasmUrl };
// Multi-threaded WASM needs a cross-origin-isolated page, which LOLA isn't (see parakeetWorker.ts).
// The int8 model still runs about three times faster than real time on one laptop core.
ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 1) : 1;

const MODEL_CACHE = "lola-tts-models";

interface Manifest {
  version: string;
  files: Record<"bundle" | "tokenizerJson" | "textConditioner" | "flowLmMain" | "flowLmFlow" | "mimiDecoder", ManifestFile>;
  voices: Record<string, ManifestFile & { credit: string }>;
}

export type PocketInbound =
  | { type: "init"; modelUrl: string }
  | { type: "synthesize"; id: number; text: string };

export type PocketOutbound =
  | { type: "progress"; loadedBytes: number; totalBytes: number }
  | { type: "ready"; backend: string; voice: string; credit: string }
  | { type: "audio"; id: number; samples: Float32Array<ArrayBuffer>; sampleRate: number }
  | { type: "done"; id: number; synthMs: number; audioSeconds: number }
  | { type: "error"; id: number | null; message: string };

let model: PocketTtsModel | null = null;
let voice: FlowState | null = null;
// Requests run one after another, in the order the reply's chunks arrive.
let chain: Promise<void> = Promise.resolve();

function post(message: PocketOutbound, transfer: Transferable[] = []) {
  // The options form, because this project type-checks workers against the DOM lib.
  postMessage(message, { transfer });
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// ORT Web takes float16 data as Uint16Array bits; recent browsers also have a Float16Array, which
// onnxruntime-common then converts to on its own — both describe the same bytes.
const makeTensor: MakeTensor = (type, data, dims) => new ort.Tensor(type, data as never, dims);

async function init(modelUrl: string) {
  const manifestResponse = await fetch(new URL("manifest.json", modelUrl), { cache: "no-cache" });
  if (!manifestResponse.ok) throw new Error(`Pocket TTS manifest not found (HTTP ${manifestResponse.status}) — run scripts/fetch-tts-model.sh`);
  const manifest = (await manifestResponse.json()) as Manifest;
  const [voiceName, voiceFile] = Object.entries(manifest.voices)[0];
  const cache = await openModelCache(MODEL_CACHE);

  const files = [...Object.values(manifest.files), voiceFile];
  const totalBytes = files.reduce((sum, file) => sum + file.bytes, 0);
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
  const json = async (file: ManifestFile) => JSON.parse(new TextDecoder().decode(await load(file)));
  const session = async (file: ManifestFile) =>
    ort.InferenceSession.create(await load(file), { executionProviders: ["wasm"], graphOptimizationLevel: "all" });

  // Small files first, so a broken setup fails fast instead of after the big download.
  const bundle = (await json(manifest.files.bundle)) as PocketBundle;
  const tokenizer = createPocketTokenizer(await json(manifest.files.tokenizerJson));
  const voiceBytes = await load(voiceFile);
  model = new PocketTtsModel(
    {
      textConditioner: await session(manifest.files.textConditioner),
      flowLmFlow: await session(manifest.files.flowLmFlow),
      mimiDecoder: await session(manifest.files.mimiDecoder),
      flowLmMain: await session(manifest.files.flowLmMain),
    },
    tokenizer,
    bundle,
    makeTensor,
  );
  voice = voiceStateFromSafetensors(voiceBytes.buffer, bundle, makeTensor);
  await pruneModelCache(cache, files.map(urlOf));
  post({ type: "ready", backend: `wasm-int8 ×${ort.env.wasm.numThreads}`, voice: voiceName, credit: voiceFile.credit });
}

async function synthesize(id: number, text: string) {
  if (!model || !voice) throw new Error("Pocket TTS is not loaded.");
  const started = performance.now();
  let samples = 0;
  for await (const audio of model.stream(text, voice)) {
    samples += audio.length;
    const transferable = new Float32Array(audio);
    post({ type: "audio", id, samples: transferable, sampleRate: model.sampleRate }, [transferable.buffer]);
  }
  post({ type: "done", id, synthMs: performance.now() - started, audioSeconds: samples / model.sampleRate });
}

self.onmessage = (event: MessageEvent<PocketInbound>) => {
  const message = event.data;
  const run = (id: number | null, task: () => Promise<void>) => {
    chain = chain.then(task).catch((error) => post({ type: "error", id, message: errorText(error) }));
  };
  switch (message.type) {
    case "init":
      run(null, () => init(message.modelUrl));
      break;
    case "synthesize":
      run(message.id, () => synthesize(message.id, message.text));
      break;
  }
};
