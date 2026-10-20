// The built-in imprint and privacy policy (pages/Imprint, pages/Privacy) as Markdown, so an admin
// who replaces a page starts from the current text with the site settings' details filled in,
// rather than from a blank editor. Keep in step with those two pages when their sections change.
import type { TFunction } from "i18next";
import i18n from "@/i18n";
import type { PageLocale } from "@/api/pages";
import type { PublicSiteSettings } from "@/api/settings";

// The translation texts are plain prose; escape what Markdown would otherwise read as syntax.
function escapeMarkdown(text: string): string {
  return text.replace(/([\\`*_[\]<>#|])/g, "\\$1");
}

function paragraphs(...texts: string[]): string {
  return texts.map(escapeMarkdown).join("\n\n");
}

function section(title: string, body: string): string {
  return `## ${escapeMarkdown(title)}\n\n${body}`;
}

function imprint(t: TFunction, settings: PublicSiteSettings | undefined): string {
  const placeholder = t("imprint.placeholder");
  const line = (label: string, value: string | null | undefined, fallback = placeholder) =>
    `**${escapeMarkdown(label)}** ${escapeMarkdown(value || fallback)}`;
  const email = line(t("imprint.contact.email"), settings?.contactEmail);
  return [
    section(
      t("imprint.provider.title"),
      [
        line(t("imprint.provider.name"), settings?.providerName),
        line(t("imprint.provider.street"), settings?.providerStreet),
        line(t("imprint.provider.city"), settings?.providerCity),
        line(t("imprint.provider.country"), settings?.providerCountry, t("imprint.provider.countryValue")),
      ].join("  \n"),
    ),
    section(
      t("imprint.contact.title"),
      [email, line(t("imprint.contact.phone"), settings?.contactPhone, t("imprint.contact.phonePlaceholder"))].join("  \n"),
    ),
    section(t("imprint.responsible.title"), [line(t("imprint.provider.name"), settings?.providerName), email].join("  \n")),
    section(t("imprint.liability.contentTitle"), paragraphs(t("imprint.liability.contentP1"), t("imprint.liability.contentP2"))),
    section(t("imprint.liability.linksTitle"), paragraphs(t("imprint.liability.linksP1"), t("imprint.liability.linksP2"))),
  ].join("\n\n");
}

function privacy(t: TFunction, settings: PublicSiteSettings | undefined): string {
  const placeholder = t("imprint.placeholder");
  const address = [settings?.providerName, settings?.providerStreet, settings?.providerCity, settings?.providerCountry]
    .map((value) => escapeMarkdown(value || placeholder))
    .join("  \n");
  const email = `**${escapeMarkdown(t("imprint.contact.email"))}** ${escapeMarkdown(settings?.contactEmail || placeholder)}`;
  return [
    section(t("privacy.controller.title"), [escapeMarkdown(t("privacy.controller.text")), address, email].join("\n\n")),
    section(t("privacy.teachers.title"), paragraphs(t("privacy.teachers.text"), t("privacy.teachers.legalBasis"))),
    section(
      t("privacy.students.title"),
      paragraphs(
        t("privacy.students.noAccount"),
        t("privacy.students.cookie"),
        t("privacy.students.recording"),
        t("privacy.students.visitorName"),
      ),
    ),
    section(t("privacy.ai.title"), paragraphs(t("privacy.ai.text"), t("privacy.ai.speech"))),
    section(t("privacy.retention.title"), paragraphs(t("privacy.retention.text"))),
    section(
      t("privacy.rights.title"),
      paragraphs(t("privacy.rights.text"), t("privacy.rights.students"), t("privacy.rights.complaint")),
    ),
  ].join("\n\n");
}

/** The built-in page in `locale` as Markdown, filled in with the site settings where known. */
export function builtinLegalMarkdown(
  kind: "imprint" | "privacy",
  locale: PageLocale,
  settings: PublicSiteSettings | undefined,
): string {
  const t = i18n.getFixedT(locale);
  return (kind === "imprint" ? imprint(t, settings) : privacy(t, settings)) + "\n";
}
