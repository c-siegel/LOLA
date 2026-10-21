// Dashboard tab "Evaluation": measure how well a project answers from its knowledge bases. A run
// asks a test set's questions through the project (like a student would, without speech and
// without saving) and has a judge LLM score the answers with Ragas. Runs can be compared side by
// side, e.g. "supplement" vs. "strict", or two embedding models. Two tabs: the test questions
// (TestSets.tsx) and the runs. Only reachable when the deployment runs the evaluation service.

import { Fragment, useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Download, Square, Trash2 } from "lucide-react";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { InfoButton, InfoPanel, LabelWithInfo, useInfoTip } from "@/components/InfoTip";
import { PageHeader } from "@/components/PageHeader";
import { apiKeysApi } from "@/api/apiKeys";
import { errorMessage } from "@/api/client";
import {
  ACTIVE_RUN_STATUSES,
  METRICS,
  QUESTION_KINDS,
  evaluationApi,
  type EvaluationStatus,
  type MetricName,
  type MetricScore,
  type QuestionKind,
  type Run,
  type RunItem,
} from "@/api/evaluation";
import { projectsApi } from "@/api/projects";
import { TestQuestions } from "./TestSets";
import { numberLocale } from "@/lib/format";
import { isJudgeKey, keyDisplayName, useEvaluationStatus, useProviders } from "@/lib/providers";
import styles from "./Evaluation.module.css";

const POLL_MS = 3000;
const MAX_COMPARE = 4;

/** The evaluation tab. */
export function EvaluationPage() {
  const { t } = useTranslation();
  const statusQuery = useEvaluationStatus();
  const status = statusQuery.data;
  const runsQuery = useQuery({
    queryKey: ["evaluation-runs"],
    queryFn: evaluationApi.runs,
    enabled: Boolean(status?.available),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((r) => ACTIVE_RUN_STATUSES.includes(r.status)) ? POLL_MS : false,
  });
  const runs = runsQuery.data ?? [];
  const testSetsQuery = useQuery({
    queryKey: ["test-sets", "all"],
    queryFn: evaluationApi.allTestSets,
    enabled: Boolean(status?.available),
  });
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [chosenTab, setChosenTab] = useState<"questions" | "runs" | null>(null);

  // Someone with questions or runs comes to run or read them; a newcomer has to write questions
  // first. Decided once, when both lists are loaded — otherwise creating the first test set would
  // flip the tab under their hands.
  const loaded = testSetsQuery.isSuccess && runsQuery.isSuccess;
  useEffect(() => {
    if (loaded && chosenTab === null) {
      setChosenTab((testSetsQuery.data ?? []).length > 0 || runs.length > 0 ? "runs" : "questions");
    }
  }, [loaded, chosenTab, testSetsQuery.data, runs.length]);

  if (statusQuery.isLoading) return <p className={styles.hint}>{t("common.loading")}</p>;
  if (!status?.available) {
    return (
      <div className={styles.page}>
        <Header />
        <Callout variant="info">{t("evaluation.disabled")}</Callout>
      </div>
    );
  }

  const tab = chosenTab;

  function toggleCompare(id: string) {
    setCompareIds((ids) =>
      ids.includes(id) ? ids.filter((i) => i !== id) : ids.length < MAX_COMPARE ? [...ids, id] : ids,
    );
  }

  const compared = runs.filter((r) => compareIds.includes(r.id));

  return (
    <div className={styles.page}>
      <Header />
      {!status.reachable && <Callout variant="warning">{t("evaluation.unreachable")}</Callout>}
      <div className={styles.tabs} role="tablist" aria-label={t("evaluation.title")}>
        {(["questions", "runs"] as const).map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={`${styles.tab} ${tab === id ? styles.tabActive : ""}`}
            onClick={() => setChosenTab(id)}
          >
            {t(`evaluation.tabs.${id}`)}
          </button>
        ))}
      </div>

      {tab === "questions" && <TestQuestions status={status} />}

      {tab === "runs" && (
        <>
          <Callout variant="info">{t("evaluation.judgeNote")}</Callout>
          <StartRun status={status} onStarted={setOpenRunId} onNeedQuestions={() => setChosenTab("questions")} />

          <div className={styles.card}>
            <h3>{t("evaluation.runsTitle")}</h3>
            {runsQuery.isLoading && <p className={styles.hint}>{t("common.loading")}</p>}
            {!runsQuery.isLoading && runs.length === 0 && <p className={styles.hint}>{t("evaluation.noRuns")}</p>}
            {runs.length > 1 && <p className={styles.hint}>{t("evaluation.compareHint", { max: MAX_COMPARE })}</p>}
            <ul className={styles.runList}>
              {runs.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  open={openRunId === run.id}
                  onToggle={() => setOpenRunId(openRunId === run.id ? null : run.id)}
                  compared={compareIds.includes(run.id)}
                  onCompare={() => toggleCompare(run.id)}
                />
              ))}
            </ul>
          </div>

          {compared.length >= 2 && <Compare runs={compared} />}
        </>
      )}
    </div>
  );
}

