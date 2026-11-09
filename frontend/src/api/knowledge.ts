// API client for the knowledge-base (RAG) routes: a teacher's knowledge bases, their documents
// and a test search. Only usable when the deployment runs the knowledge service — see
// knowledgeApi.status and backend/app/features/knowledge/.

import { apiClient } from "./client";

export interface KnowledgeLimits {
  maxUploadMb: number;
  maxPages: number;
  maxDocumentsPerKb: number;
  maxKbPerUser: number;
  userQuotaMb: number;
}

export interface KnowledgeStatus {
  available: boolean;
  // The service can be configured but momentarily down — uploads then fail with a clear error.
  reachable: boolean;
  localModel: string | null;
  doclingAvailable: boolean;
  fileTypes: string[];
  limits: KnowledgeLimits | null;
  usageBytes: number;
  // Whether projects may use adaptive reasoning here: the knowledge service runs an embedding
  // model it is offered for (backend chat/reasoning_router.py::ADAPTIVE_EMBEDDING_MODELS).
  adaptiveReasoning: boolean;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  embeddingMode: "local" | "api";
  embeddingModel: string;
  embeddingApiKeyId: string | null;
  // False once the embedding key of an API-embedded knowledge base was deleted.
  embeddingAvailable: boolean;
  documentCount: number;
  sizeBytes: number;
  usedByProjects: number;
  createdAt: string;
}

export type KnowledgeDocumentStatus = "queued" | "processing" | "ready" | "failed";

export const SOURCE_TYPES = ["script", "worksheet", "article", "book", "thesis", "report", "web", "transcript", "other"] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];
export const SOURCE_PRIORITIES = ["primary", "secondary", "supplementary"] as const;
export type SourcePriority = (typeof SOURCE_PRIORITIES)[number];

// A document's source metadata (backend/app/features/knowledge/metadata.py). Only goes into the
// avatar's prompt, never into the search index, so editing it never re-indexes anything.
export interface SourceMetadata {
  title: string | null;
  author: string | null;
  year: number | null;
  container: string | null;
  url: string | null;
  citation: string | null;
  sourceType: SourceType | null;
  priority: SourcePriority | null;
  note: string | null;
  bibtexKey: string | null;
}

export interface BibEntry {
  key: string;
  title: string | null;
  author: string | null;
  year: number | null;
}

export interface BibImportResult {
  entries: number;
  skipped: number;
  linked: number;
  unlinked: string[];
}

export interface KnowledgeDocument {
  id: string;
  knowledgeBaseId: string;
  filename: string;
  fileType: string;
  sizeBytes: number;
  parser: "light" | "docling";
  status: KnowledgeDocumentStatus;
  // A KNOWLEDGE_* error code, translated via errors.<code>.
  errorCode: string | null;
  pageCount: number | null;
  chunkCount: number | null;
  truncated: boolean;
  // Failed, and the knowledge service still keeps the original (for a day): retry() works
  // without a re-upload.
  retryable: boolean;
  // Combined: the teacher's entries > the linked bibliography entry > the file's own header.
  // `cite` is what the avatar says when asked for the source.
  metadata: SourceMetadata & { cite: string | null };
  // Only what the teacher entered; the form shows the rest as inherited placeholders.
  ownMetadata: SourceMetadata;
  createdAt: string;
}

export interface KnowledgePassage {
  chunkId: number;
  documentId: string;
  filename: string | null;
  text: string;
  page: number | null;
  heading: string | null;
  score: number;
}

export const knowledgeApi = {
  status: () => apiClient.get<KnowledgeStatus>("/providers/rag-status"),
  list: () => apiClient.get<KnowledgeBase[]>("/knowledge-bases"),
  create: (name: string, description: string | null, embeddingApiKeyId: string | null) =>
    apiClient.post<KnowledgeBase>("/knowledge-bases", { name, description, embeddingApiKeyId }),
  update: (id: string, data: { name?: string; description?: string }) =>
    apiClient.patch<KnowledgeBase>(`/knowledge-bases/${id}`, data),
  remove: (id: string) => apiClient.delete<void>(`/knowledge-bases/${id}`),
  documents: (id: string) => apiClient.get<KnowledgeDocument[]>(`/knowledge-bases/${id}/documents`),
  // `consent`: the teacher confirms they may make this material available to everyone with the
  // project's link — the backend refuses the upload without it.
  upload: (id: string, file: File, parser: "light" | "docling", consent: boolean) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("parser", parser);
    formData.append("consent", String(consent));
    return apiClient.upload<KnowledgeDocument>(`/knowledge-bases/${id}/documents`, formData);
  },
  retry: (documentId: string, parser: "light" | "docling") =>
    apiClient.post<KnowledgeDocument>(`/knowledge-documents/${documentId}/retry`, { parser }),
  removeDocument: (documentId: string) => apiClient.delete<void>(`/knowledge-documents/${documentId}`),
  updateMetadata: (documentId: string, metadata: Partial<SourceMetadata>) =>
    apiClient.patch<KnowledgeDocument>(`/knowledge-documents/${documentId}/metadata`, metadata),
  bibliography: (id: string) => apiClient.get<BibEntry[]>(`/knowledge-bases/${id}/bibliography`),
  importBibliography: (id: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    return apiClient.upload<BibImportResult>(`/knowledge-bases/${id}/bibliography`, formData);
  },
  removeBibliography: (id: string) => apiClient.delete<void>(`/knowledge-bases/${id}/bibliography`),
  search: (id: string, query: string) => apiClient.post<KnowledgePassage[]>(`/knowledge-bases/${id}/search`, { query }),
};
