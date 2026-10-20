// API client for the /pronunciation/* backend routes: a teacher's own word list of terms the
// avatar should pronounce differently ("pH" -> "p H"), per spoken language, plus preset packs,
// text import/export and the test box.

import { apiClient, downloadFile } from "./client";
import type { SpokenLanguage } from "@/types/project";

export interface PronunciationEntry {
  id: string;
  language: SpokenLanguage;
  term: string;
  spoken: string;
  wholeWord: boolean;
  caseSensitive: boolean;
  // The preset pack this entry was copied from — null once the teacher edited it.
  sourcePack: string | null;
  createdAt: string;
}

export interface PronunciationEntryInput {
  term: string;
  // May stay empty with spellOut — the backend generates it from the term then.
  spoken: string;
  wholeWord: boolean;
  caseSensitive: boolean;
  spellOut: boolean;
}

export interface PresetPack {
  id: string;
  language: SpokenLanguage;
  version: number;
  entries: { term: string; spoken: string; wholeWord: boolean; caseSensitive: boolean }[];
  // How many of the teacher's entries currently come from this pack (0: not applied).
  appliedCount: number;
}

export interface ImportResult {
  added: number;
  updated: number;
  skipped: number;
  errors: { line: number; content: string; error: string }[];
}

export interface PreviewInput {
  text: string;
  language: SpokenLanguage;
  synthesize: boolean;
  // null: the local TTS service (optionally cloning voiceClipId).
  ttsApiKeyId: string | null;
  ttsVoice: string | null;
  voiceClipId: string | null;
}

export interface PreviewResult {
  spokenText: string;
  appliedTerms: string[];
  audioBase64: string | null;
  contentType: string | null;
}

export const pronunciationApi = {
  list: (language: SpokenLanguage) =>
    apiClient.get<PronunciationEntry[]>(`/pronunciation/entries?language=${language}`),
  create: (language: SpokenLanguage, input: PronunciationEntryInput) =>
    apiClient.post<PronunciationEntry>("/pronunciation/entries", { language, ...input }),
  update: (id: string, input: PronunciationEntryInput) =>
    apiClient.put<PronunciationEntry>(`/pronunciation/entries/${id}`, input),
  remove: (id: string) => apiClient.delete<void>(`/pronunciation/entries/${id}`),
  presets: (language: SpokenLanguage) => apiClient.get<PresetPack[]>(`/pronunciation/presets?language=${language}`),
  applyPreset: (id: string, language: SpokenLanguage) =>
    apiClient.post<{ added: number; skipped: number }>(`/pronunciation/presets/${id}/apply?language=${language}`),
  removePreset: (id: string, language: SpokenLanguage) =>
    apiClient.delete<{ removed: number }>(`/pronunciation/presets/${id}?language=${language}`),
  import: (language: SpokenLanguage, text: string, overwrite: boolean, dryRun: boolean) =>
    apiClient.post<ImportResult>("/pronunciation/import", { language, text, overwrite, dryRun }),
  preview: (input: PreviewInput) => apiClient.post<PreviewResult>("/pronunciation/preview", input),
  exportCsv: (language: SpokenLanguage) =>
    downloadFile(`/pronunciation/export?language=${language}`, `pronunciation-${language}.csv`),
};
