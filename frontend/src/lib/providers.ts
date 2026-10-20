import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiKeysApi } from "@/api/apiKeys";
import i18n from "@/i18n";
import { evaluationApi } from "@/api/evaluation";
import { knowledgeApi } from "@/api/knowledge";
import type { ApiKey, ApiKeyType, ProviderModel, ProviderSpec } from "@/types/apiKey";

// Providers, endpoint defaults and curated models come from the backend registry
// (backend/app/core/providers.py) instead of a second list here — otherwise the frontend could
// offer providers or models that have no call path on the server.
export function useProviders() {
  const { t, i18n } = useTranslation();
  // The registry's labels and hints are German; translated here by provider id (providers.<id>
  // in the locales), with the backend's own text as fallback for a provider added there first.
  const select = useCallback(
    (specs: ProviderSpec[]) =>
      specs.map((spec) => ({
        ...spec,
        label: t(`providers.${spec.value}.label`, { defaultValue: spec.label }),
        hint: spec.hint && t(`providers.${spec.value}.hint`, { defaultValue: spec.hint }),
      })),
    // i18n.language: re-translate on a language switch.
    [t, i18n.language],
  );
  return useQuery({
    queryKey: ["api-key-providers"],
    queryFn: apiKeysApi.listProviders,
    // This reference data only changes with a deployment.
    staleTime: Infinity,
    select,
  });
}

/** Whether this deployment can fall back to local TTS synthesis when a project has no TTS key selected. */
export function useLocalTtsStatus() {
  return useQuery({
    queryKey: ["local-tts-status"],
    queryFn: apiKeysApi.localTtsStatus,
    // Whether it's enabled only changes with a redeploy, but cloning and the voices also change
    // when the sidecar restarts (the backend caches them for a minute too).
    staleTime: 60_000,
  });
}

/** Whether this deployment allows browser-side (WebGPU) transcription at all — gates the
 * Configurator's per-project "on-device transcription" checkbox (see Step2Technical.tsx). */
export function useBrowserSttStatus() {
  return useQuery({
    queryKey: ["browser-stt-status"],
    queryFn: apiKeysApi.browserSttStatus,
    staleTime: Infinity,
  });
}

/** Whether this deployment offers on-device speech output, and the one language its model speaks. */
export function useBrowserTtsStatus() {
  return useQuery({
    queryKey: ["browser-tts-status"],
    queryFn: apiKeysApi.browserTtsStatus,
    staleTime: Infinity,
  });
}

/** The deployment's default server STT engine and whether Parakeet's model files are present —
 * drives the Configurator's server-engine choice (see Step2Technical.tsx). */
export function useServerSttStatus() {
  return useQuery({
    queryKey: ["server-stt-status"],
    queryFn: apiKeysApi.serverSttStatus,
    staleTime: Infinity,
  });
}

/** Whether this deployment runs the knowledge service, plus its upload limits — gates the
 * Knowledge page, its nav item and the Configurator's knowledge section. */
export function useKnowledgeStatus() {
  return useQuery({
    queryKey: ["rag-status"],
    queryFn: knowledgeApi.status,
    // Includes the teacher's usage, so not Infinity like the other deployment facts above.
    staleTime: 60_000,
  });
}

/** A provider's curated models for one key type: embedding models for embedding keys, its
 * chat models otherwise. */
export function curatedModels(spec: ProviderSpec | undefined, keyType: ApiKeyType): ProviderModel[] {
  if (!spec) return [];
  return keyType === "embedding" ? spec.embeddingModels ?? [] : spec.models;
}

export function findProvider(specs: ProviderSpec[], value: string): ProviderSpec | undefined {
  return specs.find((spec) => spec.value === value);
}

export function providerLabel(specs: ProviderSpec[], value: string): string {
  return findProvider(specs, value)?.label ?? value;
}

/** Display name of a key: the teacher's own name for it, otherwise the provider label. */
export function keyDisplayName(key: ApiKey, specs: ProviderSpec[]): string {
  return key.label?.trim() || providerLabel(specs, key.provider);
}

/** Display name of the stored model — the curated title, otherwise the entered model ID. */
export function modelLabel(key: ApiKey, specs: ProviderSpec[]): string | null {
  const spec = findProvider(specs, key.provider);
  if (!key.modelId) {
    // A provider with a fixed model (TTS: OpenAI/Gemini; STT: GWDG SAIA) deliberately stores no
    // model_id (see ApiKeyForm.tsx) — not "no model", just no choice needed.
    const modelFixed = (key.keyType === "tts" && spec?.ttsModelFixed) || (key.keyType === "stt" && spec?.sttModelFixed);
    return modelFixed ? i18n.t("apiKeyForm.defaultModel") : null;
  }
  return curatedModels(spec, key.keyType).find((model) => model.value === key.modelId)?.label ?? key.modelId;
}

/** Whether this deployment runs the evaluation (Ragas) service — gates the Evaluation page, its
 * nav item and the test sets on the Knowledge page. */
export function useEvaluationStatus(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["evaluation-status"],
    queryFn: evaluationApi.status,
    staleTime: 60_000,
    enabled: options.enabled ?? true,
  });
}

/** Keys that can judge answers: LLM keys with a model, except Arcana (it adds its own retrieval
 * to every call, see backend/app/features/evaluation/service.py::judge_for). */
export function isJudgeKey(key: ApiKey): boolean {
  return key.keyType === "llm" && Boolean(key.modelId) && key.provider !== "gwdg_arcana";
}
