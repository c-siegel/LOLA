// Tab-scoped storage for a public chat's visitor name/ID (see pages/PublicChat/index.tsx) —
// sessionStorage instead of localStorage, same reasoning as chatUnlockStorage.ts: the name-gate
// form re-appears in a fresh tab instead of silently reusing whatever was typed last time.

import { clearSessionKeys } from "@/lib/chatUnlockStorage";

const KEY_PREFIX = "lola:visitor-name:";

export function getVisitorName(slug: string): string | null {
  return sessionStorage.getItem(KEY_PREFIX + slug);
}

export function setVisitorName(slug: string, name: string): void {
  sessionStorage.setItem(KEY_PREFIX + slug, name);
}

/** Forgets every entered name in this tab, for the same reason as clearUnlockTokens. */
export function clearVisitorNames(): void {
  clearSessionKeys(KEY_PREFIX);
}
