import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Download, ChevronLeft, ChevronRight, Eye, Trash2, X } from "lucide-react";
import { Button } from "@/components/Button";
import { Tile } from "@/components/Tile";
import { Callout } from "@/components/Callout";
import { BarChart } from "@/components/Chart";
import { ChatBubble } from "@/components/ChatBubble";
import { Scrim } from "@/components/Drawer";
import { analyticsApi } from "@/api/analytics";
import { projectsApi } from "@/api/projects";
import { formatCompactNumber, formatDuration, formatEuro, numberLocale } from "@/lib/format";
import type { AnalyticsFilters, Granularity, TranscriptMotion } from "@/types/analytics";
import styles from "./Analytics.module.css";

// How many words after a gesture's position identify where in the reply it happened.
const MOTION_CONTEXT_WORDS = 4;

// "nod_yes (before „Genau, das ist…“)" — where in the reply a gesture was used, readable without
// counting characters. The raw offset is in the CSV export.
function describeMotion(motion: TranscriptMotion, content: string, t: TFunction): string {
  const rest = content.slice(motion.offset).trim();
  if (motion.offset === 0) return `${motion.name} (${t("analytics.transcript.motionAtStart")})`;
  if (!rest) return `${motion.name} (${t("analytics.transcript.motionAtEnd")})`;
  const words = rest.split(/\s+/);
  const excerpt = words.slice(0, MOTION_CONTEXT_WORDS).join(" ") + (words.length > MOTION_CONTEXT_WORDS ? "…" : "");
  return `${motion.name} (${t("analytics.transcript.motionBefore", { text: excerpt })})`;
}

const PERIOD_OPTIONS = [
  { value: 7, days: 7 },
  { value: 30, days: 30 },
  { value: 90, days: 90 },
];

const PAGE_SIZE = 4;

const DEFAULT_FILTERS: AnalyticsFilters = { projectId: null, periodDays: 30, model: null };

