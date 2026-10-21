import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { settingsApi } from "@/api/settings";
import { MarkdownPage } from "@/components/MarkdownPage";
import { builtinLegalMarkdown } from "@/lib/legalTemplates";
import { pickTranslation, usePageLocale, usePublicPage } from "@/lib/pages";
import { PublicLayout } from "@/layouts/PublicLayout";

/** Screen — privacy policy (Datenschutzerklärung), required before any student data is processed. */
export function PrivacyPage() {
  const { t } = useTranslation();
  // The controller's contact details come from the same admin-editable site settings as the
  // imprint, so an operator fills them in once and both pages are correct. An admin can also
  // replace the whole page with their own Markdown (Dashboard → Admin → Pages).
  const settingsQuery = useQuery({ queryKey: ["settings", "public"], queryFn: settingsApi.getPublic });
  const pageLocale = usePageLocale();
  const pageQuery = usePublicPage("datenschutz");
  const custom = pickTranslation(pageQuery.data, pageLocale);

  // Nothing until it's known which of the two to show, rather than flashing the template.
  if (pageQuery.isPending) return <PublicLayout>{null}</PublicLayout>;
  return (
    <MarkdownPage
      title={custom?.title || t("privacy.title")}
      subtitle={custom ? undefined : t("privacy.subtitle")}
      contentMd={custom?.contentMd ?? builtinLegalMarkdown("privacy", pageLocale, settingsQuery.data)}
      updatedAt={custom?.updatedAt ?? new Date().toISOString()}
      footer={
        <p>
          <Link to="/impressum">{t("landing.footer.imprint")}</Link>
        </p>
      }
    />
  );
}
