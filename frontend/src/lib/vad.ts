// Voice activity detection (VAD) in the browser: tells when the student starts and stops speaking.
// Silero VAD runs in workers/vadWorker.ts; this file is the main-thread side, fed from the
// microphone through the same AudioWorklet as on-device speech recognition (lib/parakeetStt.ts).
// Used by the public chat's server fallback (pages/PublicChat/useVoiceInput.ts) to cut a
// recording at real speech pauses.
//
// The model comes from scripts/fetch-stt-model.sh (self-hosted under /models/, ~2 MB). It's tiny
// and runs on the CPU, so unlike Parakeet it works on every device.

import { sitePath } from "@/lib/basePath";
import type { VadWorkerInbound, VadWorkerOutbound } from "@/workers/vadWorker";
import pcmCaptureWorkletUrl from "@/workers/pcmCapture.worklet.ts?worker&url";

export const VAD_MODEL_URL = sitePath("/models/silero-vad/v6.2/");

export type VadStatus = "idle" | "loading" | "ready" | "error";

export interface VadHandlers {
  onSpeechStart?: (atMs: number) => void;
  /** `atMs`: when the speech ended (ms of audio since listening started), not when the pause was
   * confirmed `minSilenceMs` later. */
  onSpeechEnd?: (atMs: number) => void;
}

/** Listening on one microphone stream. Created by SileroVad.listen. */
export class VadSession {
  private stopped = false;

  constructor(
    private readonly audioContext: AudioContext,
    private readonly captureNode: AudioWorkletNode,
    private readonly source: MediaStreamAudioSourceNode,
    private readonly sendStop: () => void,
  ) {}

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.source.disconnect();
    this.captureNode.disconnect();
    void this.audioContext.close();
    this.sendStop();
  }
}

/** Loads Silero VAD in a Web Worker. One instance per page visit; one recording at a time. */
export class SileroVad {
  status: VadStatus = "idle";
  private worker: Worker | null = null;
  private readyPromise: Promise<void> | null = null;
  private handlers: VadHandlers = {};

  constructor(readonly modelUrl: string = VAD_MODEL_URL) {}

  /** Starts loading if it hasn't yet (safe to call repeatedly); resolves once `status` is
   * "ready" or "error" — never rejects. */
  ensureReady(): Promise<void> {
    this.readyPromise ??= this.init();
    return this.readyPromise;
  }

  private init(): Promise<void> {
    this.status = "loading";
    return new Promise((resolve) => {
      const worker = new Worker(new URL("../workers/vadWorker.ts", import.meta.url), { type: "module" });
      this.worker = worker;
      worker.onmessage = (event: MessageEvent<VadWorkerOutbound>) => {
        const message = event.data;
        switch (message.type) {
          case "ready":
            this.status = "ready";
            resolve();
            break;
          case "initError":
            this.fail(message.message);
            resolve();
            break;
          case "speechStart":
            this.handlers.onSpeechStart?.(message.atMs);
            break;
          case "speechEnd":
            this.handlers.onSpeechEnd?.(message.atMs);
            break;
          case "error":
            // This recording just stops getting pause events; the next one tries again.
            console.error("Voice activity detection failed:", message.message);
            break;
        }
      };
      worker.onerror = (event) => {
        this.fail(event.message || "Worker failed to start");
        resolve();
      };
      this.send({ type: "init", modelUrl: new URL(this.modelUrl, window.location.href).href });
    });
  }

  private fail(message: string) {
    console.error("Voice activity detection unavailable:", message);
    this.status = "error";
  }

  private send(message: VadWorkerInbound, transfer: Transferable[] = []) {
    this.worker?.postMessage(message, transfer);
  }

  /** Starts detecting speech on an already-open microphone stream. `audioContext` should be
   * created inside the click handler (before awaiting getUserMedia), or Safari starts it
   * suspended; the session closes it when it stops. */
  async listen(
    stream: MediaStream,
    audioContext: AudioContext,
    handlers: VadHandlers,
    minSilenceMs: number,
  ): Promise<VadSession> {
    if (this.status !== "ready") throw new Error(`Voice activity detection is not ready (${this.status}).`);
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
    this.handlers = handlers;
    this.send({ type: "start", minSilenceMs });
    return new VadSession(audioContext, captureNode, source, () => {
      // A late stop of an earlier recording must not cut off a newer one.
      if (this.handlers !== handlers) return;
      this.handlers = {};
      this.send({ type: "stop" });
    });
  }

  /** Releases the worker — call on page unmount, not between recordings. */
  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
    this.handlers = {};
  }
}
