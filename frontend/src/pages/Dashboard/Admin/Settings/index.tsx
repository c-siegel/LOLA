import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { HeadingWithInfo, ToggleWithInfo } from "@/components/InfoTip";
import { Input } from "@/components/Input";
import { PageHeader } from "@/components/PageHeader";
import { adminApi, type SiteSettings } from "@/api/admin";
import { errorMessage } from "@/api/client";
import styles from "./AdminSettings.module.css";

const EMPTY_FORM: SiteSettings = {
  contactEmail: "",
  contactPhone: "",
  providerName: "",
  providerStreet: "",
  providerCity: "",
  providerCountry: "",
  registrationEnabled: true,
  conversationRetentionDays: 0,
  ragMaxUploadMb: 20,
  ragMaxPages: 500,
  ragMaxCharsPerDocument: 2_000_000,
  ragMaxDocumentsPerKb: 50,
  ragMaxKbPerUser: 20,
  ragUserQuotaMb: 200,
  ragUploadRatePer10Min: 30,
  ragEvalMaxCasesPerRun: 50,
};

type KnowledgeLimitField =
  | "ragMaxUploadMb"
  | "ragMaxPages"
  | "ragMaxCharsPerDocument"
  | "ragMaxDocumentsPerKb"
  | "ragMaxKbPerUser"
  | "ragUserQuotaMb"
  | "ragUploadRatePer10Min"
  | "ragEvalMaxCasesPerRun";

// The knowledge-base limits, in display order. `ceiling` names the operator's hard limit (from
// GET /admin/settings/knowledge-ceilings) a field may not exceed; `max` is a fixed bound of the
// backend's; the others are backend-only.
const KNOWLEDGE_LIMIT_FIELDS: {
  field: KnowledgeLimitField;
  ceiling?: "maxUploadMb" | "maxPages" | "maxChars";
  max?: number;
  min: number;
}[] = [
  { field: "ragMaxUploadMb", ceiling: "maxUploadMb", min: 1 },
  { field: "ragMaxPages", ceiling: "maxPages", min: 1 },
  { field: "ragMaxCharsPerDocument", ceiling: "maxChars", min: 1000 },
  { field: "ragMaxDocumentsPerKb", min: 1 },
  { field: "ragMaxKbPerUser", min: 1 },
  { field: "ragUserQuotaMb", min: 1 },
  { field: "ragUploadRatePer10Min", min: 1 },
  // Only matters with the evaluation service, but harmless to keep without it.
  { field: "ragEvalMaxCasesPerRun", min: 1, max: 500 },
];

/** Admin dashboard: instance-wide site settings (imprint details, registration, data retention,
 * knowledge-base limits). */
