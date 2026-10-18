import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  AudioLines,
  BookOpen,
  Gauge,
  LayoutDashboard,
  Timer,
  ScrollText,
  BarChart3,
  KeyRound,
  Languages,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Menu,
  X,
  LogOut,
  Moon,
  Sun,
  type LucideIcon,
} from "lucide-react";
import { NavItem } from "@/components/NavItem";
import { Scrim } from "@/components/Drawer";
import { Wordmark } from "@/components/Wordmark";
import { Avatar } from "@/components/Avatar";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { authApi } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { useDashboardTheme } from "@/hooks/useDashboardTheme";
import { useEvaluationStatus, useKnowledgeStatus } from "@/lib/providers";
import styles from "./DashboardShell.module.css";

const FORCE_PASSWORD_CHANGE_PATH = "/dashboard/change-password-required";

interface NavConfigItem {
  labelKey: string;
  href: string;
  icon: LucideIcon;
  isActive: (pathname: string) => boolean;
}

const NAV_ITEMS = {
  overview: { labelKey: "nav.overview", href: "/dashboard", icon: LayoutDashboard, isActive: (p) => p === "/dashboard" },
  analytics: {
    labelKey: "nav.analytics",
    href: "/dashboard/analytics",
    icon: BarChart3,
    isActive: (p) => p.startsWith("/dashboard/analytics"),
  },
  apiDashboard: {
    labelKey: "nav.apiDashboard",
    href: "/dashboard/api",
    icon: KeyRound,
    isActive: (p) => p.startsWith("/dashboard/api"),
  },
  voices: {
    labelKey: "nav.voices",
    href: "/dashboard/voices",
    icon: AudioLines,
    isActive: (p) => p.startsWith("/dashboard/voices"),
  },
  pronunciation: {
    labelKey: "nav.pronunciation",
    href: "/dashboard/pronunciation",
    icon: Languages,
    isActive: (p) => p.startsWith("/dashboard/pronunciation"),
  },
  latencyLab: {
    labelKey: "nav.latencyLab",
    href: "/dashboard/latency",
    icon: Timer,
    isActive: (p) => p.startsWith("/dashboard/latency"),
  },
  motionPrompt: {
    labelKey: "nav.motionPrompt",
    href: "/dashboard/motion-prompt",
    icon: ScrollText,
    isActive: (p) => p.startsWith("/dashboard/motion-prompt"),
  },
  profile: {
    labelKey: "nav.profile",
    href: "/dashboard/profile",
    icon: Settings2,
    isActive: (p) => p.startsWith("/dashboard/profile"),
  },
  // Only shown where the deployment runs the knowledge service (see useKnowledgeStatus).
  knowledge: {
    labelKey: "nav.knowledge",
    href: "/dashboard/knowledge",
    icon: BookOpen,
    isActive: (p) => p.startsWith("/dashboard/knowledge"),
  },
  // Only shown where the deployment also runs the evaluation service (see useEvaluationStatus).
  evaluation: {
    labelKey: "nav.evaluation",
    href: "/dashboard/evaluation",
    icon: Gauge,
    isActive: (p) => p.startsWith("/dashboard/evaluation"),
  },
  admin: {
    labelKey: "nav.adminUsers",
    href: "/dashboard/admin",
    icon: ShieldCheck,
    isActive: (p) => p === "/dashboard/admin",
  },
  adminSettings: {
    labelKey: "nav.adminSettings",
    href: "/dashboard/admin/settings",
    icon: SlidersHorizontal,
    isActive: (p) => p.startsWith("/dashboard/admin/settings"),
  },
} satisfies Record<string, NavConfigItem>;

interface NavGroup {
  id: string;
  labelKey: string;
  items: NavConfigItem[];
}

