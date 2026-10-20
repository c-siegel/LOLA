// API client for the quality evaluation routes (Ragas): test sets per knowledge base, and runs
// that answer a test set through a project and score the answers. Only usable when the
// deployment runs the evaluation service — see evaluationApi.status and
// backend/app/features/evaluation/.

import { apiClient, downloadFile } from "./client";

export const METRICS = [
  "faithfulness",
  "answer_relevancy",
  "context_precision",
  "context_recall",
  "factual_correctness",
  "coverage",
  "restraint",
] as const;
export type MetricName = (typeof METRICS)[number];

// What a question tests (backend/app/features/evaluation/models.py): "grounded" – drafted from a
// passage of the material (retrieval); "topic" – asked about the subject without knowing the
// material (coverage, gaps); "offtopic" – outside the subject (does the avatar admit it).
export const QUESTION_KINDS = ["grounded", "topic", "offtopic"] as const;
export type QuestionKind = (typeof QUESTION_KINDS)[number];

export interface EvaluationStatus {
  available: boolean;
  reachable: boolean;
  ragasVersion: string | null;
  maxCasesPerRun: number | null;
  maxDraftsPerRequest: number | null;
  metrics: MetricName[];
  // Judge calls per question and metric (context_precision: per retrieved passage).
  judgeCallsPerMetric: Partial<Record<MetricName, number>>;
}

export interface TestSet {
  id: string;
  knowledgeBaseId: string;
  name: string;
  language: "de" | "en";
  caseCount: number;
  approvedCount: number;
  createdAt: string;
}

export interface TestCase {
  id: string;
  testSetId: string;
  question: string;
  reference: string | null;
  kind: QuestionKind;
  origin: "manual" | "csv" | "generated";
  // Drafted questions start unapproved; runs only use approved ones.
  approved: boolean;
  createdAt: string;
}

export type RunStatus = "queued" | "answering" | "scoring" | "done" | "failed" | "cancelled" | "interrupted";

export interface RunConfig {
  projectTitle: string | null;
  llm: string | null;
  temperature: number | null;
  knowledgeMode: string | null;
  topK: number | null;
  knowledgeBases: { name: string; embeddingModel: string | null }[];
  testSetName: string | null;
  testSetKnowledgeBase: string | null;
  language: string | null;
  judge: string | null;
  metrics: string[];
}

export interface MetricSummary {
  mean: number | null;
  median: number | null;
  count: number;
}

export interface Latency {
  p50: number | null;
  p90: number | null;
}

export interface RunSummary {
  metrics: Record<string, MetricSummary>;
  // The same per question kind, only for kinds the run has questions of.
  byKind: Partial<Record<QuestionKind, { count: number; metrics: Record<string, MetricSummary> }>>;
  retrievalMs: Latency;
  llmMs: Latency;
}

export interface Run {
  id: string;
  projectId: string;
  testSetId: string;
  status: RunStatus;
  errorCode: string | null;
  metrics: MetricName[];
  config: RunConfig;
  caseCount: number;
  answeredCount: number;
  scoredCount: number;
  summary: RunSummary | null;
  createdAt: string;
  finishedAt: string | null;
}

export interface MetricScore {
  value: number | null;
  // Why there's no value: NO_CONTEXTS, NO_REFERENCE, NO_EMBEDDING, NOT_SCORABLE, JUDGE_FAILED.
  error: string | null;
}

export interface RunItem {
  id: string;
  position: number;
  question: string;
  reference: string | null;
  kind: QuestionKind;
  answer: string | null;
  contexts: { text: string; filename: string | null; page: number | null; score: number | null }[];
  scores: Record<string, MetricScore>;
  retrievalMs: number | null;
  llmMs: number | null;
  errorCode: string | null;
}

export interface RunDetail extends Run {
  items: RunItem[];
}

export const ACTIVE_RUN_STATUSES: RunStatus[] = ["queued", "answering", "scoring"];

export const evaluationApi = {
  status: () => apiClient.get<EvaluationStatus>("/providers/evaluation-status"),

  testSets: (knowledgeBaseId: string) => apiClient.get<TestSet[]>(`/knowledge-bases/${knowledgeBaseId}/test-sets`),
  allTestSets: () => apiClient.get<TestSet[]>("/test-sets"),
  createTestSet: (knowledgeBaseId: string, name: string, language: "de" | "en") =>
    apiClient.post<TestSet>(`/knowledge-bases/${knowledgeBaseId}/test-sets`, { name, language }),
  updateTestSet: (id: string, data: { name?: string; language?: "de" | "en" }) =>
    apiClient.patch<TestSet>(`/test-sets/${id}`, data),
  removeTestSet: (id: string) => apiClient.delete<void>(`/test-sets/${id}`),

  cases: (testSetId: string) => apiClient.get<TestCase[]>(`/test-sets/${testSetId}/cases`),
  addCase: (testSetId: string, question: string, reference: string | null, kind: QuestionKind) =>
    apiClient.post<TestCase>(`/test-sets/${testSetId}/cases`, { question, reference, kind }),
  updateCase: (
    id: string,
    data: { question?: string; reference?: string | null; approved?: boolean; kind?: QuestionKind },
  ) =>
    apiClient.patch<TestCase>(`/test-cases/${id}`, data),
  removeCase: (id: string) => apiClient.delete<void>(`/test-cases/${id}`),
  importCsv: (testSetId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    return apiClient.upload<{ imported: number; skipped: number }>(`/test-sets/${testSetId}/cases/import`, formData);
  },
  exportCsv: (testSetId: string) => downloadFile(`/test-sets/${testSetId}/cases/export`, "test-set.csv"),
  generate: (
    testSetId: string,
    data: { judgeApiKeyId: string; size: number; kind: QuestionKind; projectId?: string; objectives?: string },
  ) => apiClient.post<TestCase[]>(`/test-sets/${testSetId}/generate`, data),
  discardDrafts: (testSetId: string) => apiClient.delete<void>(`/test-sets/${testSetId}/drafts`),

  runs: () => apiClient.get<Run[]>("/evaluation/runs"),
  run: (id: string) => apiClient.get<RunDetail>(`/evaluation/runs/${id}`),
  startRun: (data: { projectId: string; testSetId: string; judgeApiKeyId: string; metrics: MetricName[] }) =>
    apiClient.post<Run>("/evaluation/runs", data),
  cancelRun: (id: string) => apiClient.post<Run>(`/evaluation/runs/${id}/cancel`),
  removeRun: (id: string) => apiClient.delete<void>(`/evaluation/runs/${id}`),
  exportRun: (id: string, format: "csv" | "json") =>
    downloadFile(`/evaluation/runs/${id}/export?format=${format}`, `evaluation.${format}`),
};
