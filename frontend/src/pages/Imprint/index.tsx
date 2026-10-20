import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { settingsApi } from "@/api/settings";
import { MarkdownPage } from "@/components/MarkdownPage";
import { builtinLegalMarkdown } from "@/lib/legalTemplates";
import { pickTranslation, usePageLocale, usePublicPage } from "@/lib/pages";
import { PublicLayout } from "@/layouts/PublicLayout";

// Screen — legal notice (Impressum)
export function ImprintPage() {
  const { t } = useTranslation();
  // The template's details are admin-editable site settings (Dashboard → Admin → Site settings),
  // so a self-hoster can produce a valid imprint without touching the source. An admin can also
  // replace the whole page with their own Markdown (Dashboard → Admin → Pages).
  const settingsQuery = useQuery({ queryKey: ["settings", "public"], queryFn: settingsApi.getPublic });
  const pageLocale = usePageLocale();
  const pageQuery = usePublicPage("impressum");
  const custom = pickTranslation(pageQuery.data, pageLocale);

  // Nothing until it's known which of the two to show, rather than flashing the template.
  if (pageQuery.isPending) return <PublicLayout>{null}</PublicLayout>;
  return (
    <MarkdownPage
      title={custom?.title || t("imprint.title")}
      subtitle={custom ? undefined : t("imprint.subtitle")}
      contentMd={custom?.contentMd ?? builtinLegalMarkdown("imprint", pageLocale, settingsQuery.data)}
      updatedAt={custom?.updatedAt ?? new Date().toISOString()}
      footer={
        <p>
          <Link to="/datenschutz">{t("landing.footer.privacy")}</Link>
        </p>
      }
    />
  );
}
