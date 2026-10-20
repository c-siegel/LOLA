// Runs one simulated student turn through the chosen configuration and times every step: speech
// input → transcript → the backend's reply (latency_router.py) → speech output on this device.
// The page (index.tsx) owns the engines and the avatar; this only orchestrates and measures.

import { errorMessage } from "@/api/client";
import { latencyTestApi, sendLatencyMessage, type LatencyChunkEvent, type ServerTtsMode } from "@/api/latencyTest";
import type { TalkingHeadAvatarHandle } from "@/components/TalkingHeadAvatar";
import type { BrowserTts } from "@/lib/browserTts";
import type { ParakeetSttEngine } from "@/lib/parakeetStt";
import type { ChatMessage } from "@/types/chat";
import { isBrowserTtsMode, type InputKind, type LabConfig, type TurnResult } from "./metrics";

export interface Clip {
  id: string;
  name: string;
  blob: Blob;
}

export type TurnInput =
  | { kind: "text"; text: string }
  | { kind: "clip"; clip: Clip }
  /** A live recording on an already-open microphone; `stopped` resolves when the user stops. */
  | { kind: "mic"; stream: MediaStream; stopped: Promise<void> };

export interface TurnContext {
  projectId: string;
  language: string;
  config: LabConfig;
  configLabel: string;
  llmModel: string;
  /** The avatar, or the audio-only speaker — same handle either way. */
  speaker: TalkingHeadAvatarHandle;
  /** Created in the click that started the run, kept for all its turns (iPadOS needs that). */
  inputContext: AudioContext;
  parakeet: ParakeetSttEngine | null;
  /** The engine for the selected browser TTS mode; null for server modes. */
  browserTts: BrowserTts | null;
  history: ChatMessage[];
  signal: AbortSignal;
  /** Live transcript while the student "speaks", for the page to show. */
  onPartial: (text: string) => void;
  /** The reply text as it streams in. */
  onReplyText: (text: string) => void;
}

interface SpeechResult {
  transcript: string;
  t0: number;
  audioSeconds: number;
  sttFirstPartialMs: number | null;
  sttMs: number;
  sttServerMs: number | null;
  sttEngine: string;
}

// The backend accepts only these exact types; browsers label the same files differently.
const UPLOAD_TYPES: Record<string, string> = {
  "audio/x-wav": "audio/wav",
  "audio/wave": "audio/wav",
  "audio/mp3": "audio/mpeg",
  "audio/x-m4a": "audio/mp4",
  "audio/m4a": "audio/mp4",
  "audio/aac": "audio/mp4",
};

function uploadable(blob: Blob): { blob: Blob; filename: string } {
  const base = blob.type.split(";")[0].trim();
  const type = UPLOAD_TYPES[base] ?? (base || "audio/webm");
  const extension = { "audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg" }[type] ?? "webm";
  return { blob: new Blob([blob], { type }), filename: `recording.${extension}` };
}

/** Plays a clip through the speakers at real-time speed; resolves when it has finished, with a
 * MediaStream of the same audio for the recognizer. */
async function playClip(context: AudioContext, clip: Clip) {
  const buffer = await context.decodeAudioData(await clip.blob.arrayBuffer());
  const source = context.createBufferSource();
  source.buffer = buffer;
  const destination = context.createMediaStreamDestination();
  source.connect(destination);
  source.connect(context.destination);
  const ended = new Promise<void>((resolve) => (source.onended = () => resolve()));
  return {
    stream: destination.stream,
    duration: buffer.duration,
    start: () => source.start(),
    ended,
    stop: () => {
      try {
        source.stop();
      } catch {
        // Already stopped.
      }
    },
  };
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    promise.then(resolve, reject);
  });
}

/** Speech input on the device: the recognizer listens while the student speaks. */
async function speakOnDevice(ctx: TurnContext, input: Exclude<TurnInput, { kind: "text" }>): Promise<SpeechResult> {
  const parakeet = ctx.parakeet;
  if (parakeet?.status !== "ready") throw new Error(`On-device speech recognition is ${parakeet?.status ?? "off"}.`);
  const clip = input.kind === "clip" ? await playClip(ctx.inputContext, input.clip) : null;
  const stream = clip ? clip.stream : (input as { stream: MediaStream }).stream;
  let firstPartialAt: number | null = null;
  const session = await parakeet.startStreaming(
    stream,
    ctx.inputContext,
    (text) => {
      if (text) firstPartialAt ??= performance.now();
      ctx.onPartial(text);
    },
    false,
  );
  const speechStart = performance.now();
  try {
    clip?.start();
    await abortable(clip ? clip.ended : (input as { stopped: Promise<void> }).stopped, ctx.signal);
  } catch (error) {
    clip?.stop();
    await session.stop().catch(() => undefined);
    throw error;
  }
  const t0 = performance.now();
  const { text } = await session.stop();
  return {
    transcript: text,
    t0,
    audioSeconds: clip ? clip.duration : (t0 - speechStart) / 1000,
    sttFirstPartialMs: firstPartialAt != null ? firstPartialAt - speechStart : null,
    sttMs: performance.now() - t0,
    sttServerMs: null,
    sttEngine: "parakeet-browser",
  };
}

