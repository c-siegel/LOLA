import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, CheckCircle2, Info, Loader2 } from "lucide-react";
import type { ModelId, PrefetchState } from "@/lib/modelPrefetch";
import styles from "./Start.module.css";

const MODEL_ORDER: ModelId[] = ["stt", "tts", "vad"];

function megabytes(bytes: number): string {
  return Math.round(bytes / 1_000_000).toLocaleString();
}

interface StorageInfo {
  usage: number | null;
  quota: number | null;
  persisted: boolean | null;
}

/** What this device has stored, re-read whenever a model finishes (navigator.storage is
 * missing or throws in some private modes — then it's just not shown). */
function useStorageInfo(refreshKey: unknown): StorageInfo {
  const [info, setInfo] = useState<StorageInfo>({ usage: null, quota: null, persisted: null });
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const estimate = await navigator.storage?.estimate?.();
        const persisted = (await navigator.storage?.persisted?.()) ?? null;
        if (!cancelled) setInfo({ usage: estimate?.usage ?? null, quota: estimate?.quota ?? null, persisted });
      } catch {
        // Leave it unknown.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);
  return info;
}

/** The line under the key form saying whether the speech models are on this device yet, with an
 * (i) that opens the per-model details. */
export function ModelStatus({ states, onRetry }: { states: Record<ModelId, PrefetchState>; onRetry: () => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const all = Object.values(states);
  const storage = useStorageInfo(all.filter((state) => state.status === "ready").length);

  const loading = all.some((state) => state.status === "waiting" || state.status === "downloading");
  const failed = all.some((state) => state.status === "error");
  const anyStored = all.some((state) => state.status === "ready");
  let loaded = 0;
  let total = 0;
  for (const state of all) {
    if (state.status === "downloading") {
      loaded += state.loadedBytes;
      total += state.totalBytes;
    } else if (state.status === "ready") {
      loaded += state.totalBytes;
      total += state.totalBytes;
    }
  }
  const percent = total ? Math.floor((loaded / total) * 100) : 0;

  const summary = loading
    ? t("start.models.summaryLoading", { percent })
    : failed
      ? t("start.models.summaryFailed")
      : anyStored
        ? t("start.models.summaryReady")
        : t("start.models.summaryNone");
  const SummaryIcon = loading ? Loader2 : failed ? AlertCircle : CheckCircle2;

  function stateText(state: PrefetchState): string {
    switch (state.status) {
      case "waiting":
        return t("start.models.waiting");
      case "downloading":
        return t("start.models.downloading", {
          loaded: megabytes(state.loadedBytes),
          total: megabytes(state.totalBytes),
        });
      case "ready":
        return t("start.models.ready", { size: megabytes(state.totalBytes) });
      case "unavailable":
        return t("start.models.unavailable");
      case "unsupported":
        return t("start.models.unsupported");
      case "error":
        return t("start.models.error");
    }
  }

  return (
    <section className={styles.models} aria-live="polite">
      <div className={styles.modelsSummary}>
        <SummaryIcon
          size={18}
          aria-hidden
          className={loading ? styles.spin : failed ? styles.iconDanger : styles.iconSuccess}
        />
        <span>{summary}</span>
        <button
          type="button"
          className={styles.infoButton}
          aria-label={t("start.models.details")}
          title={t("start.models.details")}
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((value) => !value)}
        >
          <Info size={20} />
        </button>
      </div>
      {open && (
        <div className={styles.modelsPanel} id={panelId}>
          <p className={styles.modelsIntro}>{t("start.models.intro")}</p>
          <ul className={styles.modelList}>
            {MODEL_ORDER.map((id) => (
              <li key={id} className={styles.modelRow}>
                <span className={styles.modelName}>{t(`start.models.names.${id}`)}</span>
                <span className={states[id].status === "error" ? styles.modelError : styles.modelState}>
                  {stateText(states[id])}
                </span>
                {states[id].status === "downloading" && (
                  <progress
                    className={styles.progress}
                    value={(states[id] as { loadedBytes: number }).loadedBytes}
                    max={(states[id] as { totalBytes: number }).totalBytes}
                  />
                )}
              </li>
            ))}
          </ul>
          {storage.usage !== null && (
            <p className={styles.modelsMeta}>
              {storage.quota
                ? t("start.models.storageUsage", { used: megabytes(storage.usage), quota: megabytes(storage.quota) })
                : t("start.models.storageUsageNoQuota", { used: megabytes(storage.usage) })}
            </p>
          )}
          {storage.persisted !== null && (
            <p className={styles.modelsMeta}>
              {storage.persisted ? t("start.models.persisted") : t("start.models.notPersisted")}
            </p>
          )}
          {failed && !loading && (
            <button type="button" className={styles.retryButton} onClick={onRetry}>
              {t("start.models.retry")}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
