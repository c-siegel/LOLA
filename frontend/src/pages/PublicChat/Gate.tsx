// The screens in front of a protected public chat: the project's password, then the visitor's
// name if the project asks for one. Each screen keeps its own input; the page only needs to know
// whether the visitor is through (see isLocked/gatePassed), since loading the speech models and
// the chat itself wait for that.

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Lock, User } from "lucide-react";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Input } from "@/components/Input";
import { errorMessage } from "@/api/client";
import { publicChatApi, type PublicProject } from "@/api/publicChat";
import { setUnlockToken } from "@/lib/chatUnlockStorage";
import { setVisitorName } from "@/lib/visitorNameStorage";
import styles from "./PublicChat.module.css";

/** Password-protected and not unlocked yet. Such a response carries no layout or content yet. */
export function isLocked(tutor: PublicProject): boolean {
  return tutor.passwordProtected && !tutor.unlocked;
}

/** Through both the password and the name prompt (whichever the project has). */
export function gatePassed(tutor: PublicProject, visitorNameProvided: boolean): boolean {
  return !isLocked(tutor) && !(tutor.requireVisitorName && !visitorNameProvided);
}

export function PasswordGate({ slug }: { slug: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const unlockMutation = useMutation({
    mutationFn: (value: string) => publicChatApi.unlock(slug, value),
    onSuccess: (res) => {
      setUnlockToken(slug, res.unlockToken);
      setPassword("");
      // Refetches loadTutor with the now-stored token attached, which flips `unlocked` and
      // reveals the real stage instead of the lock screen.
      queryClient.invalidateQueries({ queryKey: ["public-chat", slug] });
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!password.trim() || unlockMutation.isPending) return;
    unlockMutation.mutate(password);
  }

  return (
    <div className={styles.centered}>
      <form className={styles.lockedForm} onSubmit={handleSubmit}>
        <Lock size={28} />
        <p className={styles.lockedText}>{t("publicChat.locked.description")}</p>
        <Input
          label={t("publicChat.locked.passwordLabel")}
          type="password"
          autoComplete="off"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {unlockMutation.isError && (
          <Callout variant="danger">{errorMessage(unlockMutation.error, t("publicChat.locked.genericError"))}</Callout>
        )}
        <Button type="submit" variant="accent" fullWidth disabled={unlockMutation.isPending}>
          {t("publicChat.locked.submit")}
        </Button>
      </form>
    </div>
  );
}

// Nothing to verify server-side (see PublicProject.requireVisitorName's comment in
// api/publicChat.ts): storing the trimmed name is the whole flow, no round-trip like the password.
export function NameGate({ slug, onProvided }: { slug: string; onProvided: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setVisitorName(slug, trimmed);
    onProvided();
  }

  return (
    <div className={styles.centered}>
      <form className={styles.lockedForm} onSubmit={handleSubmit}>
        <User size={28} />
        <p className={styles.lockedText}>{t("publicChat.nameGate.description")}</p>
        <Input
          label={t("publicChat.nameGate.nameLabel")}
          type="text"
          autoComplete="off"
          required
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Button type="submit" variant="accent" fullWidth>
          {t("publicChat.nameGate.submit")}
        </Button>
      </form>
    </div>
  );
}
