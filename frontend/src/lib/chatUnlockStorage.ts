// Tab-scoped storage for a public chat's unlock token (see pages/PublicChat/index.tsx) —
// sessionStorage instead of localStorage so a password-protected chat re-locks in a fresh tab,
// per the confirmed UX (unlock lasts for the current browser tab only).

const KEY_PREFIX = "lola:chat-unlock:";

export function getUnlockToken(slug: string): string | null {
  return sessionStorage.getItem(KEY_PREFIX + slug);
}

export function setUnlockToken(slug: string, token: string): void {
  sessionStorage.setItem(KEY_PREFIX + slug, token);
}
