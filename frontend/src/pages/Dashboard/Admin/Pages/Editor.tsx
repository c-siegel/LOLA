import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, FileText } from "lucide-react";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { ToggleWithInfo } from "@/components/InfoTip";
import { Input, Textarea } from "@/components/Input";
import { MarkdownContent } from "@/components/MarkdownContent";
import { errorMessage } from "@/api/client";
import {
  adminPagesApi,
  PAGE_LOCALES,
  type AdminPage,
  type PageLocale,
  type PageRevisionAction,
  type TranslationInput,
} from "@/api/pages";
import { settingsApi } from "@/api/settings";
import { numberLocale } from "@/lib/format";
import { builtinLegalMarkdown } from "@/lib/legalTemplates";
import { FALLBACK_PAGE_LOCALE, pickTitle, usePageLocale } from "@/lib/pages";
import { LastChange } from "./LastChange";
import listStyles from "./Pages.module.css";
import styles from "./Editor.module.css";

// The backend's limits (features/pages/schemas.py), so the browser stops at the same point.
const MAX_TITLE = 200;
const MAX_CONTENT = 100_000;
const MAX_FIELD = 2_000;

// The landing page's editable texts (backend: service.py::LANDING_FIELDS), each with the
// built-in text it replaces — shown as the placeholder, so an empty field reads as "unchanged".
const LANDING_FIELDS: { field: string; defaultKey: string; multiline?: boolean }[] = [
  { field: "badge", defaultKey: "landing.badge" },
  { field: "headingFull", defaultKey: "landing.headingFull" },
  { field: "headingShort", defaultKey: "landing.headingShort" },
  { field: "heroText", defaultKey: "landing.heroText", multiline: true },
  { field: "configureTitle", defaultKey: "landing.features.configure.full" },
  { field: "configureText", defaultKey: "landing.features.configure.text", multiline: true },
  { field: "publishTitle", defaultKey: "landing.features.publish.full" },
  { field: "publishText", defaultKey: "landing.features.publish.text", multiline: true },
  { field: "flexibilityTitle", defaultKey: "landing.features.flexibility.full" },
  { field: "flexibilityText", defaultKey: "landing.features.flexibility.text", multiline: true },
];

const PUBLIC_PATHS: Record<string, string> = { landing: "/", imprint: "/impressum", privacy: "/datenschutz" };

const EMPTY_DRAFT: TranslationInput = { title: "", contentMd: "", fields: {} };

function draftFrom(page: AdminPage | undefined, locale: PageLocale): TranslationInput {
  const translation = page?.translations.find((tr) => tr.locale === locale);
  if (!translation) return EMPTY_DRAFT;
  return { title: translation.title, contentMd: translation.contentMd, fields: { ...translation.fields } };
}

// Compared after trimming, like the backend saves it, so stray whitespace doesn't count as an edit.
function normalized(draft: TranslationInput): string {
  const fields = Object.fromEntries(
    Object.entries(draft.fields)
      .map(([key, value]) => [key, value.trim()])
      .filter(([, value]) => value),
  );
  return JSON.stringify([draft.title.trim(), draft.contentMd.trim(), Object.entries(fields).sort()]);
}

function hasOwnText(page: AdminPage, locale: PageLocale): boolean {
  const translation = page.translations.find((tr) => tr.locale === locale);
  if (!translation) return false;
  // Matches lib/pages.ts: the imprint and privacy policy only count with Markdown.
  return page.kind === "imprint" || page.kind === "privacy" ? translation.contentMd.trim() !== "" : true;
}

/** What a visitor sees in `locale` right now — the same order as lib/pages.ts. */
function statusKey(page: AdminPage, locale: PageLocale): string {
  if (hasOwnText(page, locale)) return "adminPages.status.own";
  if (locale !== FALLBACK_PAGE_LOCALE && hasOwnText(page, FALLBACK_PAGE_LOCALE)) return "adminPages.status.fallbackEnglish";
  if (page.kind !== "custom") return "adminPages.status.builtin";
  return page.translations.length > 0 ? "adminPages.status.customOther" : "adminPages.status.customEmpty";
}

