// Which admin-written text a public page shows (see backend features/pages). The API returns
// every language at once, so switching the UI language picks again without a new request.
//
// Order: the visitor's language, then English, then the built-in template from the translation
// files. On the landing page the language is picked once for the whole page, then each field the
// admin left empty falls back to the built-in text — so a partly filled page doesn't mix in a
// second language beyond what the admin wrote.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/client";
import { pagesApi, type PageLocale, type PageTranslation, type PublicPage } from "@/api/pages";

export const FALLBACK_PAGE_LOCALE: PageLocale = "en";

/** The UI language as a page locale ("en-US" from the browser counts as "en"). */
export function usePageLocale(): PageLocale {
  const { i18n } = useTranslation();
  return i18n.resolvedLanguage === "en" ? "en" : "de";
}

/** A public page's texts; `data` stays undefined on a 404 (an unknown or unpublished page). */
export function usePublicPage(slug: string) {
  return useQuery({
    queryKey: ["pages", "public", slug],
    queryFn: () => pagesApi.getPublic(slug),
    // A missing page won't appear by retrying; anything else (network) gets the usual retries.
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    staleTime: 60_000,
  });
}

/** The published custom pages linked from the footer. */
export function useFooterPages() {
  return useQuery({ queryKey: ["pages", "footer"], queryFn: pagesApi.listFooter, staleTime: 60_000 });
}

function isUsable(page: PublicPage, translation: PageTranslation): boolean {
  // The imprint and privacy policy are either wholly the admin's Markdown or wholly the
  // template, so a language without Markdown doesn't count as written.
  if (page.kind === "imprint" || page.kind === "privacy") return translation.contentMd.trim() !== "";
  return true;
}

/** The admin's text the visitor should see, or undefined for "use the built-in template". */
export function pickTranslation(page: PublicPage | undefined, locale: PageLocale): PageTranslation | undefined {
  if (!page) return undefined;
  const order: PageLocale[] = [locale, FALLBACK_PAGE_LOCALE];
  for (const candidate of order) {
    const translation = page.translations[candidate];
    if (translation && isUsable(page, translation)) return translation;
  }
  // A custom page has no template, so any language beats showing nothing.
  if (page.kind === "custom") return Object.values(page.translations).find(Boolean);
  return undefined;
}

/** The same order for a footer link label. */
export function pickTitle(titles: Partial<Record<PageLocale, string>>, locale: PageLocale): string {
  return titles[locale] || titles[FALLBACK_PAGE_LOCALE] || Object.values(titles).find(Boolean) || "";
}
