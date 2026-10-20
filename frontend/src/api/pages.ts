// API client for the admin-editable public pages: the public /pages/* routes (landing page texts,
// imprint, privacy policy, custom pages and the footer links to them) and the admin-only
// /admin/pages/* editor routes.
import { apiClient } from "./client";

export type PageLocale = "de" | "en";
export type PageKind = "landing" | "imprint" | "privacy" | "custom";

export const PAGE_LOCALES: PageLocale[] = ["de", "en"];

export interface PageTranslation {
  title: string;
  contentMd: string;
  fields: Record<string, string>;
  updatedAt: string;
}

export interface PublicPage {
  slug: string;
  kind: PageKind;
  // Only the languages an admin has written text in; see lib/pages.ts for the fallback order.
  translations: Partial<Record<PageLocale, PageTranslation>>;
}

export interface FooterPage {
  slug: string;
  titles: Partial<Record<PageLocale, string>>;
}

export interface PageAuthor {
  name: string;
  email: string;
}

export interface AdminPage {
  id: string;
  kind: PageKind;
  slug: string;
  isPublished: boolean;
  showInFooter: boolean;
  footerOrder: number;
  translations: (PageTranslation & { locale: PageLocale })[];
  // author is null once the account that made the change has been deleted.
  lastChange: { at: string; author: PageAuthor | null } | null;
}

export type PageRevisionAction = "created" | "content_saved" | "content_reset" | "settings_changed";

export interface PageRevision {
  id: string;
  action: PageRevisionAction;
  locale: PageLocale | null;
  createdAt: string;
  author: PageAuthor | null;
}

export interface PageSettings {
  slug: string;
  isPublished: boolean;
  showInFooter: boolean;
  footerOrder: number;
}

export interface TranslationInput {
  title: string;
  contentMd: string;
  fields: Record<string, string>;
}

export const pagesApi = {
  getPublic: (slug: string) => apiClient.get<PublicPage>(`/pages/${encodeURIComponent(slug)}`),
  listFooter: () => apiClient.get<FooterPage[]>("/pages"),
};

export const adminPagesApi = {
  list: () => apiClient.get<AdminPage[]>("/admin/pages"),
  get: (id: string) => apiClient.get<AdminPage>(`/admin/pages/${id}`),
  create: (data: Partial<PageSettings> & { slug: string }) => apiClient.post<AdminPage>("/admin/pages", data),
  update: (id: string, data: Partial<PageSettings>) => apiClient.patch<AdminPage>(`/admin/pages/${id}`, data),
  remove: (id: string) => apiClient.delete<void>(`/admin/pages/${id}`),
  saveTranslation: (id: string, locale: PageLocale, data: TranslationInput) =>
    apiClient.put<AdminPage>(`/admin/pages/${id}/translations/${locale}`, data),
  resetTranslation: (id: string, locale: PageLocale) =>
    apiClient.delete<AdminPage>(`/admin/pages/${id}/translations/${locale}`),
  history: (id: string) => apiClient.get<PageRevision[]>(`/admin/pages/${id}/history`),
};
