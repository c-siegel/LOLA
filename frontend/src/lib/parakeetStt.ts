// On-device speech-to-text (STT) with the Parakeet Redux model: downloads the model once, then
// turns live microphone audio into growing partial text while the student speaks. The model runs
// in workers/parakeetWorker.ts; this file is the main-thread side. Used by
// pages/PublicChat/index.tsx (which falls back to the backend's /transcriptions route whenever
// this isn't "ready") and by the latency test (pages/Dashboard/LatencyLab).

import type { WorkerInbound, WorkerOutbound } from "@/workers/parakeetWorker";
import pcmCaptureWorkletUrl from "@/workers/pcmCapture.worklet.ts?worker&url";

export type ParakeetStatus = "idle" | "loading" | "ready" | "unsupported" | "error";

export interface LoadProgress {
  loadedBytes: number;
  totalBytes: number;
}

export interface DecodeStats {
  decodeMs: number;
  audioSeconds: number;
}

// navigator.gpu has no ambient TypeScript type in this project (no @webgpu/types dependency) —
// narrowly typed here just enough to call requestAdapter().
interface NavigatorWithGpu extends Navigator {
  gpu?: { requestAdapter: () => Promise<unknown> };
}

/** Whether this browser can actually run a WebGPU model, not just whether the API exists — some
 * browsers expose navigator.gpu but fail to produce a real adapter (disabled flag, no
 * compatible hardware). */
export async function isWebGpuAvailable(): Promise<boolean> {
  const gpu = (navigator as NavigatorWithGpu).gpu;
  if (!gpu) return false;
  try {
    return (await gpu.requestAdapter()) != null;
  } catch {
    return false;
  }
}

/** One recording. Created by ParakeetSttEngine.startStreaming. */
export class StreamingSession {
  private finished = false;

  constructor(
    private readonly audioContext: AudioContext,
    private readonly captureNode: AudioWorkletNode,
    private readonly source: MediaStreamAudioSourceNode,
    private readonly finalText: Promise<{ text: string; finalizeMs: number }>,
    private readonly sendStop: () => void,
    private readonly closeContext: boolean,
  ) {}

  /** Stops capturing and resolves with the final transcript once the last audio is decoded.
   * Rejects if the model failed during this recording. */
  async stop(): Promise<{ text: string; finalizeMs: number }> {
    if (!this.finished) {
      this.finished = true;
      // Flush the worklet's half-filled batch first, so the last ~100 ms of speech isn't lost.
      await new Promise<void>((resolve) => {
        const timeout = setTimeout(resolve, 300);
        this.captureNode.port.addEventListener("message", (event) => {
          if (event.data === "flushed") {
            clearTimeout(timeout);
            resolve();
          }
        });
        this.captureNode.port.postMessage("flush");
      });
      this.source.disconnect();
      this.captureNode.disconnect();
      if (this.closeContext) void this.audioContext.close();
      this.sendStop();
    }
    return this.finalText;
  }
}

/** Loads Parakeet Redux in a Web Worker and streams microphone audio into it. One instance per
 * chat page visit — loading the model is the expensive part. */
export class ParakeetSttEngine {
  status: ParakeetStatus = "idle";
  /** Why loading failed, for the device test page and the console. */
  errorMessage: string | null = null;
  /** Called after every re-decode, for the device test page's latency numbers. */
  onStats: ((stats: DecodeStats) => void) | null = null;

  private worker: Worker | null = null;
  private readyPromise: Promise<void> | null = null;
  private onPartial: ((text: string) => void) | null = null;
  private pendingFinal: { resolve: (r: { text: string; finalizeMs: number }) => void; reject: (e: Error) => void } | null =
    null;
  private streamFailed: Error | null = null;

  /** `modelUrl` is the base URL of the model files (see backend Settings.browser_stt_model_url). */
  constructor(readonly modelUrl: string) {}

  /** Starts loading the model if it hasn't started yet (safe to call repeatedly); resolves once
   * `status` has settled to "ready", "unsupported", or "error" — never rejects. */
  ensureReady(onProgress?: (progress: LoadProgress) => void): Promise<void> {
    this.readyPromise ??= this.init(onProgress);
    return this.readyPromise;
  }

