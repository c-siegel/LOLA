// Sopro text-to-speech in the browser, for the latency test page (pages/Dashboard/LatencyLab):
// the main-thread side of workers/soproWorker.ts. Speaks with a voice cloned from a reference
// clip the teacher picks on that page.

import type { SoproInbound, SoproOutbound } from "@/workers/soproWorker";
import type { BrowserSynthesis, BrowserTts } from "@/lib/browserTts";
import type { LoadProgress } from "@/lib/parakeetStt";

// Sopro v2 only speaks these; anything else lets the model detect the language itself.
const SOPRO_LANGUAGES = new Set(["en", "de", "fr", "pt"]);

interface PendingSynthesis {
  started: number;
  firstAudioMs: number | null;
  onAudio: (samples: Float32Array<ArrayBuffer>, sampleRate: number) => void;
  resolve: (result: BrowserSynthesis) => void;
  reject: (error: Error) => void;
}

export class SoproBrowserTts implements BrowserTts {
  status: "idle" | "loading" | "ready" | "error" = "idle";
  /** Model profile chosen for this device, e.g. "webgpu-fp16" or "wasm-uint8". */
  backend: string | null = null;
  errorMessage: string | null = null;

  private worker: Worker | null = null;
  private nextId = 1;
  private pending = new Map<number, PendingSynthesis>();
  private waitingForLoad: { resolve: () => void; reject: (error: Error) => void } | null = null;
  private waitingForReference: { resolve: (ms: number) => void; reject: (error: Error) => void } | null = null;

  /** Downloads the model (from Hugging Face unless `modelBaseUrl` points at a self-hosted copy). */
  load(modelBaseUrl: string | null, onProgress: (progress: LoadProgress) => void): Promise<void> {
    this.dispose();
    this.status = "loading";
    this.errorMessage = null;
    const worker = new Worker(new URL("../workers/soproWorker.ts", import.meta.url), { type: "module" });
    this.worker = worker;
    worker.onmessage = (event: MessageEvent<SoproOutbound>) => this.onMessage(event.data, onProgress);
    worker.onerror = (event) => this.fail(null, event.message || "Worker failed to start");
    return new Promise((resolve, reject) => {
      this.waitingForLoad = { resolve, reject };
      this.send({ type: "init", modelBaseUrl });
    });
  }

  /** Clones the voice of `clip`; resolves with how long preparing it took. */
  async setReference(clip: Blob): Promise<number> {
    // Decoded here because workers have no Web Audio.
    const audioContext = new AudioContext();
    try {
      const decoded = await audioContext.decodeAudioData(await clip.arrayBuffer());
      const mono = new Float32Array(decoded.length);
      for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
        const data = decoded.getChannelData(channel);
        for (let i = 0; i < mono.length; i++) mono[i] += data[i] / decoded.numberOfChannels;
      }
      return await new Promise((resolve, reject) => {
        this.waitingForReference = { resolve, reject };
        this.send({ type: "reference", pcm: mono, sampleRate: decoded.sampleRate }, [mono.buffer]);
      });
    } finally {
      void audioContext.close();
    }
  }

  /** Speaks `text`, handing each piece of audio to `onAudio` as soon as it's generated. */
  synthesize(
    text: string,
    language: string,
    onAudio: (samples: Float32Array<ArrayBuffer>, sampleRate: number) => void,
  ): Promise<BrowserSynthesis> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { started: performance.now(), firstAudioMs: null, onAudio, resolve, reject });
      this.send({ type: "synthesize", id, text, language: SOPRO_LANGUAGES.has(language) ? language : "auto" });
    });
  }

  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
    for (const request of this.pending.values()) request.reject(new Error("Sopro was shut down."));
    this.pending.clear();
    this.status = "idle";
  }

  private send(message: SoproInbound, transfer: Transferable[] = []) {
    this.worker?.postMessage(message, transfer);
  }

  private onMessage(message: SoproOutbound, onProgress: (progress: LoadProgress) => void) {
    switch (message.type) {
      case "progress":
        onProgress(message);
        break;
      case "ready":
        this.status = "ready";
        this.backend = message.backend;
        this.waitingForLoad?.resolve();
        this.waitingForLoad = null;
        break;
      case "referenceReady":
        this.waitingForReference?.resolve(message.prepareMs);
        this.waitingForReference = null;
        break;
      case "audio": {
        const request = this.pending.get(message.id);
        if (!request) break;
        request.firstAudioMs ??= performance.now() - request.started;
        request.onAudio(message.samples, message.sampleRate);
        break;
      }
      case "done": {
        const request = this.pending.get(message.id);
        this.pending.delete(message.id);
        request?.resolve({
          firstAudioMs: request.firstAudioMs ?? performance.now() - request.started,
          synthMs: message.synthMs,
          audioSeconds: message.audioSeconds,
        });
        break;
      }
      case "error":
        this.fail(message.id, message.message);
        break;
    }
  }

  private fail(id: number | null, message: string) {
    const error = new Error(message);
    if (id != null) {
      this.pending.get(id)?.reject(error);
      this.pending.delete(id);
      return;
    }
    this.status = this.status === "ready" ? "ready" : "error";
    this.errorMessage = message;
    this.waitingForLoad?.reject(error);
    this.waitingForLoad = null;
    this.waitingForReference?.reject(error);
    this.waitingForReference = null;
  }
}
