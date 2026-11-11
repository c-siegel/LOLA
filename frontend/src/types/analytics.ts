import type { ChatMessage } from "./chat";

export interface AnalyticsStats {
  sessions: number;
  sessionsDeltaPct: number;
  messages: number;
  messagesDeltaPct: number;
  avgDurationSeconds: number;
  avgDurationDeltaPct: number;
  tokenCostEur: number;
  tokenCostDeltaPct: number;
  // Replies of projects that route between a fast and a deep model, by route.
  fastReplies: number;
  deepReplies: number;
}

export interface SessionRow {
  id: string;
  projectTitle: string;
  startedAt: string;
  messageCount: number;
  durationSeconds: number;
  lastQuestion: string | null;
  // The name/ID the visitor typed in, if the project asked for one (see
  // types/project.ts::Project.requireVisitorName) — null for every session that didn't ask.
  visitorName: string | null;
}

export interface SessionsPage {
  items: SessionRow[];
  total: number;
}

export interface SessionDetail {
  id: string;
  projectTitle: string;
  visitorName: string | null;
  startedAt: string;
  // Full message-by-message transcript — what GET /conversations/{id} returns, unlike
  // SessionRow's truncated lastQuestion. Reused from types/chat.ts since the shape matches
  // exactly (role "user"/"assistant", never "system" — that's a PublicChat-only local notice).
  messages: TranscriptMessage[];
}

// A knowledge-base passage a reply was given (see backend features/knowledge/) — the document
// and page only; filename is null once the document was deleted.
export interface TranscriptSource {
  documentId: string;
  filename: string | null;
  page: number | null;
}

// An avatar gesture the LLM marked in a reply (see backend features/chat/motion.py): its name and
// the character position in the message content where the marker stood.
export interface TranscriptMotion {
  name: string;
  offset: number;
}

// A conversation-plan step the LLM ticked off (done) or took back in a reply (see backend
// features/chat/checklist.py), resolved to the step's current number and title — both null once
// the step has been deleted from the plan.
export interface TranscriptCheck {
  id: string;
  done: boolean;
  offset: number;
  number: number | null;
  title: string | null;
}

export type TranscriptMessage = Omit<ChatMessage, "checks"> & {
  sources?: TranscriptSource[] | null;
  motions?: TranscriptMotion[] | null;
  checks?: TranscriptCheck[] | null;
  // Reasoning projects only: the route ("fast" or "deep") and the model that answered.
  route?: string | null;
  model?: string | null;
};

export type Granularity = "day" | "week" | "month";

export interface TimeseriesPoint {
  label: string;
  value: number;
}

export interface AnalyticsFilters {
  projectId: string | null;
  periodDays: number;
  model: string | null;
}

// Body shared by POST /conversations/export and POST /conversations/batch-delete — which checked-off
// conversations (see pages/Dashboard/Analytics) to bundle into a CSV/ZIP download or delete.
export interface ConversationIdsRequest {
  conversationIds: string[];
}
