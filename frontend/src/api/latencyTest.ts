// API client for the dashboard's latency test (backend features/chat/latency_router.py): the
// owner's own project chat with switchable LLM key, TTS path and streaming, answered as
// server-sent events that carry the server-side timing of every step. Nothing is saved.

import { API_BASE_URL, ApiError, apiClient } from "./client";
import { parseSseFrame } from "./publicChat";
import type { ChatMessage } from "@/types/chat";

export type ServerTtsMode = "project" | "local" | "none";
export type ServerSttEngine = "project" | "whisper" | "parakeet";

export interface LatencyMessageRequest {
  message: string;
  history: ChatMessage[];
  llmApiKeyId: string | null;
  ttsMode: ServerTtsMode;
  streaming: boolean;
  /** "auto": the project's mode and rules decide; the others force the route. */
  route: "auto" | "fast" | "deep";
}

/** Server-side times, each in ms since the backend received the request. */
export interface LatencyChunkEvent {
  index: number;
  /** The bridge phrase said while a reasoning reply is thought out (index -1, not part of the reply). */
  bridge?: boolean;
  text: string;
  audioBase64: string | null;
  contentType: string | null;
  textReadyMs: number;
  /** This chunk's synthesis time; null without server TTS. */
  ttsMs: number | null;
  sentMs: number;
}

export interface LatencyDoneEvent {
  reply: string;
  llmMs: number;
  /** null for a non-streamed reply. */
  llmFirstTokenMs: number | null;
  firstChunkMs: number | null;
  firstChunkTextReadyMs: number | null;
  ttsMs: number | null;
  /** Knowledge-base lookup before the LLM call; null when the project uses no knowledge base. */
  retrievalMs?: number | null;
  /** How the turn was routed: "deep" asked the model to reason, "fast" switched reasoning off (or a budget or
   * timeout fell back to that), "default" sent nothing. */
  route?: "default" | "fast" | "deep" | null;
  /** The language model's token counts; each is null when the provider doesn't report it. */
  usage?: {
    promptTokens: number | null;
    completionTokens: number | null;
    cachedTokens: number | null;
    reasoningTokens: number | null;
  };
}

export interface LatencyTranscription {
  text: string;
  sttMs: number;
  engine: string;
}

/** Sends one test message; resolves with the "done" event, rejects on an "error" event. */
export async function sendLatencyMessage(
  projectId: string,
  request: LatencyMessageRequest,
  onChunk: (chunk: LatencyChunkEvent) => void,
  signal?: AbortSignal,
): Promise<LatencyDoneEvent> {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/latency-test/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  if (!res.body) throw new Error("Streamed reply has no body.");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separatorAt: number;
    while ((separatorAt = buffer.indexOf("\n\n")) !== -1) {
      const parsed = parseSseFrame(buffer.slice(0, separatorAt));
      buffer = buffer.slice(separatorAt + 2);
      if (!parsed) continue;
      if (parsed.event === "chunk") onChunk(parsed.data as LatencyChunkEvent);
      else if (parsed.event === "done") return parsed.data as LatencyDoneEvent;
      else if (parsed.event === "error") {
        // Same shape as an HTTP error's body, so errorMessage() translates the code.
        const { detail, message } = parsed.data as { detail: string; message?: string };
        throw new ApiError(502, JSON.stringify({ detail: { code: detail, message } }));
      }
    }
  }
  throw new Error("The reply stream ended without a result.");
}

export const latencyTestApi = {
  transcribe: (projectId: string, audio: Blob, filename: string, engine: ServerSttEngine) => {
    const formData = new FormData();
    formData.append("audio", audio, filename);
    formData.append("engine", engine);
    return apiClient.upload<LatencyTranscription>(`/projects/${projectId}/latency-test/transcriptions`, formData);
  },
};
