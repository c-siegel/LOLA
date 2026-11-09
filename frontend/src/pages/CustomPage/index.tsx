import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { MarkdownPage } from "@/components/MarkdownPage";
import { NotFound } from "@/components/NotFound";
import { PublicLayout } from "@/layouts/PublicLayout";
import { pickTranslation, usePageLocale, usePublicPage } from "@/lib/pages";

/** Screen — a page an admin created (Dashboard → Admin → Pages), e.g. an accessibility
 * statement, at /p/<slug>. Unpublished and unknown pages look the same: not found. */
export function CustomPage() {
  const { t } = useTranslation();
  const { slug = "" } = useParams();
  const locale = usePageLocale();
  const pageQuery = usePublicPage(slug);
  const translation = pickTranslation(pageQuery.data, locale);

  if (pageQuery.isPending) return <PublicLayout>{null}</PublicLayout>;
  if (!translation) {
    return (
      <PublicLayout>
        <NotFound text={t("customPage.notFoundText")} />
      </PublicLayout>
    );
  }
  return <MarkdownPage title={translation.title} contentMd={translation.contentMd} updatedAt={translation.updatedAt} />;
}
