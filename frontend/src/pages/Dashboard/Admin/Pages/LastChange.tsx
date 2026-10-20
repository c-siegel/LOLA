import { useTranslation } from "react-i18next";
import type { AdminPage } from "@/api/pages";
import { numberLocale } from "@/lib/format";
import styles from "./Pages.module.css";

/** "18.10.2026, 14:03 · Ada Admin" — or a dash for a page nobody has edited yet. */
export function LastChange({ change }: { change: AdminPage["lastChange"] }) {
  const { t } = useTranslation();
  if (!change) return <span className={styles.muted}>{t("adminPages.neverChanged")}</span>;
  return (
    <span>
      {new Date(change.at).toLocaleString(numberLocale())}
      <span className={styles.muted}> · {change.author?.name ?? t("adminPages.deletedAccount")}</span>
    </span>
  );
}
