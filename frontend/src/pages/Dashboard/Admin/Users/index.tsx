import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Input } from "@/components/Input";
import { PageHeader } from "@/components/PageHeader";
import { adminApi, type AdminUser } from "@/api/admin";
import { errorMessage } from "@/api/client";
import styles from "./Users.module.css";

/** Admin dashboard: list every account, create new ones, and manage role/enabled/password. */
export function AdminUsersPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const usersQuery = useQuery({ queryKey: ["admin", "users"], queryFn: adminApi.listUsers });
  const users = usersQuery.data ?? [];

  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [grantAdmin, setGrantAdmin] = useState(false);

  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [resetPasswordInput, setResetPasswordInput] = useState("");

  function invalidateUsers() {
    queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
  }

  const createMutation = useMutation({
    mutationFn: () => adminApi.createUser(name, email, password, grantAdmin),
    onSuccess: () => {
      invalidateUsers();
      setFormOpen(false);
      setName("");
      setEmail("");
      setPassword("");
      setGrantAdmin(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ userId, data }: { userId: string; data: { isAdmin?: boolean; enabled?: boolean } }) =>
      adminApi.updateUser(userId, data),
    onSuccess: invalidateUsers,
  });

  const resetMutation = useMutation({
    mutationFn: () => adminApi.resetPassword(resetTarget!.id, resetPasswordInput),
    onSuccess: () => {
      setResetTarget(null);
      setResetPasswordInput("");
    },
  });

  function handleCreateSubmit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !email.trim() || !password.trim() || createMutation.isPending) return;
    createMutation.mutate();
  }

  function handleResetSubmit(event: FormEvent) {
    event.preventDefault();
    if (!resetPasswordInput.trim() || resetMutation.isPending) return;
    resetMutation.mutate();
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("admin.users.title")} subtitle={t("admin.users.subtitle")} />

      <div className={styles.card}>
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{t("admin.users.table.name")}</th>
                <th>{t("admin.users.table.email")}</th>
                <th>{t("admin.users.table.role")}</th>
                <th>{t("admin.users.table.status")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>
                    {user.name}
                    {user.mustChangePassword && (
                      <div className={styles.hint}>{t("admin.users.pendingPasswordChange")}</div>
                    )}
                  </td>
                  <td>{user.email}</td>
                  <td>
                    <Badge variant={user.isAdmin ? "accent" : "default"}>
                      {user.isAdmin ? t("admin.users.roleAdmin") : t("admin.users.roleTeacher")}
                    </Badge>
                  </td>
                  <td>
                    <Badge variant={user.enabled ? "default" : "danger"}>
                      {user.enabled ? t("admin.users.statusEnabled") : t("admin.users.statusDisabled")}
                    </Badge>
                  </td>
                  <td>
                    <div className={styles.actions}>
                      <Button
                        size="sm"
                        onClick={() => updateMutation.mutate({ userId: user.id, data: { isAdmin: !user.isAdmin } })}
                        disabled={updateMutation.isPending}
                      >
                        {user.isAdmin ? t("admin.users.demote") : t("admin.users.promote")}
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => updateMutation.mutate({ userId: user.id, data: { enabled: !user.enabled } })}
                        disabled={updateMutation.isPending}
                      >
                        {user.enabled ? t("admin.users.disable") : t("admin.users.enable")}
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => {
                          setResetTarget(user);
                          setResetPasswordInput("");
                        }}
                      >
                        {t("admin.users.resetPassword")}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {users.length === 0 && !usersQuery.isLoading && <p className={styles.empty}>{t("admin.users.empty")}</p>}
        {updateMutation.isError && (
          <Callout variant="danger">{errorMessage(updateMutation.error, t("admin.users.actionFailed"))}</Callout>
        )}
      </div>

      <div className={styles.card}>
        <h3>{t("admin.users.addUser")}</h3>
        {formOpen ? (
          <form className={styles.form} onSubmit={handleCreateSubmit}>
            <Input label={t("admin.users.form.name")} value={name} onChange={(e) => setName(e.target.value)} required />
            <Input
              label={t("admin.users.form.email")}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <Input
              label={t("admin.users.form.temporaryPassword")}
              type="text"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <label className={styles.checkboxRow}>
              <input type="checkbox" checked={grantAdmin} onChange={(e) => setGrantAdmin(e.target.checked)} />
              {t("admin.users.form.grantAdmin")}
            </label>
            {createMutation.isError && (
              <Callout variant="danger">{errorMessage(createMutation.error, t("admin.users.actionFailed"))}</Callout>
            )}
            <div className={styles.formActions}>
              <Button type="submit" variant="accent" disabled={createMutation.isPending}>
                {t("admin.users.form.create")}
              </Button>
              <Button type="button" onClick={() => setFormOpen(false)}>
                {t("common.cancel")}
              </Button>
            </div>
          </form>
        ) : (
          <Button onClick={() => setFormOpen(true)}>{t("admin.users.addUserButton")}</Button>
        )}
      </div>

      {resetTarget && (
        <div className={styles.card}>
          <h3>{t("admin.users.resetPasswordTitle", { name: resetTarget.name })}</h3>
          <form className={styles.form} onSubmit={handleResetSubmit}>
            <Input
              label={t("admin.users.form.temporaryPassword")}
              type="text"
              value={resetPasswordInput}
              onChange={(e) => setResetPasswordInput(e.target.value)}
              required
            />
            {resetMutation.isError && (
              <Callout variant="danger">{errorMessage(resetMutation.error, t("admin.users.actionFailed"))}</Callout>
            )}
            <div className={styles.formActions}>
              <Button type="submit" variant="accent" disabled={resetMutation.isPending}>
                {t("admin.users.form.reset")}
              </Button>
              <Button type="button" onClick={() => setResetTarget(null)}>
                {t("common.cancel")}
              </Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