/** Speech input transcribed on the server after the student stops, like the public chat's
 * fallback. */
async function speakToServer(ctx: TurnContext, input: Exclude<TurnInput, { kind: "text" }>): Promise<SpeechResult> {
  const engine = ctx.config.sttMode === "server-whisper" ? "whisper" : ctx.config.sttMode === "server-parakeet" ? "parakeet" : "project";
  let audio: Blob;
  let audioSeconds: number;
  let t0: number;
  if (input.kind === "clip") {
    const clip = await playClip(ctx.inputContext, input.clip);
    clip.start();
    try {
      await abortable(clip.ended, ctx.signal);
    } catch (error) {
      clip.stop();
      throw error;
    }
    t0 = performance.now();
    audio = input.clip.blob;
    audioSeconds = clip.duration;
  } else {
    const recorder = new MediaRecorder(input.stream);
    const parts: Blob[] = [];
    recorder.ondataavailable = (event) => parts.push(event.data);
    const finished = new Promise<void>((resolve) => (recorder.onstop = () => resolve()));
    const speechStart = performance.now();
    recorder.start();
    try {
      await abortable(input.stopped, ctx.signal);
    } finally {
      recorder.stop();
    }
    t0 = performance.now();
    await finished;
    audio = new Blob(parts, { type: recorder.mimeType });
    audioSeconds = (t0 - speechStart) / 1000;
  }
  const upload = uploadable(audio);
  const result = await latencyTestApi.transcribe(ctx.projectId, upload.blob, upload.filename, engine);
  ctx.onPartial(result.text);
  return {
    transcript: result.text,
    t0,
    audioSeconds,
    sttFirstPartialMs: null,
    sttMs: performance.now() - t0,
    sttServerMs: result.sttMs,
    sttEngine: result.engine,
  };
}