/** A Markdown textarea with a preview tab — the same pattern as the configurator's system prompt. */
function MarkdownEditor({
  label,
  value,
  onChange,
  info,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  info?: string;
}) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState(false);
  return (
    <div className={styles.markdownEditor}>
      <div className={styles.viewToggle} role="tablist" aria-label={label}>
        <button
          type="button"
          role="tab"
          aria-selected={!preview}
          className={!preview ? styles.viewToggleActive : undefined}
          onClick={() => setPreview(false)}
        >
          {t("adminPages.editor.write")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={preview}
          className={preview ? styles.viewToggleActive : undefined}
          onClick={() => setPreview(true)}
        >
          {t("adminPages.editor.preview")}
        </button>
      </div>
      {preview ? (
        <div className={styles.preview} aria-label={t("adminPages.editor.preview")}>
          {value.trim() ? (
            <MarkdownContent>{value}</MarkdownContent>
          ) : (
            <p className={listStyles.muted}>{t("adminPages.editor.previewEmpty")}</p>
          )}
        </div>
      ) : (
        <Textarea
          label={label}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={18}
          maxLength={MAX_CONTENT}
          className={styles.markdownInput}
          hint={t("adminPages.editor.markdownHint")}
          info={info}
        />
      )}
    </div>
  );
}

function revisionLabel(t: (key: string) => string, action: PageRevisionAction, locale: PageLocale | null): string {
  const text = t(`adminPages.history.actions.${action}`);
  return locale ? `${text} (${locale.toUpperCase()})` : text;
}

/** Admin dashboard: edit one public page per language, plus a custom page's settings and the
 * page's change history. */
