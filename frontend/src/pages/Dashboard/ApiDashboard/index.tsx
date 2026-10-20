import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { PageHeader } from "@/components/PageHeader";
import { apiKeysApi } from "@/api/apiKeys";
import { errorMessage } from "@/api/client";
import { numberLocale } from "@/lib/format";
import { keyDisplayName, modelLabel, providerLabel, useProviders } from "@/lib/providers";
import { KEY_TYPE_LABELS, type ApiKey, type ApiKeyInput, type ApiKeyStatus } from "@/types/apiKey";
import { ApiKeyForm } from "./ApiKeyForm";
import styles from "./ApiDashboard.module.css";

const STATUS_VARIANT: Record<ApiKeyStatus, "accent" | "default" | "danger"> = {
  active: "accent",
  unverified: "default",
  error: "danger",
};

// Screen 1g — API dashboard tab
export function ApiDashboardPage() {
  const { t } = useTranslation();
  const STATUS_LABEL: Record<ApiKeyStatus, string> = {
    active: t("apiDashboard.statusActive"),
    unverified: t("apiDashboard.statusUnverified"),
    error: t("apiDashboard.statusError"),
  };
  const queryClient = useQueryClient();
  const providersQuery = useProviders();
  const keysQuery = useQuery({ queryKey: ["api-keys"], queryFn: apiKeysApi.list });
  const specs = providersQuery.data ?? [];
  const keys = keysQuery.data ?? [];

  const [editingKey, setEditingKey] = useState<ApiKey | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  // Result of the most recently pressed "Test" button, per key — contains the cause on failure.
  const [testResult, setTestResult] = useState<{ id: string; status: string; message: string | null } | null>(null);

  function closeForm() {
    setFormOpen(false);
    setEditingKey(null);
  }

  const saveMutation = useMutation({
    mutationFn: (input: ApiKeyInput) =>
      editingKey ? apiKeysApi.update(editingKey.id, input) : apiKeysApi.create(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      // Changing a key's model changes the stored model string of its projects.
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      closeForm();
    },
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => apiKeysApi.test(id).then((result) => ({ id, ...result })),
    onSuccess: (result) => {
      setTestResult(result);
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => apiKeysApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  function handleRemove(key: ApiKey) {
    // Deliberately a second, explicit step (see the wireframe annotation on 1g) — "Remove" revokes
    // a key for all projects, which needs deliberate friction.
    const used = key.usedByProjects > 0 ? `\n\n${t("apiDashboard.removeUsedByProjects", { count: key.usedByProjects })}` : "";
    if (window.confirm(t("apiDashboard.removeConfirm", { name: keyDisplayName(key, specs) }) + used)) {
      removeMutation.mutate(key.id);
    }
  }

  function openEdit(key: ApiKey) {
    saveMutation.reset();
    setEditingKey(key);
    setFormOpen(true);
  }

  function openCreate() {
    saveMutation.reset();
    setEditingKey(null);
    setFormOpen(true);
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("apiDashboard.title")} subtitle={t("apiDashboard.subtitle")} />

      <Callout variant="info">
        <span className={styles.calloutFull}>{t("apiDashboard.securityNoticeFull")}</span>
        <span className={styles.calloutShort}>{t("apiDashboard.securityNoticeShort")}</span>
      </Callout>

      {testResult?.status === "error" && (
        <Callout variant="danger">
          {t("apiDashboard.testFailed", { message: testResult.message ?? t("apiDashboard.testFailedFallback") })}
        </Callout>
      )}
      {testResult?.status === "active" && <Callout variant="success">{t("apiDashboard.testSucceeded")}</Callout>}

      <div className={styles.card}>
        {/* Seven columns don't always fit on narrower desktops — scroll instead of overflowing. */}
        <div className={styles.tableScroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>{t("apiDashboard.table.name")}</th>
              <th>{t("apiDashboard.table.type")}</th>
              <th>{t("apiDashboard.table.model")}</th>
              <th>{t("apiDashboard.table.key")}</th>
              <th>{t("apiDashboard.table.added")}</th>
              <th>{t("apiDashboard.table.status")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id}>
                <td>
                  {keyDisplayName(key, specs)}
                  <div className={styles.hint}>{providerLabel(specs, key.provider)}</div>
                </td>
                <td>
                  <Badge variant={key.keyType === "llm" ? "accent" : "default"}>{KEY_TYPE_LABELS[key.keyType]}</Badge>
                </td>
                <td>{modelLabel(key, specs) ?? "–"}</td>
                <td className="mono">
                  {key.maskedKey || "–"}
                  {key.apiBase && <div className={styles.hint}>{key.apiBase}</div>}
                </td>
                <td>{new Date(key.addedAt).toLocaleDateString(numberLocale())}</td>
                <td>
                  <Badge variant={STATUS_VARIANT[key.status]}>{STATUS_LABEL[key.status]}</Badge>
                </td>
                <td>
                  <div className={styles.actions}>
                    <Button size="sm" onClick={() => testMutation.mutate(key.id)} disabled={testMutation.isPending}>
                      {t("apiDashboard.test")}
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => openEdit(key)}
                      aria-label={t("overview.card.edit")}
                      title={t("overview.card.edit")}
                    >
                      <Pencil size={14} />
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => handleRemove(key)}
                      disabled={removeMutation.isPending}
                      aria-label={t("apiDashboard.remove")}
                      title={t("apiDashboard.remove")}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>

        <div className={styles.keyCards}>
          {keys.map((key) => (
            <div key={key.id} className={styles.keyCard}>
              <div className={styles.keyCardTop}>
                <strong>{keyDisplayName(key, specs)}</strong>
                <div className={styles.badgeRow}>
                  <Badge variant={key.keyType === "llm" ? "accent" : "default"}>{KEY_TYPE_LABELS[key.keyType]}</Badge>
                  <Badge variant={STATUS_VARIANT[key.status]}>{STATUS_LABEL[key.status]}</Badge>
                </div>
              </div>
              <span className={styles.hint}>
                {providerLabel(specs, key.provider)}
                {modelLabel(key, specs) && ` · ${modelLabel(key, specs)}`}
              </span>
              <span className="mono">{key.maskedKey || "–"}</span>
              {key.apiBase && <span className={styles.hint}>{key.apiBase}</span>}
              <div className={styles.keyCardActions}>
                <Button size="sm" onClick={() => testMutation.mutate(key.id)} disabled={testMutation.isPending}>
                  {t("apiDashboard.test")}
                </Button>
                <Button size="sm" onClick={() => openEdit(key)}>
                  {t("overview.card.edit")}
                </Button>
                <Button size="sm" onClick={() => handleRemove(key)} disabled={removeMutation.isPending}>
                  {t("apiDashboard.remove")}
                </Button>
              </div>
            </div>
          ))}
        </div>

        {keys.length === 0 && !keysQuery.isLoading && <p className={styles.empty}>{t("apiDashboard.empty")}</p>}
      </div>

      <div className={styles.card}>
        <h3>{editingKey ? t("apiDashboard.editKey") : t("apiDashboard.addKey")}</h3>
        {providersQuery.isError && <Callout variant="danger">{t("apiDashboard.providersLoadError")}</Callout>}
        {specs.length > 0 &&
          (formOpen ? (
            <ApiKeyForm
              // When switching between create and edit, the fields should be re-initialized from
              // the respective record instead of keeping the old input.
              key={editingKey?.id ?? "new"}
              specs={specs}
              editing={editingKey ?? undefined}
              pending={saveMutation.isPending}
              errorMessage={saveMutation.isError ? errorMessage(saveMutation.error, t("apiDashboard.saveError")) : undefined}
              onSubmit={(input) => saveMutation.mutate(input)}
              onCancel={closeForm}
            />
          ) : (
            <Button onClick={openCreate}>{t("apiDashboard.addKeyButton")}</Button>
          ))}
      </div>
    </div>
  );
}
