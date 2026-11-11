// Voice activity detection (VAD) in the browser: tells when the student starts and stops speaking
// and, for hands-free voice input, when their turn is over. Silero VAD and Smart Turn run in
// workers/vadWorker.ts; this file is the main-thread side, fed from the microphone through the same
// AudioWorklet as on-device speech recognition (lib/parakeetStt.ts). Used by the public chat's voice
// input (pages/PublicChat/useVoiceInput.ts).
//
// The models come from scripts/fetch-stt-model.sh (self-hosted under /models/: Silero ~2 MB, Smart
// Turn ~8 MB). Both are small and run on the CPU, so unlike Parakeet they work on every device.

import { sitePath } from "@/lib/basePath";
import type { TurnConfig, TurnEndReason, VadWorkerInbound, VadWorkerOutbound } from "@/workers/vadWorker";
import pcmCaptureWorkletUrl from "@/workers/pcmCapture.worklet.ts?worker&url";

export type { TurnEndReason } from "@/workers/vadWorker";

export const VAD_MODEL_URL = sitePath("/models/silero-vad/v6.2/");
export const TURN_MODEL_URL = sitePath("/models/smart-turn/v3.2/");

/** Hands-free defaults (docs/stt-plan.md, phase 3): a 300 ms pause asks Smart Turn; an unfinished
 * turn ends after 2 s of silence at most. Fixed values for now, to be tuned with the Latency Lab. */
export const HANDS_FREE_PAUSE_MS = 300;
export const HANDS_FREE_TURN: TurnConfig = { maxSilenceMs: 2000, fallbackSilenceMs: 1000, slowPredictMs: 600 };

export type VadStatus = "idle" | "loading" | "ready" | "error";

export interface TurnEnd {
  /** When the last speech ended and when the turn was declared over, in ms since listening
   * (re)started. */
  speechEndMs: number;
  atMs: number;
  reason: TurnEndReason;
  /** Smart Turn's probability that the turn is finished, if it decided. */
  probability: number | null;
}

export interface VadHandlers {
  onSpeechStart?: (atMs: number) => void;
  /** `atMs`: when the speech ended (ms of audio since listening started), not when the pause was
   * confirmed `minSilenceMs` later. */
  onSpeechEnd?: (atMs: number) => void;
  /** Hands-free only: the student's turn is over. Nothing more is reported until resume(). */
  onTurnEnd?: (turn: TurnEnd) => void;
}

/** Listening on one microphone stream. Created by SileroVad.listen. */
export class VadSession {
  private stopped = false;

  constructor(
    private readonly audioContext: AudioContext,
    private readonly captureNode: AudioWorkletNode,
    private readonly source: MediaStreamAudioSourceNode,
    private readonly sendStart: () => void,
    private readonly sendPause: () => void,
    private readonly release: () => void,
    private readonly closeContext: boolean,
  ) {}

  /** Stops analysing (the microphone stays open), e.g. while the avatar answers. */
  pause(): void {
    if (!this.stopped) this.sendPause();
  }

  /** Starts over with fresh model state, as for a new recording or turn. */
  resume(): void {
    if (!this.stopped) this.sendStart();
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.source.disconnect();
    this.captureNode.disconnect();
    if (this.closeContext) void this.audioContext.close();
    this.sendPause();
    this.release();
  }
}

/** Loads Silero VAD (and, if asked, Smart Turn) in a Web Worker. One instance per page visit; one
 * listening session at a time. */
export class SileroVad {
  status: VadStatus = "idle";
  /** Whether Smart Turn loaded; without it, hands-free ends a turn after a fixed silence. */
  turnDetection = false;
  private worker: Worker | null = null;
  private readyPromise: Promise<void> | null = null;
  private handlers: VadHandlers = {};

  /** `withTurnDetection`: also load Smart Turn (only hands-free projects need it). */
  constructor(
    readonly withTurnDetection = false,
    readonly modelUrl: string = VAD_MODEL_URL,
    readonly turnModelUrl: string = TURN_MODEL_URL,
  ) {}

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
            this.turnDetection = message.turnDetection;
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
          case "turnEnd":
            this.handlers.onTurnEnd?.(message);
            break;
          case "error":
            // This recording just stops getting events; the next one tries again.
            console.error("Voice activity detection failed:", message.message);
            break;
        }
      };
      worker.onerror = (event) => {
        this.fail(event.message || "Worker failed to start");
        resolve();
      };
      const absolute = (url: string) => new URL(url, window.location.href).href;
      this.send({
        type: "init",
        modelUrl: absolute(this.modelUrl),
        turnModelUrl: this.withTurnDetection ? absolute(this.turnModelUrl) : null,
      });
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
   * suspended; the session closes it when it stops, unless `closeContext` is false. `turn`
   * switches on hands-free turn detection (onTurnEnd). */
  async listen(
    stream: MediaStream,
    audioContext: AudioContext,
    handlers: VadHandlers,
    minSilenceMs: number,
    turn: TurnConfig | null = null,
    closeContext = true,
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
    const start = () => this.send({ type: "start", minSilenceMs, turn });
    start();
    // A late call on an earlier session must not affect a newer one.
    const isCurrent = () => this.handlers === handlers;
    return new VadSession(
      audioContext,
      captureNode,
      source,
      () => isCurrent() && start(),
      () => isCurrent() && this.send({ type: "stop" }),
      () => {
        if (isCurrent()) this.handlers = {};
      },
      closeContext,
    );
  }

  /** Releases the worker — call on page unmount, not between recordings. */
  dispose(): void {
    this.worker?.terminate();
    this.worker = null;
    this.handlers = {};
  }
}
