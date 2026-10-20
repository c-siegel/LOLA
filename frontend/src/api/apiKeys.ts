import { apiClient } from "./client";
import type { ApiKey, ApiKeyInput, ProviderSpec } from "@/types/apiKey";
import type { SttServerEngine } from "@/types/project";

/** The local TTS sidecar (backend providers_router.py::local_tts_status). */
export interface LocalTtsStatus {
  available: boolean;
  /** The sidecar answered; the fields below are empty when it didn't. */
  reachable: boolean;
  /** Teachers' clips and donated voices are cloned; otherwise only presets speak. */
  cloning: boolean;
  languages: string[];
  voices: LocalTtsVoice[];
}

export interface LocalTtsVoice {
  id: string;
  label: string;
  kind: "preset" | "donation";
}

export const apiKeysApi = {
  listProviders: () => apiClient.get<ProviderSpec[]>("/providers"),
  localTtsStatus: () => apiClient.get<LocalTtsStatus>("/providers/local-tts-status"),
  browserSttStatus: () => apiClient.get<{ available: boolean }>("/providers/browser-stt-status"),
  serverSttStatus: () =>
    apiClient.get<{ defaultEngine: SttServerEngine; parakeetAvailable: boolean }>("/providers/server-stt-status"),
  list: () => apiClient.get<ApiKey[]>("/api-keys"),
  create: (input: ApiKeyInput) => apiClient.post<ApiKey>("/api-keys", input),
  // Keys are addressed by their id (no longer by provider) — the same teacher can store several
  // keys for the same provider.
  update: (id: string, input: ApiKeyInput) => apiClient.put<ApiKey>(`/api-keys/${id}`, input),
  remove: (id: string) => apiClient.delete<void>(`/api-keys/${id}`),
  test: (id: string) => apiClient.post<{ status: string; message: string | null }>(`/api-keys/${id}/test`),
};
