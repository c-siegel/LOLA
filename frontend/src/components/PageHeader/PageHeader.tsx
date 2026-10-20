import { HeadingWithInfo } from "@/components/InfoTip";
import styles from "./PageHeader.module.css";

interface PageHeaderProps {
  title: string;
  /** Shown under the title. */
  subtitle?: string;
  /** Behind an (i) next to the title instead, for pages whose intro would crowd the content. */
  info?: string;
}

/** The title block at the top of a dashboard page. */
export function PageHeader({ title, subtitle, info }: PageHeaderProps) {
  if (info) return <HeadingWithInfo as="h2" className={styles.header} title={title} info={info} />;
  return (
    <div className={styles.header}>
      <h2>{title}</h2>
      {subtitle && <p>{subtitle}</p>}
    </div>
  );
}