// Screen 1f — analytics tab
export function AnalyticsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // "Apply filter" in the wireframe implies two states: what's currently in the dropdowns (draft)
  // and what actually drives the queries (applied) — only the button transfers one to the other.
  const [draftFilters, setDraftFilters] = useState(DEFAULT_FILTERS);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [granularity, setGranularity] = useState<Granularity>("week");
  const [page, setPage] = useState(1);
  // Conversation ids checked off in the table, for the bulk CSV/ZIP download below — a Set so it
  // stays cheap to check/toggle, and survives paging (see analyticsApi.sessionIds/"Select all").
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // Which session's full transcript the "view" action is currently showing — null = overlay closed.
  const [viewingSessionId, setViewingSessionId] = useState<string | null>(null);

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const statsQuery = useQuery({ queryKey: ["analytics", "stats", filters], queryFn: () => analyticsApi.stats(filters) });
  const timeseriesQuery = useQuery({
    queryKey: ["analytics", "timeseries", filters, granularity],
    queryFn: () => analyticsApi.timeseries(filters, granularity),
  });
  const sessionsQuery = useQuery({
    queryKey: ["analytics", "sessions", filters, page],
    queryFn: () => analyticsApi.sessions(filters, page),
  });
  const sessionDetailQuery = useQuery({
    queryKey: ["analytics", "session-detail", viewingSessionId],
    queryFn: () => analyticsApi.sessionDetail(viewingSessionId!),
    enabled: viewingSessionId !== null,
  });

  // "Select all" pulls every id matching the current filter (not just the loaded page) — see
  // analyticsApi.sessionIds.
  const selectAllMutation = useMutation({
    mutationFn: () => analyticsApi.sessionIds(filters),
    onSuccess: (ids) => setSelectedIds(new Set(ids)),
  });
  const downloadMutation = useMutation({
    mutationFn: (ids: string[]) => analyticsApi.exportConversations(ids),
  });
  const deleteMutation = useMutation({
    mutationFn: (ids: string[]) => analyticsApi.deleteConversations(ids),
    onSuccess: () => {
      // Covers the sessions table itself plus the stats/timeseries cards above it (deleting
      // conversations changes their counts too) — and the transcript overlay's own query, if
      // the conversation currently open in it was among those just deleted (see its isError
      // branch below for how that shows up).
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
      setSelectedIds(new Set());
    },
  });

  const stats = statsQuery.data;
  // Only offer models that are actually used in projects — the filter compares exactly against
  // Project.llm_model on the server, so a fixed list would also contain options without any match.
  const usedModels = [...new Set((projectsQuery.data ?? []).map((p) => p.llmModel).filter(Boolean))].sort();
  const sessions = sessionsQuery.data?.items ?? [];
  const totalSessions = sessionsQuery.data?.total ?? 0;
  const timeseries = timeseriesQuery.data ?? [];
  // Proxy for "every matching conversation is checked", without re-fetching the id list just to
  // compare — good enough since ids only ever enter selectedIds through sessionIds or the
  // per-row checkboxes below, both scoped to the current filter.
  const allMatchingSelected = totalSessions > 0 && selectedIds.size === totalSessions;

  function applyFilters() {
    setFilters(draftFilters);
    setPage(1);
    setSelectedIds(new Set());
  }

  function toggleRowSelected(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (allMatchingSelected) {
      setSelectedIds(new Set());
    } else {
      selectAllMutation.mutate();
    }
  }

  function handleDownload() {
    downloadMutation.mutate([...selectedIds]);
  }

  function handleDeleteSelected() {
    // Deliberate second step, like deleting a whole project (see ProjectRow) — this cannot be
    // undone.
    if (window.confirm(t("analytics.selection.deleteConfirm", { count: selectedIds.size }))) {
      deleteMutation.mutate([...selectedIds]);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.topbar}>
        <h2>{t("nav.analytics")}</h2>
      </div>

      {downloadMutation.isError && <Callout variant="danger">{t("analytics.exportError")}</Callout>}
      {deleteMutation.isError && <Callout variant="danger">{t("analytics.selection.deleteError")}</Callout>}

      <div className={styles.filterRow}>
        <select
          className={styles.select}
          value={draftFilters.projectId ?? ""}
          onChange={(e) => setDraftFilters((f) => ({ ...f, projectId: e.target.value || null }))}
        >
          <option value="">{t("analytics.filters.projectAll")}</option>
          {(projectsQuery.data ?? []).map((project) => (
            <option key={project.id} value={project.id}>
              {project.title}
            </option>
          ))}
        </select>
        <select
          className={styles.select}
          value={draftFilters.periodDays}
          onChange={(e) => setDraftFilters((f) => ({ ...f, periodDays: Number(e.target.value) }))}
        >
          {PERIOD_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {t("analytics.filters.period", { days: option.days })}
            </option>
          ))}
        </select>
        <select
          className={styles.select}
          value={draftFilters.model ?? ""}
          onChange={(e) => setDraftFilters((f) => ({ ...f, model: e.target.value || null }))}
        >
          <option value="">{t("analytics.filters.modelAll")}</option>
          {usedModels.map((model) => (
            <option key={model} value={model!}>
              {model}
            </option>
          ))}
        </select>
        <Button variant="accent" size="sm" onClick={applyFilters}>
          {t("analytics.filters.apply")}
        </Button>
      </div>

      <div className={styles.statGrid}>
        <Tile
          label={t("analytics.stats.sessions")}
          value={stats ? formatCompactNumber(stats.sessions) : "–"}
          delta={stats && { value: stats.sessionsDeltaPct, goodDirection: "up" }}
        />
        <Tile
          label={t("analytics.stats.messages")}
          value={stats ? formatCompactNumber(stats.messages) : "–"}
          delta={stats && { value: stats.messagesDeltaPct, goodDirection: "up" }}
        />
        <Tile
          label={t("analytics.stats.avgDuration")}
          value={stats ? formatDuration(stats.avgDurationSeconds) : "–"}
          delta={stats && { value: stats.avgDurationDeltaPct, goodDirection: "up" }}
        />
        <Tile
          label={t("analytics.stats.tokenCost")}
          value={stats ? formatEuro(stats.tokenCostEur) : "–"}
          delta={stats && { value: stats.tokenCostDeltaPct, goodDirection: "down" }}
        />
        {stats && stats.fastReplies + stats.deepReplies > 0 && (
          <Tile
            label={t("analytics.stats.deepReplies")}
            value={`${formatCompactNumber(stats.deepReplies)} / ${formatCompactNumber(stats.fastReplies + stats.deepReplies)}`}
          />
        )}
      </div>

      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h3>{t("analytics.sessionsOverTime")}</h3>
          <div className={styles.granularityToggle}>
            {(["day", "week", "month"] as Granularity[]).map((g) => (
              <button
                key={g}
                className={`${styles.granularityButton} ${granularity === g ? styles.granularityActive : ""}`}
                onClick={() => setGranularity(g)}
              >
                {t(`analytics.granularity.${g}`)}
              </button>
            ))}
          </div>
        </div>
        <BarChart data={timeseries} isFetching={timeseriesQuery.isFetching} />
      </div>

      <div className={styles.card}>
        <div className={styles.selectionBar}>
          <span className={styles.selectionCount}>
            {selectedIds.size === 0
              ? t("analytics.selection.none")
              : t("analytics.selection.count", { count: selectedIds.size })}
          </span>
          <div className={styles.selectionActions}>
            <Button size="sm" onClick={toggleSelectAll} disabled={selectAllMutation.isPending || totalSessions === 0}>
              {allMatchingSelected ? t("analytics.selection.clear") : t("analytics.selection.selectAll")}
            </Button>
            <Button
              size="sm"
              variant="accent"
              onClick={handleDownload}
              disabled={selectedIds.size === 0 || downloadMutation.isPending}
            >
              <Download size={14} /> {t("analytics.selection.download")}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={handleDeleteSelected}
              disabled={selectedIds.size === 0 || deleteMutation.isPending}
            >
              <Trash2 size={14} /> {t("analytics.selection.delete")}
            </Button>
          </div>
        </div>

        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.checkboxColumn} />
              <th>{t("analytics.table.project")}</th>
              <th>{t("analytics.table.visitorName")}</th>
              <th>{t("analytics.table.started")}</th>
              <th>{t("analytics.table.messagesAbbr")}</th>
              <th>{t("analytics.table.duration")}</th>
              <th>{t("analytics.table.lastQuestion")}</th>
              <th className={styles.viewColumn} />
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 ? (
              <tr className={styles.emptyRow}>
                <td colSpan={8}>{t("analytics.table.empty")}</td>
              </tr>
            ) : (
              sessions.map((session) => (
                <tr key={session.id}>
                  <td className={styles.checkboxColumn}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(session.id)}
                      onChange={() => toggleRowSelected(session.id)}
                      aria-label={t("analytics.table.selectRow")}
                    />
                  </td>
                  <td>{session.projectTitle}</td>
                  <td>{session.visitorName ?? "–"}</td>
                  <td>{new Date(session.startedAt).toLocaleString(numberLocale())}</td>
                  <td>{session.messageCount}</td>
                  <td>{formatDuration(session.durationSeconds)}</td>
                  <td>{session.lastQuestion ?? "–"}</td>
                  <td className={styles.viewColumn}>
                    <Button size="sm" onClick={() => setViewingSessionId(session.id)}>
                      <Eye size={14} /> {t("analytics.table.view")}
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        <div className={styles.sessionCards}>
          {sessions.length === 0 ? (
            <p className={styles.emptyRow}>{t("analytics.table.empty")}</p>
          ) : (
            sessions.map((session) => (
              <div key={session.id} className={styles.sessionCard}>
                <div className={styles.sessionCardTop}>
                  <label className={styles.sessionCardSelect}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(session.id)}
                      onChange={() => toggleRowSelected(session.id)}
                      aria-label={t("analytics.table.selectRow")}
                    />
                    <span>{session.projectTitle}{session.visitorName ? ` – ${session.visitorName}` : ""}</span>
                  </label>
                  <span>{formatDuration(session.durationSeconds)}</span>
                </div>
                <div className={styles.sessionCardMeta}>
                  <span>{new Date(session.startedAt).toLocaleDateString(numberLocale())}</span>
                  <span>{t("analytics.table.messagesCount", { count: session.messageCount })}</span>
                </div>
                <Button size="sm" onClick={() => setViewingSessionId(session.id)}>
                  <Eye size={14} /> {t("analytics.table.view")}
                </Button>
              </div>
            ))
          )}
        </div>

        <div className={styles.pagination}>
          <span>
            {totalSessions === 0
              ? t("analytics.pagination.zero")
              : t("analytics.pagination.range", {
                  from: (page - 1) * PAGE_SIZE + 1,
                  to: (page - 1) * PAGE_SIZE + sessions.length,
                  total: formatCompactNumber(totalSessions),
                })}
          </span>
          <div className={styles.pageButtons}>
            <Button size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>
              <ChevronLeft size={14} />
            </Button>
            <Button
              size="sm"
              onClick={() => setPage((p) => p + 1)}
              disabled={page * PAGE_SIZE >= totalSessions}
            >
              <ChevronRight size={14} />
            </Button>
          </div>
        </div>
      </div>

      {viewingSessionId && (
        <>
          <Scrim onClick={() => setViewingSessionId(null)} />
          <div className={styles.transcriptPanel} role="dialog" aria-label={t("analytics.transcript.title")}>
            <div className={styles.transcriptHeader}>
              <div>
                <h3>{sessionDetailQuery.data?.projectTitle ?? t("analytics.transcript.title")}</h3>
                {sessionDetailQuery.data?.visitorName && (
                  <p className={styles.transcriptMeta}>{sessionDetailQuery.data.visitorName}</p>
                )}
              </div>
              <button
                type="button"
                className={styles.transcriptClose}
                onClick={() => setViewingSessionId(null)}
                aria-label={t("common.close")}
              >
                <X size={18} />
              </button>
            </div>
            <div className={styles.transcriptBody}>
              {sessionDetailQuery.isLoading && <p>{t("common.loading")}</p>}
              {sessionDetailQuery.isError && <Callout variant="danger">{t("analytics.transcript.loadError")}</Callout>}
              {sessionDetailQuery.data?.messages.map((message, index) => (
                <div key={index}>
                  <ChatBubble role={message.role === "assistant" ? "assistant" : "user"} content={message.content} />
                  {message.sources && message.sources.length > 0 && (
                    <p className={styles.transcriptSources}>
                      {t("analytics.transcript.sources")}{" "}
                      {[
                        ...new Set(
                          message.sources.map(
                            (source) =>
                              (source.filename ?? t("knowledge.deletedDocument")) +
                              (source.page ? ` (${t("knowledge.page", { page: source.page })})` : ""),
                          ),
                        ),
                      ].join(", ")}
                    </p>
                  )}
                  {message.motions && message.motions.length > 0 && (
                    <p className={styles.transcriptSources}>
                      {t("analytics.transcript.motions")}{" "}
                      {message.motions.map((motion) => describeMotion(motion, message.content, t)).join(" · ")}
                    </p>
                  )}
                  {message.checks && message.checks.length > 0 && (
                    <p className={styles.transcriptSources}>
                      {t("analytics.transcript.checks")}{" "}
                      {message.checks
                        .map((check) =>
                          t(check.done ? "analytics.transcript.checkDone" : "analytics.transcript.checkUndone", {
                            step: check.title
                              ? `${check.number}. ${check.title}`
                              : t("analytics.transcript.deletedStep"),
                          }),
                        )
                        .join(" · ")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