  private async init(onProgress?: (progress: LoadProgress) => void): Promise<void> {
    if (!(await isWebGpuAvailable())) {
      this.status = "unsupported";
      return;
    }
    this.status = "loading";
    // Asks the browser not to evict the cached model under storage pressure. Window-only API,
    // hence here instead of in the worker; a refusal is fine (it just may download again).
    void navigator.storage?.persist?.().catch(() => undefined);
    return new Promise((resolve) => {
      const worker = new Worker(new URL("../workers/parakeetWorker.ts", import.meta.url), { type: "module" });
      this.worker = worker;
      worker.onmessage = (event: MessageEvent<WorkerOutbound>) => {
        const message = event.data;
        switch (message.type) {
          case "progress":
            onProgress?.(message);
            break;
          case "ready":
            this.status = "ready";
            resolve();
            break;
          case "initError":
            this.fail(message.message);
            resolve();
            break;
          case "partial":
            this.onPartial?.(message.text);
            break;
          case "stats":
            this.onStats?.(message);
            break;
          case "final":
            if (this.streamFailed) this.pendingFinal?.reject(this.streamFailed);
            else this.pendingFinal?.resolve({ text: message.text, finalizeMs: message.finalizeMs });
            this.pendingFinal = null;
            break;
          case "streamError":
            // The model broke mid-recording (e.g. the GPU device was lost). This recording is
            // gone; every later one goes through the server instead.
            this.streamFailed = new Error(message.message);
            this.fail(message.message);
            this.pendingFinal?.reject(this.streamFailed);
            this.pendingFinal = null;
            break;
        }
      };
      worker.onerror = (event) => {
        this.fail(event.message || "Worker failed to start");
        resolve();
      };
      const absoluteUrl = new URL(this.modelUrl, window.location.href).href;
      this.send({ type: "init", modelUrl: absoluteUrl });
    });
  }

  private fail(message: string) {
    console.error("On-device speech recognition unavailable:", message);
    this.status = "error";
    this.errorMessage = message;
  }

  private send(message: WorkerInbound, transfer: Transferable[] = []) {
    this.worker?.postMessage(message, transfer);
  }

  /** Starts a recording on an already-open microphone stream. `audioContext` should be created
   * inside the click handler (before awaiting getUserMedia), or Safari starts it suspended.
   * `onPartial` receives the whole transcript so far each time it changes. The session closes
   * `audioContext` when it stops, unless `closeContext` is false (the latency test page keeps one
   * context for a whole scripted run, since iPadOS only starts audio from a click). */
  async startStreaming(
    stream: MediaStream,
    audioContext: AudioContext,
    onPartial: (text: string) => void,
    closeContext = true,
  ): Promise<StreamingSession> {
    if (this.status !== "ready") throw new Error(`On-device speech recognition is not ready (${this.status}).`);
    await audioContext.resume();
    await audioContext.audioWorklet.addModule(pcmCaptureWorkletUrl);
    const source = audioContext.createMediaStreamSource(stream);
    const captureNode = new AudioWorkletNode(audioContext, "pcm-capture");
    const sampleRate = audioContext.sampleRate;
    captureNode.port.onmessage = (event: MessageEvent<Float32Array | string>) => {
      if (typeof event.data === "string") return;
      this.send({ type: "audio", samples: event.data, sampleRate }, [event.data.buffer]);
    };
    source.connect(captureNode);
    // Some browsers only run a worklet that's connected through to the speakers. It writes no
    // output, so this plays silence.
    captureNode.connect(audioContext.destination);

    this.onPartial = onPartial;
    this.streamFailed = null;
    const finalText = new Promise<{ text: string; finalizeMs: number }>((resolve, reject) => {
      this.pendingFinal = { resolve, reject };
    });
    // A rejection here is handled by whoever awaits stop(); this only avoids an "unhandled
    // rejection" warning if the model fails before stop() is called.
    finalText.catch(() => undefined);
    this.send({ type: "start" });
    return new StreamingSession(
      audioContext,
      captureNode,
      source,
      finalText,
      () => this.send({ type: "stop" }),
      closeContext,
    );
  }

  /** Releases the worker — call on page unmount, not between recordings. */
  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
    this.pendingFinal?.reject(new Error("On-device speech recognition was shut down."));
    this.pendingFinal = null;
  }
}
