// Dashboard tab "Motion prompt": the gesture instructions a project appends to its system prompt
// (see backend features/chat/motion.py), editable per project. Blank/default = the built-in text.

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Textarea } from "@/components/Input";
import { PageHeader } from "@/components/PageHeader";
import { errorMessage } from "@/api/client";
import { projectsApi } from "@/api/projects";
import styles from "./MotionPrompt.module.css";

/** The motion prompt tab. */
export function MotionPromptPage() {
  const { t } = useTranslation();
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const projects = projectsQuery.data ?? [];
  const [selectedId, setSelectedId] = useState("");
  const project = projects.find((p) => p.id === selectedId) ?? projects[0];

  return (
    <div className={styles.page}>
      <PageHeader title={t("motionPrompt.title")} subtitle={t("motionPrompt.subtitle")} />

      {projectsQuery.isSuccess && projects.length === 0 && <Callout variant="info">{t("motionPrompt.noProject")}</Callout>}

      {project && (
        <>
          <select
            className={styles.select}
            aria-label={t("motionPrompt.project")}
            value={project.id}
            onChange={(e) => setSelectedId(e.target.value)}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
          <ProjectMotionPrompt key={project.id} projectId={project.id} />
        </>
      )}
    </div>
  );
}

function ProjectMotionPrompt({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // The endpoint gives the effective text, the built-in default and whether gestures are on.
  const promptQuery = useQuery({
    queryKey: ["system-prompt", projectId],
    queryFn: () => projectsApi.systemPrompt(projectId),
  });
  const prompt = promptQuery.data;
  const [draft, setDraft] = useState("");

  // Re-sync the draft whenever the saved prompt changes (first load, after saving).
  useEffect(() => {
    if (prompt) setDraft(prompt.motionPrompt);
  }, [prompt]);

  const saveMutation = useMutation({
    // Equal to the default = not customized (sent blank), so the project keeps following future
    // improvements to the built-in text.
    mutationFn: () =>
      projectsApi.update(projectId, {
        motionPrompt: draft.trim() === prompt?.defaultMotionPrompt.trim() ? "" : draft,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["system-prompt", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  if (!prompt) return promptQuery.isError ? <Callout variant="danger">{t("motionPrompt.loadError")}</Callout> : null;

  const dirty = draft !== prompt.motionPrompt;

  return (
    <>
      {!prompt.motionActive && <Callout variant="warning">{t("motionPrompt.inactive")}</Callout>}

      <Textarea
        label={t("motionPrompt.label")}
        hint={t("motionPrompt.hint")}
        rows={12}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />

      <div className={styles.actions}>
        <Button onClick={() => saveMutation.mutate()} disabled={!dirty || saveMutation.isPending}>
          {t("common.save")}
        </Button>
        <Button onClick={() => setDraft(prompt.defaultMotionPrompt)} disabled={draft === prompt.defaultMotionPrompt}>
          {t("motionPrompt.reset")}
        </Button>
        {saveMutation.isError && <span role="alert">{errorMessage(saveMutation.error, t("motionPrompt.saveError"))}</span>}
        {saveMutation.isSuccess && !dirty && <span>{t("motionPrompt.saved")}</span>}
      </div>
    </>
  );
}
