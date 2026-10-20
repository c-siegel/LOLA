import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { settingsApi } from "@/api/settings";
import { MarkdownPage } from "@/components/MarkdownPage";
import { numberLocale } from "@/lib/format";
import { pickTranslation, usePageLocale, usePublicPage } from "@/lib/pages";
import { PublicLayout } from "@/layouts/PublicLayout";
import styles from "./Privacy.module.css";

/** Screen — privacy policy (Datenschutzerklärung), required before any student data is processed. */
export function PrivacyPage() {
  const { t } = useTranslation();
  // The controller's contact details come from the same admin-editable site settings as the
  // imprint, so an operator fills them in once and both pages are correct.
  const settingsQuery = useQuery({ queryKey: ["settings", "public"], queryFn: settingsApi.getPublic });
  const settings = settingsQuery.data;
  const placeholder = t("imprint.placeholder");
  // An admin can replace the whole page with their own Markdown (Dashboard → Admin → Pages);
  // the template below is what an instance shows until then.
  const pageLocale = usePageLocale();
  const pageQuery = usePublicPage("datenschutz");
  const custom = pickTranslation(pageQuery.data, pageLocale);

  // Nothing until it's known which of the two to show, rather than flashing the template.
  if (pageQuery.isPending) return <PublicLayout>{null}</PublicLayout>;
  if (custom) {
    return (
      <MarkdownPage
        title={custom.title || t("privacy.title")}
        contentMd={custom.contentMd}
        updatedAt={custom.updatedAt}
        footer={
          <p>
            <Link to="/impressum">{t("landing.footer.imprint")}</Link>
          </p>
        }
      />
    );
  }

  return (
    <PublicLayout>
      <div className={styles.container}>
        <header className={styles.header}>
          <h1>{t("privacy.title")}</h1>
          <p className={styles.subtitle}>{t("privacy.subtitle")}</p>
        </header>

        <section className={styles.section}>
          <h2>{t("privacy.controller.title")}</h2>
          <div className={styles.content}>
            <p>{t("privacy.controller.text")}</p>
            <p>
              {settings?.providerName || placeholder}
              <br />
              {settings?.providerStreet || placeholder}
              <br />
              {settings?.providerCity || placeholder}
              <br />
              {settings?.providerCountry || placeholder}
            </p>
            <p>
              <strong>{t("imprint.contact.email")}</strong>{" "}
              {settings?.contactEmail ? (
                <a href={`mailto:${settings.contactEmail}`}>{settings.contactEmail}</a>
              ) : (
                placeholder
              )}
            </p>
          </div>
        </section>

        <section className={styles.section}>
          <h2>{t("privacy.teachers.title")}</h2>
          <div className={styles.content}>
            <p>{t("privacy.teachers.text")}</p>
            <p>{t("privacy.teachers.legalBasis")}</p>
          </div>
        </section>

        <section className={styles.section}>
          <h2>{t("privacy.students.title")}</h2>
          <div className={styles.content}>
            <p>{t("privacy.students.noAccount")}</p>
            <p>{t("privacy.students.cookie")}</p>
            <p>{t("privacy.students.recording")}</p>
            <p>{t("privacy.students.visitorName")}</p>
          </div>
        </section>

        <section className={styles.section}>
          <h2>{t("privacy.ai.title")}</h2>
          <div className={styles.content}>
            <p>{t("privacy.ai.text")}</p>
            <p>{t("privacy.ai.speech")}</p>
          </div>
        </section>

        <section className={styles.section}>
          <h2>{t("privacy.retention.title")}</h2>
          <div className={styles.content}>
            <p>{t("privacy.retention.text")}</p>
          </div>
        </section>

        <section className={styles.section}>
          <h2>{t("privacy.rights.title")}</h2>
          <div className={styles.content}>
            <p>{t("privacy.rights.text")}</p>
            <p>{t("privacy.rights.students")}</p>
            <p>{t("privacy.rights.complaint")}</p>
          </div>
        </section>

        <footer className={styles.footer}>
          <p>{t("imprint.asOf", { date: new Date().toLocaleDateString(numberLocale()) })}</p>
          <p>
            <Link to="/impressum">{t("landing.footer.imprint")}</Link>
          </p>
        </footer>
      </div>
    </PublicLayout>
  );
}