export function AdminSettingsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["admin", "settings"], queryFn: adminApi.getSettings });
  const ceilingsQuery = useQuery({ queryKey: ["admin", "knowledge-ceilings"], queryFn: adminApi.getKnowledgeCeilings });
  const ceilings = ceilingsQuery.data;

  const [form, setForm] = useState<SiteSettings>(EMPTY_FORM);

  // Seeds the form from the loaded settings once, then only the user's own edits control it —
  // same pattern as the Configurator draft (see pages/Dashboard/Configurator).
  useEffect(() => {
    if (settingsQuery.data) {
      setForm({
        ...settingsQuery.data,
        // Null (never filled in) becomes "" so the inputs stay controlled.
        contactEmail: settingsQuery.data.contactEmail ?? "",
        contactPhone: settingsQuery.data.contactPhone ?? "",
        providerName: settingsQuery.data.providerName ?? "",
        providerStreet: settingsQuery.data.providerStreet ?? "",
        providerCity: settingsQuery.data.providerCity ?? "",
        providerCountry: settingsQuery.data.providerCountry ?? "",
      });
    }
  }, [settingsQuery.data]);

  function update(patch: Partial<SiteSettings>) {
    setForm((current) => ({ ...current, ...patch }));
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      adminApi.updateSettings({
        // Blank input means "not set" — send null so the public pages fall back to the
        // placeholder rather than rendering an empty line.
        contactEmail: form.contactEmail?.trim() || null,
        contactPhone: form.contactPhone?.trim() || null,
        providerName: form.providerName?.trim() || null,
        providerStreet: form.providerStreet?.trim() || null,
        providerCity: form.providerCity?.trim() || null,
        providerCountry: form.providerCountry?.trim() || null,
        registrationEnabled: form.registrationEnabled,
        conversationRetentionDays: form.conversationRetentionDays,
        // Only sent where the knowledge service exists — otherwise there's nothing to limit.
        ...(ceilings?.enabled
          ? Object.fromEntries(KNOWLEDGE_LIMIT_FIELDS.map(({ field }) => [field, form[field]]))
          : {}),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["admin", "settings"], updated);
      // The public /settings/public endpoint serves the same values — refetch so the Imprint and
      // privacy pages pick the change up without a reload.
      queryClient.invalidateQueries({ queryKey: ["settings", "public"] });
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (saveMutation.isPending) return;
    saveMutation.mutate();
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("admin.settings.title")} subtitle={t("admin.settings.subtitle")} />

      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.card}>
          <HeadingWithInfo title={t("admin.settings.imprintTitle")} info={t("admin.settings.imprintHint")} />

          <Input
            label={t("admin.settings.providerNameLabel")}
            value={form.providerName ?? ""}
            onChange={(e) => update({ providerName: e.target.value })}
          />
          <Input
            label={t("admin.settings.providerStreetLabel")}
            value={form.providerStreet ?? ""}
            onChange={(e) => update({ providerStreet: e.target.value })}
          />
          <Input
            label={t("admin.settings.providerCityLabel")}
            value={form.providerCity ?? ""}
            onChange={(e) => update({ providerCity: e.target.value })}
          />
          <Input
            label={t("admin.settings.providerCountryLabel")}
            value={form.providerCountry ?? ""}
            onChange={(e) => update({ providerCountry: e.target.value })}
          />
          <Input
            label={t("admin.settings.contactEmailLabel")}
            type="email"
            placeholder="kontakt@example.com"
            value={form.contactEmail ?? ""}
            onChange={(e) => update({ contactEmail: e.target.value })}
          />
          <Input
            label={t("admin.settings.contactPhoneLabel")}
            value={form.contactPhone ?? ""}
            onChange={(e) => update({ contactPhone: e.target.value })}
          />
        </div>

        <div className={styles.card}>
          <h3>{t("admin.settings.privacyTitle")}</h3>

          <Input
            label={t("admin.settings.retentionLabel")}
            type="number"
            min={0}
            value={String(form.conversationRetentionDays)}
            onChange={(e) => update({ conversationRetentionDays: Math.max(0, Number(e.target.value) || 0) })}
            info={t("admin.settings.retentionHint")}
          />

          <ToggleWithInfo
            title={t("admin.settings.registrationTitle")}
            checked={form.registrationEnabled}
            onChange={(e) => update({ registrationEnabled: e.target.checked })}
            info={t("admin.settings.registrationText")}
          />
        </div>

        {ceilings?.enabled && (
          <div className={styles.card}>
            <HeadingWithInfo title={t("admin.settings.knowledgeTitle")} info={t("admin.settings.knowledgeHint")} />
            {KNOWLEDGE_LIMIT_FIELDS.map(({ field, ceiling, max: fixedMax, min }) => {
              const max = ceiling ? ceilings[ceiling] : fixedMax;
              return (
                <div key={field} className={styles.limitField}>
                  <Input
                    label={t(`admin.settings.knowledgeLimits.${field}`)}
                    type="number"
                    min={min}
                    max={max}
                    value={String(form[field])}
                    onChange={(e) => update({ [field]: Math.max(min, Math.floor(Number(e.target.value) || min)) })}
                  />
                  {max !== undefined && (
                    <span className={styles.limitCeiling}>
                      {t("admin.settings.knowledgeCeiling", { max: max.toLocaleString() })}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {saveMutation.isError && (
          <Callout variant="danger">{errorMessage(saveMutation.error, t("admin.settings.saveError"))}</Callout>
        )}
        {saveMutation.isSuccess && <Callout variant="success">{t("admin.settings.saved")}</Callout>}

        <div>
          <Button type="submit" variant="accent" disabled={saveMutation.isPending}>
            {t("admin.settings.save")}
          </Button>
        </div>
      </form>
    </div>
  );
}
