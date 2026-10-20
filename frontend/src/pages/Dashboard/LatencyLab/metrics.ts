// What the latency test records per turn, and how it's summarized and exported. Every client
// time is in ms since the end of the student's input ("t0": the recording stopped, the clip
// ended, or the text was sent) — the moment from which a student waits.

export type SttMode = "device" | "server-project" | "server-whisper" | "server-parakeet";
/** "browser-pocket": Pocket TTS in this browser. */
export type TtsMode = "project" | "local" | "browser-pocket" | "none";

/** Modes in which this device speaks the reply (the server sends text only). */
export function isBrowserTtsMode(mode: TtsMode): boolean {
  return mode === "browser-pocket";
}

export interface LabConfig {
  sttMode: SttMode;
  /** Another of the teacher's LLM keys; null: the project's own. */
  llmKeyId: string | null;
  streaming: boolean;
  ttsMode: TtsMode;
  avatar: boolean;
}

export type InputKind = "text" | "clip" | "mic";

export interface TurnResult {
  runId: string;
  turn: number;
  startedAt: string;
  /** Human-readable configuration, the key results are grouped by. */
  config: string;
  llmModel: string;
  inputKind: InputKind;
  input: string;
  /** Length of the spoken input (clip or recording). */
  inputAudioSeconds: number | null;
  transcript: string | null;

  /** On-device only: from the start of speaking until the first live text. */
  sttFirstPartialMs: number | null;
  /** t0 → final transcript (device: finalizing; server: upload + transcription + response). */
  sttMs: number | null;
  /** Server STT only: time inside the backend's transcription. */
  sttServerMs: number | null;
  sttEngine: string | null;

  /** Server: knowledge-base lookup before the LLM call (null without a knowledge base). */
  retrievalMs: number | null;
  /** Server-side, since the backend received the message. */
  llmFirstTokenMs: number | null;
  llmTotalMs: number | null;
  firstChunkTextReadyMs: number | null;
  /** Synthesis time of the first chunk: server TTS, or the browser engine until first audio. */
  firstChunkTtsMs: number | null;
  /** Summed synthesis time (server TTS, or the browser engine's generation time). */
  ttsTotalMs: number | null;

  /** Client: request sent → first chunk received. */
  requestToFirstChunkMs: number | null;
  /** t0 → the avatar starts speaking. The number a student feels. */
  firstAudioMs: number | null;
  /** t0 → the whole reply (text) has arrived. */
  replyCompleteMs: number | null;
  /** t0 → the avatar has finished speaking. */
  playbackEndMs: number | null;
  /** Silence between chunks because the next one wasn't ready in time. */
  playbackGapMs: number | null;
  chunks: number;
  replyChars: number;

  avatarFps: number | null;
  droppedFrames: number | null;
  contextLosses: number;
  error: string | null;
}

/** The numeric columns, in table order, with their i18n label keys. */
export const METRIC_COLUMNS = [
  "sttFirstPartialMs",
  "sttMs",
  "sttServerMs",
  "retrievalMs",
  "llmFirstTokenMs",
  "llmTotalMs",
  "firstChunkTextReadyMs",
  "firstChunkTtsMs",
  "ttsTotalMs",
  "requestToFirstChunkMs",
  "firstAudioMs",
  "replyCompleteMs",
  "playbackEndMs",
  "playbackGapMs",
  "avatarFps",
] as const satisfies readonly (keyof TurnResult)[];

export type MetricColumn = (typeof METRIC_COLUMNS)[number];

/** Nearest-rank percentile of the non-null values; null if there are none. */
export function percentile(values: (number | null)[], p: number): number | null {
  const sorted = values.filter((v): v is number => v != null && Number.isFinite(v)).sort((a, b) => a - b);
  if (!sorted.length) return null;
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))];
}

export interface SummaryRow {
  config: string;
  turns: number;
  errors: number;
  median: Record<MetricColumn, number | null>;
  p90: Record<MetricColumn, number | null>;
}

/** Median and 90th percentile of every metric, per configuration. */
export function summarize(results: TurnResult[]): SummaryRow[] {
  const groups = new Map<string, TurnResult[]>();
  for (const result of results) groups.set(result.config, [...(groups.get(result.config) ?? []), result]);
  return [...groups.entries()].map(([config, rows]) => {
    const ok = rows.filter((row) => !row.error);
    const stat = (p: number) =>
      Object.fromEntries(METRIC_COLUMNS.map((column) => [column, percentile(ok.map((row) => row[column]), p)])) as Record<
        MetricColumn,
        number | null
      >;
    return { config, turns: rows.length, errors: rows.length - ok.length, median: stat(50), p90: stat(90) };
  });
}

export interface DeviceInfo {
  userAgent: string;
  webGpu: boolean;
  deviceMemoryGb: number | null;
  cpuCores: number | null;
  pixelRatio: number;
  screen: string;
}

const csvCell = (value: unknown) => {
  if (value == null) return "";
  const text = typeof value === "number" ? String(Math.round(value * 10) / 10) : String(value);
  return /[",\n;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

/** One row per turn, with the device in every row so files from several devices can be merged. */
export function toCsv(results: TurnResult[], device: DeviceInfo): string {
  if (!results.length) return "";
  const deviceColumns = Object.keys(device) as (keyof DeviceInfo)[];
  const resultColumns = Object.keys(results[0]) as (keyof TurnResult)[];
  const header = [...resultColumns, ...deviceColumns.map((c) => `device_${c}`)];
  const lines = results.map((row) =>
    [...resultColumns.map((c) => csvCell(row[c])), ...deviceColumns.map((c) => csvCell(device[c]))].join(","),
  );
  return [header.join(","), ...lines].join("\n");
}