// Layout for 1c–1h — sidebar (desktop) or hamburger + overlay drawer (<1024px) + content area
export function DashboardShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const { data: user, error } = useCurrentUser();
  const { theme, toggleTheme } = useDashboardTheme();

  // Auth guard: only a confirmed 401 sends teachers back to /login. A network/5xx error
  // (e.g. backend not running yet during frontend-only work) just falls back to "…" below,
  // instead of bouncing the user away from a page they were legitimately allowed to see.
  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      navigate("/login", { replace: true });
    }
  }, [error, navigate]);

  // Forced password change (see User.must_change_password): blocks every other dashboard route
  // until it's cleared, same idea as the 401 guard above but for "logged in, can't proceed yet".
  useEffect(() => {
    if (user?.mustChangePassword && location.pathname !== FORCE_PASSWORD_CHANGE_PATH) {
      navigate(FORCE_PASSWORD_CHANGE_PATH, { replace: true });
    }
  }, [user, location.pathname, navigate]);

  // Close the mobile drawer on every route change (e.g. after tapping a nav item).
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  // Dismissing the drawer (Escape, scrim) hands focus back to the menu button that opened it, so
  // keyboard users don't drop to the top of the page.
  function closeMobileNav() {
    setMobileNavOpen(false);
    menuButtonRef.current?.focus();
  }

  // Opening moves focus into the drawer, so the next Tab lands on a nav link instead of the page
  // behind the scrim.
  useEffect(() => {
    if (mobileNavOpen) sidebarRef.current?.querySelector("a")?.focus();
  }, [mobileNavOpen]);

  // Escape closes the open drawer, as with any overlay.
  useEffect(() => {
    if (!mobileNavOpen) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeMobileNav();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [mobileNavOpen]);

  const knowledgeAvailable = useKnowledgeStatus().data?.available ?? false;
  const evaluationAvailable = useEvaluationStatus({ enabled: knowledgeAvailable }).data?.available ?? false;
  // Sidebar sections: what teachers build (including their libraries), what they learn from it,
  // and the one-time setup around it. Empty sections are dropped.
  const navGroups: NavGroup[] = [
    {
      id: "create",
      labelKey: "nav.groups.create",
      items: [
        NAV_ITEMS.overview,
        NAV_ITEMS.voices,
        ...(knowledgeAvailable ? [NAV_ITEMS.knowledge] : []),
        NAV_ITEMS.pronunciation,
      ],
    },
    {
      id: "insights",
      labelKey: "nav.groups.insights",
      items: [NAV_ITEMS.analytics, ...(knowledgeAvailable && evaluationAvailable ? [NAV_ITEMS.evaluation] : []), NAV_ITEMS.latencyLab, NAV_ITEMS.motionPrompt],
    },
    { id: "setup", labelKey: "nav.groups.setup", items: [NAV_ITEMS.apiDashboard, NAV_ITEMS.profile] },
    ...(user?.isAdmin ? [{ id: "admin", labelKey: "nav.groups.admin", items: [NAV_ITEMS.admin, NAV_ITEMS.adminSettings] }] : []),
  ];
  const activeItem = navGroups.flatMap((group) => group.items).find((item) => item.isActive(location.pathname));
  const activeLabel = activeItem ? t(activeItem.labelKey) : t("nav.dashboardFallback");

  async function handleLogout() {
    await authApi.logout().catch(() => undefined);
    queryClient.removeQueries({ queryKey: ["auth", "me"] });
    navigate("/login");
  }

  const sidebar = (
    <aside ref={sidebarRef} className={`${styles.sidebar} ${mobileNavOpen ? styles.sidebarOpen : ""}`}>
      <div className={styles.sidebarHeader}>
        <Wordmark />
      </div>
      <nav className={styles.nav} aria-label={t("nav.dashboardNavAriaLabel")}>
        {navGroups.map((group) => (
          <div key={group.id} role="group" aria-labelledby={`nav-group-${group.id}`} className={styles.navGroup}>
            <p id={`nav-group-${group.id}`} className={styles.navGroupLabel}>
              {t(group.labelKey)}
            </p>
            {group.items.map((item) => (
              <NavItem key={item.labelKey} to={item.href} icon={item.icon} active={item.isActive(location.pathname)}>
                {t(item.labelKey)}
              </NavItem>
            ))}
          </div>
        ))}
      </nav>
      <div className={styles.sidebarFooter}>
        <div className={styles.userRow}>
          <Avatar name={user?.name ?? ""} src={user?.avatarUrl ?? undefined} size="sm" />
          <div className={styles.userInfo}>
            <span className={styles.userName}>{user?.name ?? "…"}</span>
            <span className={styles.userSchool}>{user?.school ?? ""}</span>
          </div>
        </div>
        <div className={styles.languageSwitcherRow}>
          <LanguageSwitcher />
          <button
            className={styles.themeButton}
            onClick={toggleTheme}
            aria-label={theme === "dark" ? t("nav.themeToLight") : t("nav.themeToDark")}
            title={theme === "dark" ? t("nav.themeToLight") : t("nav.themeToDark")}
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
        <button className={styles.logoutButton} onClick={handleLogout}>
          <LogOut size={16} strokeWidth={2} />
          {t("nav.logout")}
        </button>
      </div>
    </aside>
  );

  return (
    <div className={styles.shell}>
      <header className={styles.mobileTopbar}>
        <button
          ref={menuButtonRef}
          className={styles.menuButton}
          aria-label={mobileNavOpen ? t("nav.closeMenu") : t("nav.openMenu")}
          aria-expanded={mobileNavOpen}
          onClick={() => setMobileNavOpen((open) => !open)}
        >
          {mobileNavOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
        <h3 className={styles.mobileTitle}>{activeLabel}</h3>
        <Avatar name={user?.name ?? ""} size="sm" />
      </header>

      {mobileNavOpen && <Scrim onClick={closeMobileNav} />}

      {sidebar}

      <main className={styles.content}>{children}</main>
    </div>
  );
}
