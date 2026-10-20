import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { MarkdownContent } from "@/components/MarkdownContent";
import { numberLocale } from "@/lib/format";
import { PublicLayout } from "@/layouts/PublicLayout";
import styles from "./MarkdownPage.module.css";

interface MarkdownPageProps {
  title: string;
  /** A line under the title; only the built-in imprint and privacy policy have one. */
  subtitle?: string;
  contentMd: string;
  /** When the shown text was last saved — the "as of" date a legal page needs. */
  updatedAt: string;
  /** Extra footer content, e.g. the link from the imprint to the privacy policy. */
  footer?: ReactNode;
}

/** A public page made of Markdown: the imprint and privacy policy (the built-in template or the
 * admin's replacement) and every custom page (e.g. an accessibility statement). */
export function MarkdownPage({ title, subtitle, contentMd, updatedAt, footer }: MarkdownPageProps) {
  const { t } = useTranslation();
  return (
    <PublicLayout>
      <article className={styles.container}>
        <header className={styles.header}>
          <h1>{title}</h1>
          {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
        </header>
        <MarkdownContent>{contentMd}</MarkdownContent>
        <footer className={styles.footer}>
          <p>{t("imprint.asOf", { date: new Date(updatedAt).toLocaleDateString(numberLocale()) })}</p>
          {footer}
        </footer>
      </article>
    </PublicLayout>
  );
}
