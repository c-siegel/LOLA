import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Input } from "@/components/Input";
import { profileApi } from "@/api/profile";
import type { FlashState } from "@/layouts/DashboardShell";
import type { User } from "@/types/user";
import styles from "./ForcePasswordChange.module.css";

const MIN_PASSWORD_LENGTH = 10;

/** Blocking screen shown after an admin sets a user's password (creation or reset) — the same
 * change-password form as the Profile page, but standalone: nothing else in the dashboard is
 * reachable until it succeeds (see layouts/DashboardShell.tsx's redirect). */
export function ForcePasswordChangePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => profileApi.changePassword(current, next),
    onSuccess: () => {
      // The backend clears must_change_password on success. Clear it in the cache too before
      // navigating: with only a refetch, the dashboard guard (which reads this same query) still
      // saw the old flag and sent the user straight back to this form.
      queryClient.setQueryData<User>(["auth", "me"], (user) => user && { ...user, mustChangePassword: false });
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      navigate("/dashboard", { state: { flash: t("forcePasswordChange.successToast") } satisfies FlashState });
    },
    onError: () => setError(t("profile.passwordChangeError")),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (next.length < MIN_PASSWORD_LENGTH || !/\d/.test(next)) {
      setError(t("auth.resetPassword.errorTooShort", { min: MIN_PASSWORD_LENGTH }));
      return;
    }
    if (next !== confirm) {
      setError(t("auth.resetPassword.errorMismatch"));
      return;
    }
    mutation.mutate();
  }

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={handleSubmit}>
        <h2>{t("forcePasswordChange.title")}</h2>
        <p className={styles.hint}>{t("forcePasswordChange.description")}</p>

        <Input
          label={t("profile.currentPassword")}
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
        <Input
          label={t("auth.resetPassword.newPasswordLabel")}
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
        />
        <Input
          label={t("auth.resetPassword.repeatLabel")}
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
        <p className={styles.hint}>{t("auth.resetPassword.hint", { min: MIN_PASSWORD_LENGTH })}</p>

        {error && <Callout variant="danger">{error}</Callout>}

        <Button type="submit" variant="accent" disabled={mutation.isPending}>
          {t("forcePasswordChange.submit")}
        </Button>
      </form>
    </div>
  );
}