/** Runs one turn and returns its measurements; never throws (errors land in `error`). */
export async function runTurn(ctx: TurnContext, input: TurnInput, runId: string, turn: number): Promise<TurnResult> {
  const result: TurnResult = {
    runId,
    turn,
    startedAt: new Date().toISOString(),
    config: ctx.configLabel,
    llmModel: ctx.llmModel,
    inputKind: input.kind as InputKind,
    input: input.kind === "text" ? input.text : input.kind === "clip" ? input.clip.name : "microphone",
    inputAudioSeconds: null,
    transcript: null,
    sttFirstPartialMs: null,
    sttMs: null,
    sttServerMs: null,
    sttEngine: null,
    retrievalMs: null,
    llmFirstTokenMs: null,
    llmTotalMs: null,
    firstChunkTextReadyMs: null,
    firstChunkTtsMs: null,
    ttsTotalMs: null,
    requestToFirstChunkMs: null,
    firstAudioMs: null,
    replyCompleteMs: null,
    playbackEndMs: null,
    playbackGapMs: null,
    chunks: 0,
    replyChars: 0,
    avatarFps: null,
    droppedFrames: null,
    contextLosses: 0,
    error: null,
  };
  const onContextLost = () => result.contextLosses++;
  document.addEventListener("webglcontextlost", onContextLost, true);

  try {
    // ---- 1. Student input → text ----
    let message: string;
    let t0: number;
    if (input.kind === "text") {
      message = input.text;
      t0 = performance.now();
    } else {
      const speech = ctx.config.sttMode === "device" ? await speakOnDevice(ctx, input) : await speakToServer(ctx, input);
      message = speech.transcript;
      t0 = speech.t0;
      Object.assign(result, {
        transcript: speech.transcript,
        inputAudioSeconds: speech.audioSeconds,
        sttFirstPartialMs: speech.sttFirstPartialMs,
        sttMs: speech.sttMs,
        sttServerMs: speech.sttServerMs,
        sttEngine: speech.sttEngine,
      });
      if (!message.trim()) throw new Error("The transcript is empty.");
    }

    // ---- 2. Reply from the backend, 3. speech output on this device ----
    const browserTts = isBrowserTtsMode(ctx.config.ttsMode);
    if (browserTts && !ctx.browserTts?.backend) throw new Error("The browser TTS engine is not loaded.");
    const requestAt = performance.now();
    let firstChunkAt: number | null = null;
    let firstAudioAt: number | null = null;
    let queueEnd = 0;
    let gapMs = 0;
    let browserTtsMs = 0;
    let replyText = "";
    // Chunks are decoded/synthesized strictly in order, so they also play in order.
    let speech: Promise<void> = Promise.resolve();

    // `pieces`: the buffer continues the previous one mid-word (browser TTS), so it must follow it
    // without TalkingHead's gap between speech-queue items.
    const play = (buffer: AudioBuffer, pieces = false) => {
      const now = performance.now();
      if (firstAudioAt == null) {
        firstAudioAt = now;
        ctx.speaker.startFpsTracking();
      } else if (now > queueEnd) {
        // The previous audio ran out before this piece was ready: the student hears a pause.
        gapMs += now - queueEnd;
      }
      queueEnd = Math.max(now, queueEnd) + buffer.duration * 1000;
      if (pieces) ctx.speaker.queueBuffer(buffer);
      else ctx.speaker.speakBuffer(buffer);
    };

    const onChunk = (chunk: LatencyChunkEvent) => {
      firstChunkAt ??= performance.now();
      result.chunks++;
      if (chunk.index === 0 && !browserTts) result.firstChunkTtsMs = chunk.ttsMs;
      replyText = replyText ? `${replyText} ${chunk.text}` : chunk.text;
      ctx.onReplyText(replyText);
      const { audioBase64 } = chunk;
      if (audioBase64) {
        speech = speech.then(async () => play(await ctx.speaker.decodeAudio(audioBase64)));
      } else if (browserTts) {
        speech = speech.then(async () => {
          const synthesis = await ctx.browserTts!.synthesize(chunk.text, ctx.language, (samples, sampleRate) => {
            const buffer = new AudioBuffer({ length: samples.length, numberOfChannels: 1, sampleRate });
            buffer.copyToChannel(samples, 0);
            play(buffer, true);
          });
          if (chunk.index === 0) result.firstChunkTtsMs = synthesis.firstAudioMs;
          browserTtsMs += synthesis.synthMs;
        });
      }
    };

    const done = await sendLatencyMessage(
      ctx.projectId,
      {
        message,
        history: ctx.history,
        llmApiKeyId: ctx.config.llmKeyId,
        // Browser TTS: the server sends text only, this device speaks it.
        ttsMode: browserTts ? "none" : (ctx.config.ttsMode as ServerTtsMode),
        streaming: ctx.config.streaming,
      },
      onChunk,
      ctx.signal,
    );
    result.replyCompleteMs = performance.now() - t0;
    result.retrievalMs = done.retrievalMs ?? null;
    result.llmFirstTokenMs = done.llmFirstTokenMs;
    result.llmTotalMs = done.llmMs;
    result.firstChunkTextReadyMs = done.firstChunkTextReadyMs;
    result.ttsTotalMs = done.ttsMs;
    result.replyChars = done.reply.length;
    if (firstChunkAt != null) result.requestToFirstChunkMs = firstChunkAt - requestAt;
    ctx.history.push({ role: "user", content: message }, { role: "assistant", content: done.reply });

    await abortable(speech, ctx.signal);
    if (browserTts) result.ttsTotalMs = browserTtsMs;
    await ctx.speaker.waitUntilIdle(ctx.signal);
    if (firstAudioAt != null) {
      result.firstAudioMs = firstAudioAt - t0;
      result.playbackEndMs = performance.now() - t0;
      result.playbackGapMs = gapMs;
      const fps = ctx.speaker.stopFpsTracking();
      result.avatarFps = fps?.avgFps ?? null;
      result.droppedFrames = fps?.droppedFrames ?? null;
    }
  } catch (error) {
    ctx.speaker.stopSpeaking();
    ctx.speaker.stopFpsTracking();
    result.error =
      error instanceof DOMException && error.name === "AbortError"
        ? "aborted"
        : errorMessage(error, error instanceof Error ? error.message : String(error)) + rawDetail(error);
  } finally {
    document.removeEventListener("webglcontextlost", onContextLost, true);
  }
  return result;
}

/** The provider's own message from an API error, if there is one — the teacher is debugging. */
function rawDetail(error: unknown): string {
  try {
    const detail = JSON.parse((error as Error).message).detail;
    return typeof detail?.message === "string" ? ` (${detail.message})` : "";
  } catch {
    return "";
  }
}
