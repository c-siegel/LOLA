import { useCallback, useEffect, useLayoutEffect, useState } from "react";

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "lola_theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function readStoredTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    // Storage can be blocked (private mode); the theme then simply follows the system.
    return null;
  }
}

/**
 * Light/dark theme for the dashboard. Follows the OS setting until the teacher picks one with the
 * toggle; that choice is kept in localStorage only, like the language. The attribute goes on <html>
 * and is removed again on unmount, so the public chat and landing pages are never affected.
 */
export function useDashboardTheme() {
  const [stored, setStored] = useState<Theme | null>(readStoredTheme);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia(DARK_QUERY).matches);

  useEffect(() => {
    const query = window.matchMedia(DARK_QUERY);
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const theme: Theme = stored ?? (systemDark ? "dark" : "light");

  // Layout effect so the first paint already has the right colours.
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, [theme]);

  const toggleTheme = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setStored(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Not persisted; applies for this visit only.
    }
  }, [theme]);

  return { theme, toggleTheme };
}