function Header() {
  const { t } = useTranslation();
  return <PageHeader title={t("evaluation.title")} info={t("evaluation.subtitle")} />;
}

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toFixed(2);
}

function scoreClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return styles.scoreNone;
  if (value < 0.5) return styles.scoreLow;
  if (value < 0.75) return styles.scoreMid;
  return styles.scoreHigh;
}

/** Choose project, test set, judge and metrics; see the cost; confirm; start. */
function StartRun({
  status,
  onStarted,
  onNeedQuestions,
}: {
  status: EvaluationStatus;
  onStarted: (id: string) => void;
  onNeedQuestions: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const specs = useProviders().data ?? [];
  const projects = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list }).data ?? [];
  const testSets = useQuery({ queryKey: ["test-sets", "all"], queryFn: evaluationApi.allTestSets }).data ?? [];
  const judgeKeys = (useQuery({ queryKey: ["api-keys"], queryFn: apiKeysApi.list }).data ?? []).filter(isJudgeKey);
  const [projectId, setProjectId] = useState("");
  const [testSetId, setTestSetId] = useState("");
  const [judgeKeyId, setJudgeKeyId] = useState("");
  const [metrics, setMetrics] = useState<MetricName[]>([...METRICS]);
  const [confirmed, setConfirmed] = useState(false);

  const project = projects.find((p) => p.id === (projectId || projects[0]?.id));
  const testSet = testSets.find((s) => s.id === (testSetId || testSets[0]?.id));
  const judgeKey = judgeKeys.find((k) => k.id === (judgeKeyId || judgeKeys[0]?.id));
  const questions = testSet?.approvedCount ?? 0;
  const maxCases = status.maxCasesPerRun ?? 50;
  const topK = project?.knowledgeTopK ?? 5;
  const callsPerQuestion = metrics.reduce(
    (sum, metric) => sum + (status.judgeCallsPerMetric[metric] ?? 1) * (metric === "context_precision" ? topK : 1),
    0,
  );

  const startMutation = useMutation({
    mutationFn: () =>
      evaluationApi.startRun({
        projectId: project!.id,
        testSetId: testSet!.id,
        judgeApiKeyId: judgeKey!.id,
        metrics,
      }),
    onSuccess: (run) => {
      setConfirmed(false);
      queryClient.invalidateQueries({ queryKey: ["evaluation-runs"] });
      onStarted(run.id);
    },
  });

  const metricTip = useInfoTip();

  function toggleMetric(metric: MetricName) {
    setMetrics((current) => (current.includes(metric) ? current.filter((m) => m !== metric) : [...current, metric]));
    setConfirmed(false);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (canStart) startMutation.mutate();
  }

  const tooMany = questions > maxCases;
  const canStart =
    Boolean(project && testSet && judgeKey) && questions > 0 && !tooMany && metrics.length > 0 && confirmed && !startMutation.isPending;

  if (testSets.length === 0) {
    return (
      <div className={styles.card}>
        <h3>{t("evaluation.startTitle")}</h3>
        <p className={styles.hint}>{t("evaluation.noTestSets")}</p>
        <div>
          <Button size="sm" onClick={onNeedQuestions}>
            {t("evaluation.toQuestions")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form className={styles.card} onSubmit={handleSubmit}>
      <h3>{t("evaluation.startTitle")}</h3>
      <div className={styles.formGrid}>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="eval-project">
            {t("evaluation.project")}
          </label>
          <select id="eval-project" className={styles.select} value={project?.id ?? ""} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="eval-test-set">
            {t("evaluation.testSet")}
          </label>
          <select id="eval-test-set" className={styles.select} value={testSet?.id ?? ""} onChange={(e) => setTestSetId(e.target.value)}>
            {testSets.map((s) => (
              <option key={s.id} value={s.id}>
                {t("evaluation.testSetOption", { name: s.name, count: s.approvedCount })}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <LabelWithInfo
            label={t("evaluation.judgeKey")}
            htmlFor="eval-judge"
            className={styles.label}
            info={t("evaluation.judgeHint")}
          />
          <select id="eval-judge" className={styles.select} value={judgeKey?.id ?? ""} onChange={(e) => setJudgeKeyId(e.target.value)}>
            {judgeKeys.map((key) => (
              <option key={key.id} value={key.id}>
                {keyDisplayName(key, specs)} · {key.modelId}
              </option>
            ))}
          </select>
        </div>
      </div>
      {judgeKeys.length === 0 && <Callout variant="warning">{t("evaluation.noJudgeKey")}</Callout>}

      <fieldset className={styles.metrics}>
        <legend className={`${styles.label} ${styles.legendWithInfo}`}>
          {t("evaluation.metricsTitle")}
          <InfoButton
            topic={t("evaluation.metricsTitle")}
            open={metricTip.open}
            panelId={metricTip.panelId}
            onToggle={metricTip.toggle}
          />
        </legend>
        <div className={styles.metricInfo}>
          <InfoPanel id={metricTip.panelId} open={metricTip.open}>
            <ul className={styles.metricHints}>
              {METRICS.map((metric) => (
                <li key={metric}>
                  <strong>{t(`evaluation.metric.${metric}`)}:</strong> {t(`evaluation.metricHint.${metric}`)}
                </li>
              ))}
            </ul>
          </InfoPanel>
        </div>
        {METRICS.map((metric) => (
          <label key={metric} className={styles.metricOption}>
            <input type="checkbox" checked={metrics.includes(metric)} onChange={() => toggleMetric(metric)} />
            <strong>{t(`evaluation.metric.${metric}`)}</strong>
          </label>
        ))}
      </fieldset>

      {tooMany && <Callout variant="warning">{t("evaluation.tooMany", { count: questions, max: maxCases })}</Callout>}
      {questions === 0 && <Callout variant="warning">{t("evaluation.noApproved")}</Callout>}
      {questions > 0 && !tooMany && metrics.length > 0 && (
        <label className={styles.confirm}>
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          <span>
            {t("evaluation.estimate", {
              questions,
              answerCalls: questions,
              judgeCalls: questions * callsPerQuestion,
              judge: judgeKey ? `${keyDisplayName(judgeKey, specs)} · ${judgeKey.modelId}` : "—",
            })}
          </span>
        </label>
      )}
      {startMutation.isError && (
        <Callout variant="danger">{errorMessage(startMutation.error, t("evaluation.startFailed"))}</Callout>
      )}
      <div>
        <Button type="submit" variant="accent" disabled={!canStart}>
          {t("evaluation.start")}
        </Button>
      </div>
    </form>
  );
}

function statusVariant(run: Run): "default" | "accent" | "danger" {
  if (run.status === "done") return "accent";
  if (run.status === "failed" || run.status === "interrupted") return "danger";
  return "default";
}

function RunRow({
  run,
  open,
  onToggle,
  compared,
  onCompare,
}: {
  run: Run;
  open: boolean;
  onToggle: () => void;
  compared: boolean;
  onCompare: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const active = ACTIVE_RUN_STATUSES.includes(run.status);

  const cancelMutation = useMutation({
    mutationFn: () => evaluationApi.cancelRun(run.id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["evaluation-runs"] }),
  });
  const removeMutation = useMutation({
    mutationFn: () => evaluationApi.removeRun(run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["evaluation-runs"] }),
  });

  const progress =
    run.status === "answering"
      ? t("evaluation.progressAnswering", { done: run.answeredCount, total: run.caseCount })
      : run.status === "scoring"
        ? t("evaluation.progressScoring", { done: run.scoredCount, total: run.answeredCount })
        : null;

  return (
    <li className={styles.run}>
      <div className={styles.runTop}>
        <button type="button" className={styles.runToggle} onClick={onToggle} aria-expanded={open}>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <span>
            <strong>
              {run.config.projectTitle ?? "—"} · {run.config.testSetName ?? "—"}
            </strong>
            <span className={styles.hint}>
              {new Date(run.createdAt).toLocaleString(numberLocale())} · {run.config.llm ?? "—"} ·{" "}
              {t(`evaluation.knowledgeMode.${run.config.knowledgeMode ?? "off"}`, run.config.knowledgeMode ?? "")}
            </span>
          </span>
        </button>
        <div className={styles.runScores}>
          {run.metrics.map((metric) => {
            const mean = run.summary?.metrics[metric]?.mean;
            return (
              <span key={metric} className={`${styles.scoreChip} ${scoreClass(mean)}`} title={t(`evaluation.metric.${metric}`)}>
                {t(`evaluation.metricShort.${metric}`)} {formatScore(mean)}
              </span>
            );
          })}
        </div>
        <div className={styles.runActions}>
          <Badge variant={statusVariant(run)}>{t(`evaluation.status.${run.status}`)}</Badge>
          <label className={styles.compareToggle} title={t("evaluation.compare")}>
            <input type="checkbox" checked={compared} onChange={onCompare} aria-label={t("evaluation.compare")} />
          </label>
          {active ? (
            <Button
              size="sm"
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
              aria-label={t("evaluation.cancel")}
              title={t("evaluation.cancel")}
            >
              <Square size={14} />
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => {
                if (window.confirm(t("evaluation.removeConfirm"))) removeMutation.mutate();
              }}
              disabled={removeMutation.isPending}
              aria-label={t("evaluation.remove")}
              title={t("evaluation.remove")}
            >
              <Trash2 size={14} />
            </Button>
          )}
        </div>
      </div>
      {progress && <p className={styles.hint}>{progress}</p>}
      {run.errorCode && (
        <Callout variant={run.status === "interrupted" ? "warning" : "danger"}>
          {t(`errors.${run.errorCode}`, t("evaluation.failedGeneric"))}
        </Callout>
      )}
      {open && <RunDetail run={run} />}
    </li>
  );
}

function RunDetail({ run }: { run: Run }) {
  const { t } = useTranslation();
  const active = ACTIVE_RUN_STATUSES.includes(run.status);
  const detailQuery = useQuery({
    // The list row's status is part of the key, so the detail refetches when the run moves on.
    queryKey: ["evaluation-run", run.id, run.status, run.answeredCount, run.scoredCount],
    queryFn: () => evaluationApi.run(run.id),
  });
  const [sortMetric, setSortMetric] = useState<MetricName | "">("");
  const [onlyWeak, setOnlyWeak] = useState(false);
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const items = useMemo(() => {
    const all = detailQuery.data?.items ?? [];
    if (!sortMetric) return all;
    const value = (item: RunItem) => item.scores[sortMetric]?.value ?? Number.POSITIVE_INFINITY;
    const sorted = [...all].sort((a, b) => value(a) - value(b));
    return onlyWeak ? sorted.filter((item) => (item.scores[sortMetric]?.value ?? 1) < 0.5) : sorted;
  }, [detailQuery.data, sortMetric, onlyWeak]);

  return (
    <div className={styles.detail}>
      <ConfigSummary run={run} />
      {run.summary && <SummaryCards run={run} />}
      {run.summary && <KindBreakdown run={run} />}
      <Gaps run={run} items={detailQuery.data?.items ?? []} />
      {!active && (
        <div className={styles.actionsRow}>
          <Button size="sm" onClick={() => evaluationApi.exportRun(run.id, "csv")}>
            <Download size={14} /> CSV
          </Button>
          <Button size="sm" onClick={() => evaluationApi.exportRun(run.id, "json")}>
            <Download size={14} /> JSON
          </Button>
        </div>
      )}
      <div className={styles.actionsRow}>
        <label className={styles.inlineField}>
          <span>{t("evaluation.sortBy")}</span>
          <select
            className={styles.select}
            value={sortMetric}
            onChange={(e) => setSortMetric(e.target.value as MetricName | "")}
          >
            <option value="">{t("evaluation.sortOrder")}</option>
            {run.metrics.map((metric) => (
              <option key={metric} value={metric}>
                {t(`evaluation.metric.${metric}`)}
              </option>
            ))}
          </select>
        </label>
        {sortMetric && (
          <label className={styles.inlineField}>
            <input type="checkbox" checked={onlyWeak} onChange={(e) => setOnlyWeak(e.target.checked)} />
            <span>{t("evaluation.onlyWeak")}</span>
          </label>
        )}
      </div>
      {detailQuery.isLoading && <p className={styles.hint}>{t("common.loading")}</p>}
      {items.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{t("evaluation.question")}</th>
                {run.metrics.map((metric) => (
                  <th key={metric} title={t(`evaluation.metric.${metric}`)}>
                    {t(`evaluation.metricShort.${metric}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <Fragment key={item.id}>
                  <tr>
                    <td>
                      <button
                        type="button"
                        className={styles.itemToggle}
                        onClick={() => setOpenItemId(openItemId === item.id ? null : item.id)}
                        aria-expanded={openItemId === item.id}
                      >
                        {openItemId === item.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        <span>{item.question}</span>
                      </button>
                      {item.errorCode && (
                        <span className={styles.warning}>{t(`errors.${item.errorCode}`, t("evaluation.failedGeneric"))}</span>
                      )}
                    </td>
                    {run.metrics.map((metric) => (
                      <ScoreCell key={metric} score={item.scores[metric]} />
                    ))}
                  </tr>
                  {openItemId === item.id && (
                    <tr>
                      <td colSpan={run.metrics.length + 1}>
                        <ItemDetail item={item} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** The scores per question kind: "faithful on questions from the material" and "covered on topic
 * questions" say different things, so they aren't averaged together. */
function KindBreakdown({ run }: { run: Run }) {
  const { t } = useTranslation();
  const byKind = run.summary?.byKind ?? {};
  const kinds = QUESTION_KINDS.filter((kind) => byKind[kind]);
  if (kinds.length < 2) return null;
  const metrics = run.metrics.filter((metric) => kinds.some((kind) => (byKind[kind]?.metrics[metric]?.count ?? 0) > 0));
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.caption}>{t("evaluation.byKindTitle")}</caption>
        <thead>
          <tr>
            <th>{t("evaluation.kind")}</th>
            {metrics.map((metric) => (
              <th key={metric} title={t(`evaluation.metric.${metric}`)}>
                {t(`evaluation.metricShort.${metric}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {kinds.map((kind) => (
            <tr key={kind}>
              <th scope="row">
                {t(`testSets.kind.${kind}`)} <span className={styles.hint}>({byKind[kind]?.count})</span>
              </th>
              {metrics.map((metric) => {
                const mean = byKind[kind]?.metrics[metric]?.mean;
                return (
                  <td key={metric} className={scoreClass(mean)}>
                    {formatScore(mean)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The questions about the subject that the material couldn't answer: what to add to it. */
function Gaps({ run, items }: { run: Run; items: RunItem[] }) {
  const { t } = useTranslation();
  if (!run.metrics.includes("coverage") || !run.summary?.byKind.topic) return null;
  const gaps = items
    .filter((item) => item.kind === "topic" && (item.scores.coverage?.value ?? 1) < 1)
    .sort((a, b) => (a.scores.coverage?.value ?? 0) - (b.scores.coverage?.value ?? 0));
  const scored = items.some((item) => item.kind === "topic" && item.scores.coverage?.value != null);
  if (!scored) return null;
  return (
    <div className={styles.gaps}>
      <h4>{t("evaluation.gapsTitle", { count: gaps.length })}</h4>
      {gaps.length === 0 ? (
        <p className={styles.hint}>{t("evaluation.noGaps")}</p>
      ) : (
        <>
          <p className={styles.hint}>{t("evaluation.gapsHint")}</p>
          <ul>
            {gaps.map((item) => (
              <li key={item.id}>
                <Badge variant={item.scores.coverage?.value === 0 ? "danger" : "default"}>
                  {item.scores.coverage?.value === 0 ? t("evaluation.gapNone") : t("evaluation.gapPartly")}
                </Badge>{" "}
                {item.question}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ScoreCell({ score }: { score: MetricScore | undefined }) {
  const { t } = useTranslation();
  if (!score) return <td className={styles.scoreNone}>…</td>;
  if (score.value === null) {
    return (
      <td className={styles.scoreNone} title={score.error ? t(`evaluation.scoreError.${score.error}`, score.error) : undefined}>
        —
      </td>
    );
  }
  return <td className={scoreClass(score.value)}>{formatScore(score.value)}</td>;
}

function ItemDetail({ item }: { item: RunItem }) {
  const { t } = useTranslation();
  const reasons = Object.entries(item.scores).filter(([, score]) => score.error);
  return (
    <div className={styles.itemDetail}>
      <p className={styles.hint}>
        {t("evaluation.kind")}: {t(`testSets.kind.${item.kind}`)}
      </p>
      <div>
        <span className={styles.label}>{t("evaluation.answer")}</span>
        <p className={styles.text}>{item.answer ?? "—"}</p>
      </div>
      {item.reference && (
        <div>
          <span className={styles.label}>{t("evaluation.reference")}</span>
          <p className={styles.text}>{item.reference}</p>
        </div>
      )}
      <div>
        <span className={styles.label}>{t("evaluation.passages", { count: item.contexts.length })}</span>
        {item.contexts.length === 0 && <p className={styles.hint}>{t("evaluation.noPassages")}</p>}
        <ol className={styles.passages}>
          {item.contexts.map((context, index) => (
            <li key={index}>
              <span className={styles.hint}>
                {context.filename ?? "—"}
                {context.page ? ` · ${t("knowledge.page", { page: context.page })}` : ""}
              </span>
              <p className={styles.text}>{context.text}</p>
            </li>
          ))}
        </ol>
      </div>
      {reasons.length > 0 && (
        <ul className={styles.reasons}>
          {reasons.map(([metric, score]) => (
            <li key={metric}>
              {t(`evaluation.metric.${metric}`)}: {t(`evaluation.scoreError.${score.error}`, score.error ?? "")}
            </li>
          ))}
        </ul>
      )}
      <p className={styles.hint}>
        {t("evaluation.latency", {
          retrieval: item.retrievalMs === null ? "—" : Math.round(item.retrievalMs),
          llm: item.llmMs === null ? "—" : Math.round(item.llmMs),
        })}
      </p>
    </div>
  );
}

function ConfigSummary({ run }: { run: Run }) {
  const { t } = useTranslation();
  const c = run.config;
  return (
    <dl className={styles.config}>
      <dt>{t("evaluation.config.llm")}</dt>
      <dd>{c.llm ?? "—"}</dd>
      <dt>{t("evaluation.config.knowledge")}</dt>
      <dd>
        {t(`evaluation.knowledgeMode.${c.knowledgeMode ?? "off"}`, c.knowledgeMode ?? "—")}
        {c.knowledgeMode !== "off" && c.topK ? ` · top ${c.topK}` : ""}
        {c.knowledgeBases.length > 0 && ` · ${c.knowledgeBases.map((kb) => kb.name).join(", ")}`}
      </dd>
      <dt>{t("evaluation.config.embedding")}</dt>
      <dd>{[...new Set(c.knowledgeBases.map((kb) => kb.embeddingModel).filter(Boolean))].join(", ") || "—"}</dd>
      <dt>{t("evaluation.config.judge")}</dt>
      <dd>{c.judge ?? "—"}</dd>
      <dt>{t("evaluation.config.testSet")}</dt>
      <dd>
        {c.testSetName ?? "—"} ({run.caseCount})
      </dd>
    </dl>
  );
}

function SummaryCards({ run }: { run: Run }) {
  const { t } = useTranslation();
  const summary = run.summary!;
  return (
    <div className={styles.summary}>
      {run.metrics.map((metric) => {
        const m = summary.metrics[metric];
        return (
          <div key={metric} className={styles.summaryCard}>
            <span className={styles.summaryLabel}>{t(`evaluation.metric.${metric}`)}</span>
            <span className={`${styles.summaryValue} ${scoreClass(m?.mean)}`}>{formatScore(m?.mean)}</span>
            <span className={styles.hint}>
              {t("evaluation.summaryMeta", { median: formatScore(m?.median), count: m?.count ?? 0 })}
            </span>
          </div>
        );
      })}
      <div className={styles.summaryCard}>
        <span className={styles.summaryLabel}>{t("evaluation.latencyTitle")}</span>
        <span className={styles.hint}>
          {t("evaluation.latencySummary", {
            retrieval: summary.retrievalMs.p50 ?? "—",
            llm: summary.llmMs.p50 ?? "—",
            llm90: summary.llmMs.p90 ?? "—",
          })}
        </span>
      </div>
    </div>
  );
}

/** Two or more runs side by side, with their configuration as column headers. */
function Compare({ runs }: { runs: Run[] }) {
  const { t } = useTranslation();
  const metrics = METRICS.filter((metric) => runs.some((run) => run.metrics.includes(metric)));
  const configRows: { label: string; value: (run: Run) => string }[] = [
    { label: t("evaluation.config.project"), value: (run) => run.config.projectTitle ?? "—" },
    { label: t("evaluation.config.llm"), value: (run) => run.config.llm ?? "—" },
    {
      label: t("evaluation.config.knowledge"),
      value: (run) =>
        `${t(`evaluation.knowledgeMode.${run.config.knowledgeMode ?? "off"}`, run.config.knowledgeMode ?? "—")}${
          run.config.knowledgeMode !== "off" && run.config.topK ? ` · top ${run.config.topK}` : ""
        }`,
    },
    {
      label: t("evaluation.config.embedding"),
      value: (run) => [...new Set(run.config.knowledgeBases.map((kb) => kb.embeddingModel).filter(Boolean))].join(", ") || "—",
    },
    { label: t("evaluation.config.judge"), value: (run) => run.config.judge ?? "—" },
    { label: t("evaluation.config.testSet"), value: (run) => `${run.config.testSetName ?? "—"} (${run.caseCount})` },
  ];
  return (
    <div className={styles.card}>
      <h3>{t("evaluation.compareTitle")}</h3>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th />
              {runs.map((run) => (
                <th key={run.id}>{new Date(run.createdAt).toLocaleString(numberLocale())}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {configRows.map((row) => (
              <tr key={row.label}>
                <th scope="row">{row.label}</th>
                {runs.map((run) => (
                  <td key={run.id}>{row.value(run)}</td>
                ))}
              </tr>
            ))}
            {metrics.map((metric) => (
              <tr key={metric}>
                <th scope="row">{t(`evaluation.metric.${metric}`)}</th>
                {runs.map((run) => {
                  const mean = run.summary?.metrics[metric]?.mean;
                  return (
                    <td key={run.id} className={scoreClass(mean)}>
                      {formatScore(mean)}
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <th scope="row">{t("evaluation.latencyLlm")}</th>
              {runs.map((run) => (
                <td key={run.id}>{run.summary?.llmMs.p50 ?? "—"}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
