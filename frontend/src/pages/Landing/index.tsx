import { useState } from "react";
import { useTranslation } from "react-i18next";
import { UserCog, Share2, SlidersHorizontal } from "lucide-react";
import { ButtonLink } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { MarkdownContent } from "@/components/MarkdownContent";
import { Wordmark } from "@/components/Wordmark";
import { TalkingHeadAvatar } from "@/components/TalkingHeadAvatar";
import { SiteFooter } from "@/components/SiteFooter";
import { PublicLayout } from "@/layouts/PublicLayout";
import { pickTranslation, usePageLocale, usePublicPage } from "@/lib/pages";
import styles from "./Landing.module.css";

// Shared between the desktop nav and the mobile drawer so both stay in sync.
const NAV_ITEMS = [
  //{ labelKey: "landing.nav.features", href: "#" },
  { labelKey: "landing.nav.sourceCode", href: "https://github.com/c-siegel/LOLA" },
];

// `full`/`short` give the two title variants shown at different breakpoints
// (see .featureTitleFull/.featureTitleShort in Landing.module.css).
const FEATURES = [
  { icon: UserCog, key: "configure" },
  { icon: Share2, key: "publish" },
  { icon: SlidersHorizontal, key: "flexibility" },
] as const;

export function LandingPage() {
  const { t } = useTranslation();
  // Controls the mobile nav drawer only; the desktop nav is always visible and
  // hidden/shown purely via the @media query in Landing.module.css.
  const [menuOpen, setMenuOpen] = useState(false);
  // An admin can replace any of the texts below and add a Markdown section (Dashboard → Admin →
  // Pages). Each field left empty keeps its built-in text; see lib/pages.ts for the language order.
  const landingQuery = usePublicPage("landing");
  const landing = pickTranslation(landingQuery.data, usePageLocale());
  const fields = landing?.fields ?? {};
  const text = (field: string, key: string) => fields[field] || t(key);
  // Hidden until loaded, so an instance with its own texts doesn't flash the built-in ones first.
  const copyClass = landingQuery.isPending ? styles.pending : undefined;

  return (
    <PublicLayout showLanguageSwitcher={false}>
      {/* Header: wordmark + desktop nav (hidden below 860px) + hamburger toggle (shown below 860px) */}
      <header className={styles.header}>
        <Wordmark />
        <nav className={styles.nav} aria-label={t("landing.nav.mainAriaLabel")}>
          <ul className={styles.navLinks}>
            {NAV_ITEMS.map((item) => (
              <li key={item.labelKey}>
                <a href={item.href} {...(item.href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
                  {t(item.labelKey)}
                </a>
              </li>
            ))}
          </ul>
          <div className={styles.headerActions}>
            <LanguageSwitcher />
            <ButtonLink to="/login">{t("common.login")}</ButtonLink>
            <ButtonLink to="/register" variant="accent">
              {t("landing.getStarted")}
            </ButtonLink>
          </div>
        </nav>
        {/* Hamburger button, CSS-only hidden on desktop; toggles the drawer below */}
        <button
          className={styles.menuButton}
          aria-label={t("landing.nav.openMenu")}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span />
          <span />
          <span />
        </button>
      </header>

      {/* Mobile nav drawer: same links/CTAs as the desktop nav, rendered only when toggled open */}
      {menuOpen && (
        <nav className={`${styles.mobileNav} ${styles.open}`} aria-label={t("landing.nav.mobileAriaLabel")}>
          {NAV_ITEMS.map((item) => (
            <a
              key={item.labelKey}
              href={item.href}
              {...(item.href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}
            >
              {t(item.labelKey)}
            </a>
          ))}
          <LanguageSwitcher />
          <ButtonLink to="/login">{t("common.login")}</ButtonLink>
          <ButtonLink to="/register" variant="accent" fullWidth>
            {t("landing.getStarted")}
          </ButtonLink>
        </nav>
      )}

      {/* Hero: two-column on desktop (copy + image placeholder), stacked on mobile via CSS grid */}
      <section className={styles.hero}>
        <div className={[styles.heroCopy, copyClass].filter(Boolean).join(" ")}>
          <Badge variant="accent">{text("badge", "landing.badge")}</Badge>
          <h1>
            {/* Two copy variants: the full headline on desktop, a shorter one on mobile
                (toggled purely via CSS display, see .headingFull/.headingShort) */}
            <span className={styles.headingFull}>{text("headingFull", "landing.headingFull")}</span>
            {/* An admin who only changed the full headline gets it on mobile too, rather than
                the built-in short one that no longer matches it. */}
            <span className={styles.headingShort}>{fields.headingShort || text("headingFull", "landing.headingShort")}</span>
          </h1>
          <p>{text("heroText", "landing.heroText")}</p>
          <div className={styles.heroActions}>
            <ButtonLink to="/register" variant="accent">
              {t("landing.getStarted")}
            </ButtonLink>
            <ButtonLink to="/login">{t("common.login")}</ButtonLink>
          </div>
        </div>
        {/* 3D avatar (met4citizen/TalkingHead) instead of a static image mockup; shows the text
            placeholder while the model loads, and on reduced-motion/errors. */}
        <div className={styles.heroImage} aria-hidden="true">
          <TalkingHeadAvatar fallback={t("landing.avatarLoading")} />
        </div>
      </section>

      {/* Feature row: 3 cards, stacked on mobile; icon + title (long/short) + one-line description */}
      <section className={styles.features}>
        <div className={[styles.featureGrid, copyClass].filter(Boolean).join(" ")}>
          {FEATURES.map((feature) => (
            <div className={styles.featureCard} key={feature.key}>
              <div className={styles.featureIcon} aria-hidden="true">
                <feature.icon size={20} strokeWidth={2} />
              </div>
              <h3>
                {/* An admin's title replaces both the long and the short variant. */}
                <span className={styles.featureTitleFull}>
                  {text(`${feature.key}Title`, `landing.features.${feature.key}.full`)}
                </span>
                <span className={styles.featureTitleShort}>
                  {text(`${feature.key}Title`, `landing.features.${feature.key}.short`)}
                </span>
              </h3>
              <p>{text(`${feature.key}Text`, `landing.features.${feature.key}.text`)}</p>
            </div>
          ))}
        </div>
      </section>

      {landing?.contentMd && (
        <section className={styles.extra}>
          <MarkdownContent className={styles.extraContent}>{landing.contentMd}</MarkdownContent>
        </section>
      )}

      <SiteFooter />
    </PublicLayout>
  );
}