export function AdminPageEditorPage() {
  const { t, i18n } = useTranslation();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const uiLocale = usePageLocale();

  const pageQuery = useQuery({ queryKey: ["admin", "pages", id], queryFn: () => adminPagesApi.get(id) });
  const historyQuery = useQuery({ queryKey: ["admin", "pages", id, "history"], queryFn: () => adminPagesApi.history(id) });
  // Only needed to fill in the built-in imprint/privacy text as a starting point.
  const settingsQuery = useQuery({ queryKey: ["settings", "public"], queryFn: settingsApi.getPublic });
  const page = pageQuery.data;

  const [locale, setLocale] = useState<PageLocale>(uiLocale);
  // One draft per language, so switching tabs keeps unsaved edits in both.
  const [drafts, setDrafts] = useState<Partial<Record<PageLocale, TranslationInput>>>({});
  const draft = drafts[locale] ?? draftFrom(page, locale);
  const dirtyLocales = page
    ? PAGE_LOCALES.filter((l) => drafts[l] && normalized(drafts[l]!) !== normalized(draftFrom(page, l)))
    : [];
  const dirty = dirtyLocales.includes(locale);

  const [settings, setSettings] = useState({ slug: "", isPublished: false, showInFooter: true, footerOrder: 0 });
  // Keyed on the saved values rather than the page object, so a background refetch (window focus)
  // doesn't overwrite settings the admin is still editing.
  useEffect(() => {
    if (page) {
      setSettings({
        slug: page.slug,
        isPublished: page.isPublished,
        showInFooter: page.showInFooter,
        footerOrder: page.footerOrder,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page?.id, page?.slug, page?.isPublished, page?.showInFooter, page?.footerOrder]);

  // The browser's own "leave page?" prompt while any language has unsaved text.
  useEffect(() => {
    if (dirtyLocales.length === 0) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirtyLocales.length]);

  function updateDraft(patch: Partial<TranslationInput>) {
    setDrafts((current) => ({ ...current, [locale]: { ...draft, ...patch } }));
  }

  function afterChange(updated: AdminPage | undefined, savedLocale?: PageLocale) {
    if (updated) queryClient.setQueryData(["admin", "pages", id], updated);
    if (savedLocale) {
      setDrafts((current) => {
        const next = { ...current };
        delete next[savedLocale];
        return next;
      });
    }
    queryClient.invalidateQueries({ queryKey: ["admin", "pages"], exact: true });
    queryClient.invalidateQueries({ queryKey: ["admin", "pages", id, "history"] });
    // The public pages and footer links read the same data.
    queryClient.invalidateQueries({ queryKey: ["pages"] });
  }

  const saveMutation = useMutation({
    mutationFn: (target: PageLocale) => {
      const current = drafts[target] ?? draftFrom(page, target);
      const isLegal = page?.kind === "imprint" || page?.kind === "privacy";
      return adminPagesApi.saveTranslation(id, target, {
        // On the imprint/privacy page a heading alone would never be shown (the template keeps
        // its own), so it's only sent together with Markdown.
        title: isLegal && !current.contentMd.trim() ? "" : current.title,
        contentMd: current.contentMd,
        fields: current.fields,
      });
    },
    onSuccess: (updated, target) => afterChange(updated, target),
  });

  const resetMutation = useMutation({
    mutationFn: (target: PageLocale) => adminPagesApi.resetTranslation(id, target),
    onSuccess: (updated, target) => afterChange(updated, target),
  });

  const settingsMutation = useMutation({
    mutationFn: () => adminPagesApi.update(id, { ...settings, slug: settings.slug.trim() }),
    onSuccess: (updated) => afterChange(updated),
  });

  const deleteMutation = useMutation({
    mutationFn: () => adminPagesApi.remove(id),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["admin", "pages", id] });
      queryClient.invalidateQueries({ queryKey: ["admin", "pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      navigate("/dashboard/admin/pages");
    },
  });

  if (pageQuery.isPending) return null;
  if (!page) {
    return (
      <div className={listStyles.page}>
        <Callout variant="danger">{errorMessage(pageQuery.error, t("adminPages.loadError"))}</Callout>
        <Link to="/dashboard/admin/pages">{t("adminPages.editor.back")}</Link>
      </div>
    );
  }

  const isCustom = page.kind === "custom";
  const isLanding = page.kind === "landing";
  const isLegal = page.kind === "imprint" || page.kind === "privacy";
  const fixedT = i18n.getFixedT(locale);
  const titles = Object.fromEntries(page.translations.map((tr) => [tr.locale, tr.title]));
  const heading = isCustom ? pickTitle(titles, uiLocale) || page.slug : t(`adminPages.kinds.${page.kind}`);
  const publicPath = isCustom ? `/p/${page.slug}` : PUBLIC_PATHS[page.kind];
  const savedHere = page.translations.some((tr) => tr.locale === locale);

  function handleSave(event: FormEvent) {
    event.preventDefault();
    if (saveMutation.isPending) return;
    saveMutation.mutate(locale);
  }

  function handleReset() {
    if (window.confirm(t(isCustom ? "adminPages.editor.confirmDeleteText" : "adminPages.editor.confirmReset"))) {
      resetMutation.mutate(locale);
    }
  }

  function insertBuiltin() {
    if (!isLegal) return;
    if (draft.contentMd.trim() && !window.confirm(t("adminPages.editor.confirmInsertBuiltin"))) return;
    updateDraft({
      title: draft.title || fixedT(`${page!.kind}.title`),
      contentMd: builtinLegalMarkdown(page!.kind as "imprint" | "privacy", locale, settingsQuery.data),
    });
  }

  function handleSettings(event: FormEvent) {
    event.preventDefault();
    if (!settingsMutation.isPending) settingsMutation.mutate();
  }

  return (
    <div className={listStyles.page}>
      <Link to="/dashboard/admin/pages" className={styles.back}>
        <ArrowLeft size={16} aria-hidden="true" /> {t("adminPages.editor.back")}
      </Link>

      <div className={styles.titleRow}>
        <div className={listStyles.header}>
          <h2>{heading}</h2>
          <p>{t(`adminPages.descriptions.${page.kind}`)}</p>
        </div>
        {(!isCustom || page.isPublished) && (
          <Link to={publicPath} target="_blank" rel="noopener noreferrer" className={styles.viewLink}>
            {t("adminPages.editor.view")} <ExternalLink size={14} aria-hidden="true" />
          </Link>
        )}
      </div>

      <form className={listStyles.card} onSubmit={handleSave}>
        <div className={styles.localeTabs} role="tablist" aria-label={t("adminPages.editor.languageTabs")}>
          {PAGE_LOCALES.map((l) => (
            <button
              key={l}
              type="button"
              role="tab"
              aria-selected={l === locale}
              className={l === locale ? styles.localeTabActive : undefined}
              onClick={() => {
                setLocale(l);
                // The "Saved" note belongs to the language it was saved in.
                saveMutation.reset();
              }}
            >
              {t(`adminPages.languages.${l}`)}
              {dirtyLocales.includes(l) && (
                <span className={styles.dirtyDot} title={t("adminPages.editor.unsaved")} aria-label={t("adminPages.editor.unsaved")} />
              )}
            </button>
          ))}
        </div>

        <Callout variant={hasOwnText(page, locale) ? "success" : "info"}>{t(statusKey(page, locale))}</Callout>

        {isLanding ? (
          <>
            <div className={styles.fieldGrid}>
              {LANDING_FIELDS.map(({ field, defaultKey, multiline }) => {
                const props = {
                  label: t(`adminPages.landingFields.${field}`),
                  placeholder: fixedT(defaultKey),
                  value: draft.fields[field] ?? "",
                  maxLength: MAX_FIELD,
                };
                const onChange = (value: string) => updateDraft({ fields: { ...draft.fields, [field]: value } });
                return multiline ? (
                  <Textarea key={field} {...props} rows={3} onChange={(e) => onChange(e.target.value)} />
                ) : (
                  <Input key={field} {...props} onChange={(e) => onChange(e.target.value)} />
                );
              })}
            </div>
            <p className={listStyles.hint}>{t("adminPages.editor.landingFieldsHint")}</p>
            <MarkdownEditor
              label={t("adminPages.editor.landingExtra")}
              info={t("adminPages.editor.landingExtraHint")}
              value={draft.contentMd}
              onChange={(contentMd) => updateDraft({ contentMd })}
            />
          </>
        ) : (
          <>
            <Input
              label={t(isCustom ? "adminPages.editor.titleLabel" : "adminPages.editor.titleOptionalLabel")}
              placeholder={isCustom ? "" : fixedT(`${page.kind}.title`)}
              value={draft.title}
              maxLength={MAX_TITLE}
              onChange={(e) => updateDraft({ title: e.target.value })}
              info={t(isCustom ? "adminPages.editor.titleHint" : "adminPages.editor.titleOptionalHint")}
            />
            {isLegal && (
              <div>
                <Button type="button" size="sm" onClick={insertBuiltin}>
                  <FileText size={14} aria-hidden="true" /> {t("adminPages.editor.insertBuiltin")}
                </Button>
              </div>
            )}
            <MarkdownEditor
              label={t("adminPages.editor.contentLabel")}
              info={t(isLegal ? "adminPages.editor.contentLegalHint" : "adminPages.editor.contentHint")}
              value={draft.contentMd}
              onChange={(contentMd) => updateDraft({ contentMd })}
            />
          </>
        )}

        {saveMutation.isError && (
          <Callout variant="danger">{errorMessage(saveMutation.error, t("adminPages.editor.saveError"))}</Callout>
        )}
        {resetMutation.isError && (
          <Callout variant="danger">{errorMessage(resetMutation.error, t("adminPages.editor.saveError"))}</Callout>
        )}
        {saveMutation.isSuccess && !dirty && <Callout variant="success">{t("adminPages.editor.saved")}</Callout>}

        <div className={styles.formActions}>
          <Button type="submit" variant="accent" disabled={!dirty || saveMutation.isPending}>
            {t("adminPages.editor.save", { language: t(`adminPages.languages.${locale}`) })}
          </Button>
          {savedHere && (
            <Button type="button" variant="danger" onClick={handleReset} disabled={resetMutation.isPending}>
              {t(isCustom ? "adminPages.editor.deleteText" : "adminPages.editor.reset")}
            </Button>
          )}
        </div>
      </form>

      {isCustom && (
        <form className={listStyles.card} onSubmit={handleSettings}>
          <h3>{t("adminPages.settings.title")}</h3>
          <Input
            label={t("adminPages.slugLabel")}
            value={settings.slug}
            onChange={(e) => setSettings({ ...settings, slug: e.target.value })}
            info={t("adminPages.slugHint")}
          />
          <ToggleWithInfo
            title={t("adminPages.settings.published")}
            checked={settings.isPublished}
            onChange={(e) => setSettings({ ...settings, isPublished: e.target.checked })}
            info={t("adminPages.settings.publishedHint")}
          />
          <ToggleWithInfo
            title={t("adminPages.settings.showInFooter")}
            checked={settings.showInFooter}
            onChange={(e) => setSettings({ ...settings, showInFooter: e.target.checked })}
            info={t("adminPages.settings.showInFooterHint")}
          />
          <Input
            label={t("adminPages.settings.footerOrder")}
            type="number"
            min={0}
            max={1000}
            value={String(settings.footerOrder)}
            onChange={(e) =>
              setSettings({ ...settings, footerOrder: Math.min(1000, Math.max(0, Math.floor(Number(e.target.value) || 0))) })
            }
            info={t("adminPages.settings.footerOrderHint")}
          />
          {settingsMutation.isError && (
            <Callout variant="danger">{errorMessage(settingsMutation.error, t("adminPages.settings.saveError"))}</Callout>
          )}
          {deleteMutation.isError && (
            <Callout variant="danger">{errorMessage(deleteMutation.error, t("adminPages.settings.deleteError"))}</Callout>
          )}
          <div className={styles.formActions}>
            <Button type="submit" disabled={settingsMutation.isPending}>
              {t("adminPages.settings.save")}
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={deleteMutation.isPending}
              onClick={() => window.confirm(t("adminPages.settings.confirmDelete")) && deleteMutation.mutate()}
            >
              {t("adminPages.settings.delete")}
            </Button>
          </div>
        </form>
      )}

      <div className={listStyles.card}>
        <h3>{t("adminPages.history.title")}</h3>
        <p className={listStyles.hint}>
          {t("adminPages.history.lastChange")} <LastChange change={page.lastChange} />
        </p>
        {historyQuery.data && historyQuery.data.length > 0 ? (
          <div className={listStyles.tableScroll}>
            <table className={listStyles.table}>
              <thead>
                <tr>
                  <th>{t("adminPages.history.when")}</th>
                  <th>{t("adminPages.history.who")}</th>
                  <th>{t("adminPages.history.what")}</th>
                </tr>
              </thead>
              <tbody>
                {historyQuery.data.map((revision) => (
                  <tr key={revision.id}>
                    <td className={styles.nowrap}>{new Date(revision.createdAt).toLocaleString(numberLocale())}</td>
                    <td>
                      {revision.author ? (
                        <>
                          {revision.author.name} <span className={listStyles.muted}>{revision.author.email}</span>
                        </>
                      ) : (
                        <span className={listStyles.muted}>{t("adminPages.deletedAccount")}</span>
                      )}
                    </td>
                    <td>
                      <Badge>{revisionLabel(t, revision.action, revision.locale)}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          historyQuery.isSuccess && <p className={listStyles.empty}>{t("adminPages.history.empty")}</p>
        )}
      </div>
    </div>
  );
}
