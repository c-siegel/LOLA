import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { Badge } from "@/components/Badge";
import { Button, ButtonLink } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Input } from "@/components/Input";
import { PageHeader } from "@/components/PageHeader";
import { errorMessage } from "@/api/client";
import { adminPagesApi, PAGE_LOCALES, type AdminPage } from "@/api/pages";
import { pickTitle, usePageLocale } from "@/lib/pages";
import { LastChange } from "./LastChange";
import styles from "./Pages.module.css";

/** Which languages have the admin's own text, e.g. "DE · EN" with the missing ones dimmed. */
function LocaleStatus({ page }: { page: AdminPage }) {
  const { t } = useTranslation();
  const written = new Set(page.translations.map((translation) => translation.locale));
  return (
    <span className={styles.locales}>
      {PAGE_LOCALES.map((locale) => (
        <span
          key={locale}
          className={written.has(locale) ? styles.localeWritten : styles.localeMissing}
          title={t(written.has(locale) ? "adminPages.localeWritten" : "adminPages.localeMissing")}
        >
          {locale.toUpperCase()}
        </span>
      ))}
    </span>
  );
}

/** Admin dashboard: the editable public pages — landing page, imprint, privacy policy — and the
 * extra pages an admin creates, such as an accessibility statement. */
export function AdminPagesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const locale = usePageLocale();
  const pagesQuery = useQuery({ queryKey: ["admin", "pages"], queryFn: adminPagesApi.list });
  const pages = pagesQuery.data ?? [];
  const builtinPages = pages.filter((page) => page.kind !== "custom");
  const customPages = pages.filter((page) => page.kind === "custom");

  const [slug, setSlug] = useState("");
  const createMutation = useMutation({
    mutationFn: () => adminPagesApi.create({ slug: slug.trim() }),
    onSuccess: (page) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "pages"] });
      navigate(`/dashboard/admin/pages/${page.id}`);
    },
  });

  function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!slug.trim() || createMutation.isPending) return;
    createMutation.mutate();
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("adminPages.title")} subtitle={t("adminPages.subtitle")} />

      {pagesQuery.isError && <Callout variant="danger">{t("adminPages.loadError")}</Callout>}

      <div className={styles.card}>
        <h3>{t("adminPages.builtinTitle")}</h3>
        <p className={styles.hint}>{t("adminPages.builtinHint")}</p>
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{t("adminPages.columns.page")}</th>
                <th>{t("adminPages.columns.ownText")}</th>
                <th>{t("adminPages.columns.lastChange")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {builtinPages.map((page) => (
                <tr key={page.id}>
                  <td>
                    {/* Also a link: on a phone the table scrolls and the Edit button starts off-screen. */}
                    <Link to={`/dashboard/admin/pages/${page.id}`} className={styles.pageLink}>
                      {t(`adminPages.kinds.${page.kind}`)}
                    </Link>
                  </td>
                  <td>
                    <LocaleStatus page={page} />
                  </td>
                  <td>
                    <LastChange change={page.lastChange} />
                  </td>
                  <td className={styles.actions}>
                    <ButtonLink size="sm" to={`/dashboard/admin/pages/${page.id}`}>
                      {t("adminPages.edit")}
                    </ButtonLink>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className={styles.card}>
        <h3>{t("adminPages.customTitle")}</h3>
        <p className={styles.hint}>{t("adminPages.customHint")}</p>

        {customPages.length > 0 ? (
          <div className={styles.tableScroll}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>{t("adminPages.columns.page")}</th>
                  <th>{t("adminPages.columns.status")}</th>
                  <th>{t("adminPages.columns.ownText")}</th>
                  <th>{t("adminPages.columns.lastChange")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {customPages.map((page) => {
                  const titles = Object.fromEntries(page.translations.map((tr) => [tr.locale, tr.title]));
                  return (
                    <tr key={page.id}>
                      <td>
                        <Link to={`/dashboard/admin/pages/${page.id}`} className={styles.pageLink}>
                          {pickTitle(titles, locale) || t("adminPages.untitled")}
                        </Link>
                        <code className={styles.slug}>/p/{page.slug}</code>
                      </td>
                      <td>
                        <Badge variant={page.isPublished ? "accent" : "default"}>
                          {t(page.isPublished ? "adminPages.published" : "adminPages.draft")}
                        </Badge>
                      </td>
                      <td>
                        <LocaleStatus page={page} />
                      </td>
                      <td>
                        <LastChange change={page.lastChange} />
                      </td>
                      <td className={styles.actions}>
                        <ButtonLink size="sm" to={`/dashboard/admin/pages/${page.id}`}>
                          {t("adminPages.edit")}
                        </ButtonLink>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          pagesQuery.isSuccess && <p className={styles.empty}>{t("adminPages.noCustomPages")}</p>
        )}

        <form className={styles.createForm} onSubmit={handleCreate}>
          <Input
            label={t("adminPages.slugLabel")}
            placeholder={t("adminPages.slugPlaceholder")}
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            info={t("adminPages.slugHint")}
          />
          <Button type="submit" variant="accent" disabled={!slug.trim() || createMutation.isPending}>
            {t("adminPages.create")}
          </Button>
        </form>
        {createMutation.isError && (
          <Callout variant="danger">{errorMessage(createMutation.error, t("adminPages.createError"))}</Callout>
        )}
      </div>
    </div>
  );
}
