// Pocket TTS in the browser, for the public chat (projects with on-device speech output) and the
// latency test page (pages/Dashboard/LatencyLab): the main-thread side of workers/pocketWorker.ts. German only, with Kyutai's predefined voice "alba" — no voice
// cloning (that part of the model isn't shipped).

import type { PocketInbound, PocketOutbound } from "@/workers/pocketWorker";
import type { BrowserSynthesis, BrowserTts } from "@/lib/browserTts";
import type { LoadProgress } from "@/lib/parakeetStt";

interface PendingSynthesis {
  started: number;
  firstAudioMs: number | null;
  onAudio: (samples: Float32Array<ArrayBuffer>, sampleRate: number) => void;
  resolve: (result: BrowserSynthesis) => void;
  reject: (error: Error) => void;
}

export class PocketBrowserTts implements BrowserTts {
  status: "idle" | "loading" | "ready" | "error" = "idle";
  /** e.g. "wasm-int8 ×1" (threads). */
  backend: string | null = null;
  voice: string | null = null;
  voiceCredit: string | null = null;
  errorMessage: string | null = null;

  private worker: Worker | null = null;
  private nextId = 1;
  private pending = new Map<number, PendingSynthesis>();
  private waitingForLoad: { resolve: () => void; reject: (error: Error) => void } | null = null;

  /** Downloads the self-hosted model (scripts/fetch-tts-model.sh) from `modelUrl`. */
  load(modelUrl: string, onProgress: (progress: LoadProgress) => void): Promise<void> {
    this.dispose();
    this.status = "loading";
    this.errorMessage = null;
    const worker = new Worker(new URL("../workers/pocketWorker.ts", import.meta.url), { type: "module" });
    this.worker = worker;
    worker.onmessage = (event: MessageEvent<PocketOutbound>) => this.onMessage(event.data, onProgress);
    worker.onerror = (event) => this.fail(null, event.message || "Worker failed to start");
    return new Promise((resolve, reject) => {
      this.waitingForLoad = { resolve, reject };
      // A worker has no document to resolve "/models/…" against (see parakeetStt.ts).
      this.send({ type: "init", modelUrl: new URL(modelUrl, window.location.href).href });
    });
  }

  /** `language` is ignored: this model only speaks German. */
  synthesize(
    text: string,
    _language: string,
    onAudio: (samples: Float32Array<ArrayBuffer>, sampleRate: number) => void,
    signal?: AbortSignal,
  ): Promise<BrowserSynthesis> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { started: performance.now(), firstAudioMs: null, onAudio, resolve, reject });
      this.send({ type: "synthesize", id, text });
      // The worker stops generating and still answers "done", which resolves this promise.
      signal?.addEventListener("abort", () => this.send({ type: "cancel", id }), { once: true });
    });
  }

  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
    for (const request of this.pending.values()) request.reject(new Error("Pocket TTS was shut down."));
    this.pending.clear();
    this.status = "idle";
  }

  private send(message: PocketInbound) {
    this.worker?.postMessage(message);
  }

  private onMessage(message: PocketOutbound, onProgress: (progress: LoadProgress) => void) {
    switch (message.type) {
      case "progress":
        onProgress(message);
        break;
      case "ready":
        this.status = "ready";
        this.backend = message.backend;
        this.voice = message.voice;
        this.voiceCredit = message.credit;
        this.waitingForLoad?.resolve();
        this.waitingForLoad = null;
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
  }
}
