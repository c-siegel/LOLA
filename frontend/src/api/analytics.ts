import { apiClient, downloadFile } from "./client";
import type {
  AnalyticsFilters,
  AnalyticsStats,
  ConversationIdsRequest,
  Granularity,
  SessionDetail,
  SessionsPage,
  TimeseriesPoint,
} from "@/types/analytics";

function filterParams(filters: AnalyticsFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.projectId) params.set("project_id", filters.projectId);
  params.set("period_days", String(filters.periodDays));
  if (filters.model) params.set("model", filters.model);
  return params;
}

export const analyticsApi = {
  stats: (filters: AnalyticsFilters) => apiClient.get<AnalyticsStats>(`/analytics/stats?${filterParams(filters)}`),

  // { items, total } instead of a bare list, so "1–4 of 1,284" is real pagination.
  sessions: (filters: AnalyticsFilters, page: number) => {
    const params = filterParams(filters);
    params.set("page", String(page));
    return apiClient.get<SessionsPage>(`/conversations?${params}`);
  },

  // All conversation IDs matching the filter, regardless of page — for the "Select all" button,
  // see pages/Dashboard/Analytics/index.tsx.
  sessionIds: (filters: AnalyticsFilters) => apiClient.get<string[]>(`/conversations/ids?${filterParams(filters)}`),

  // Full transcript for one session's "view" action (see pages/Dashboard/Analytics/index.tsx) —
  // separate from `sessions` above since the table's paginated list stays a lightweight summary.
  sessionDetail: (id: string) => apiClient.get<SessionDetail>(`/conversations/${id}`),

  timeseries: (filters: AnalyticsFilters, granularity: Granularity) => {
    const params = filterParams(filters);
    params.set("granularity", granularity);
    return apiClient.get<TimeseriesPoint[]>(`/analytics/timeseries?${params}`);
  },

  // Downloads a .csv (one selected conversation) or .zip (several) directly as a file, instead of
  // parsing it as JSON like the other endpoints.
  exportConversations: (conversationIds: string[]) => {
    const body: ConversationIdsRequest = { conversationIds };
    return downloadFile("/conversations/export", "lola-transcripts.zip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  // Permanently deletes the checked-off conversations — ids that don't belong to the current
  // user are silently skipped server-side (see app/features/analytics/conversations_router.py::delete_conversations_route).
  deleteConversations: (conversationIds: string[]) => {
    const body: ConversationIdsRequest = { conversationIds };
    return apiClient.post<void>("/conversations/batch-delete", body);
  },
};
