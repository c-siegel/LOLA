import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { ButtonLink } from "@/components/Button";
import styles from "./NotFound.module.css";

interface NotFoundProps {
  /** Replaces the generic explanation, e.g. for a custom page that may only be unpublished. */
  text?: string;
  /** Where the button leads: the home page, or the overview for an address inside the dashboard. */
  backTo?: "home" | "dashboard";
}

/** "Page not found" for an address no route or page matches. */
export function NotFound({ text, backTo = "home" }: NotFoundProps) {
  const { t } = useTranslation();

  // The SPA answers every address with 200 and index.html, so a search engine would index this as
  // a page of its own ("soft 404"); the robots meta tag is what keeps it out.
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);

  return (
    <div className={styles.notFound}>
      <p className={styles.code} aria-hidden="true">
        404
      </p>
      <h1>{t("notFound.title")}</h1>
      <p>{text ?? t("notFound.text")}</p>
      {backTo === "dashboard" ? (
        <ButtonLink to="/dashboard">{t("notFound.backToDashboard")}</ButtonLink>
      ) : (
        <ButtonLink to="/">{t("notFound.backHome")}</ButtonLink>
      )}
    </div>
  );
}
