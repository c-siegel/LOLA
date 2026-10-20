import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { Callout } from "@/components/Callout";
import { Toast } from "@/components/Toast";
import { useToast } from "@/hooks/useToast";
import { projectsApi } from "@/api/projects";
import { normalizeChecklist } from "@/lib/checklist";
import type { Project } from "@/types/project";
import { Step1Appearance } from "./steps/Step1Appearance";
import { Step2Technical } from "./steps/Step2Technical";
import { Step3Behavior } from "./steps/Step3Behavior";
import { Step4Preview } from "./steps/Step4Preview";
import { Step5Publish } from "./steps/Step5Publish";
import type { ConfiguratorDraft } from "./types";
import styles from "./Configurator.module.css";

const STEPS = [
  { id: 1, labelKey: "configurator.steps.appearance" },
  { id: 2, labelKey: "configurator.steps.technical" },
  { id: 3, labelKey: "configurator.steps.behavior" },
  { id: 4, labelKey: "configurator.steps.preview" },
  { id: 5, labelKey: "configurator.steps.publish" },
] as const;

type StepId = (typeof STEPS)[number]["id"];

function toDraft(project: Project): ConfiguratorDraft {
  return {
    title: project.title,
    description: project.description ?? "",
    avatarModelId: project.avatarModelId,
    builtinAvatar: project.builtinAvatar,
    avatarBackgroundId: project.avatarBackgroundId,
    chatLayout: project.chatLayout,
    motionEnabled: project.motionEnabled,
    thinkingMotionsEnabled: project.thinkingMotionsEnabled,
    gradeLevel: project.gradeLevel ?? "",
    preprompt: project.preprompt ?? "",
    startPrompt: project.startPrompt ?? "",
    // No preselection anymore: the dropdown in step 3 only lists configured keys, and as long as
    // none is chosen it shows a placeholder instead of a model the teacher never confirmed.
    llmApiKeyId: project.llmApiKeyId,
    temperature: project.temperature,
    topP: project.topP,
    thinkingMode: project.thinkingMode,
    saveConversations: project.saveConversations,
    requireVisitorName: project.requireVisitorName,
    surveyBeforeUrl: project.surveyBeforeUrl ?? "",
    surveyBeforeEnabled: project.surveyBeforeEnabled,
    surveyAfterUrl: project.surveyAfterUrl ?? "",
    surveyAfterEnabled: project.surveyAfterEnabled,
    spokenLanguage: project.spokenLanguage,
    ttsEnabled: project.ttsEnabled,
    ttsApiKeyId: project.ttsApiKeyId,
    ttsVoice: project.ttsVoice ?? "",
    ttsVoiceClipId: project.ttsVoiceClipId,
    sttApiKeyId: project.sttApiKeyId,
    sttServerEngine: project.sttServerEngine,
    sttEnabled: project.sttEnabled,
    sttBrowserEnabled: project.sttBrowserEnabled,
    ttsBrowserEnabled: project.ttsBrowserEnabled,
    streamingEnabled: project.streamingEnabled,
    knowledgeMode: project.knowledgeMode,
    knowledgeTopK: project.knowledgeTopK,
    knowledgeBaseIds: project.knowledgeBaseIds,
    checklistEnabled: project.checklistEnabled,
    checklistVisible: project.checklistVisible,
    checklistOrdered: project.checklistOrdered,
    checklistItems: project.checklistItems,
  };
}

// Compares only the fields that are actually persisted (goal/personality are purely local to
// step 1, see types.ts) — decides whether the "unsaved changes" notice appears in step 4.
function isDirty(draft: ConfiguratorDraft, project: Project): boolean {
  return (
    draft.title !== project.title ||
    draft.description !== (project.description ?? "") ||
    draft.avatarModelId !== project.avatarModelId ||
    draft.builtinAvatar !== project.builtinAvatar ||
    draft.avatarBackgroundId !== project.avatarBackgroundId ||
    draft.chatLayout !== project.chatLayout ||
    draft.motionEnabled !== project.motionEnabled ||
    draft.thinkingMotionsEnabled !== project.thinkingMotionsEnabled ||
    (draft.gradeLevel || null) !== project.gradeLevel ||
    draft.preprompt !== (project.preprompt ?? "") ||
    draft.startPrompt !== (project.startPrompt ?? "") ||
    draft.llmApiKeyId !== project.llmApiKeyId ||
    draft.temperature !== project.temperature ||
    draft.topP !== project.topP ||
    draft.thinkingMode !== project.thinkingMode ||
    draft.saveConversations !== project.saveConversations ||
    draft.requireVisitorName !== project.requireVisitorName ||
    draft.surveyBeforeUrl !== (project.surveyBeforeUrl ?? "") ||
    draft.surveyBeforeEnabled !== project.surveyBeforeEnabled ||
    draft.surveyAfterUrl !== (project.surveyAfterUrl ?? "") ||
    draft.surveyAfterEnabled !== project.surveyAfterEnabled ||
    draft.spokenLanguage !== project.spokenLanguage ||
    draft.ttsEnabled !== project.ttsEnabled ||
    draft.ttsApiKeyId !== project.ttsApiKeyId ||
    draft.ttsVoice !== (project.ttsVoice ?? "") ||
    draft.ttsVoiceClipId !== project.ttsVoiceClipId ||
    draft.sttApiKeyId !== project.sttApiKeyId ||
    draft.sttServerEngine !== project.sttServerEngine ||
    draft.sttEnabled !== project.sttEnabled ||
    draft.sttBrowserEnabled !== project.sttBrowserEnabled ||
    draft.ttsBrowserEnabled !== project.ttsBrowserEnabled ||
    draft.streamingEnabled !== project.streamingEnabled ||
    draft.knowledgeMode !== project.knowledgeMode ||
    draft.knowledgeTopK !== project.knowledgeTopK ||
    draft.knowledgeBaseIds.join() !== project.knowledgeBaseIds.join() ||
    draft.checklistEnabled !== project.checklistEnabled ||
    draft.checklistVisible !== project.checklistVisible ||
    draft.checklistOrdered !== project.checklistOrdered ||
    JSON.stringify(normalizeChecklist(draft.checklistItems)) !== JSON.stringify(project.checklistItems)
  );
}

// Screen 1e — configurator tab: 5-step wizard (appearance → technical → behaviour → preview →
// publish). Deviates from the single-column wireframe reference on explicit request.
export function ConfiguratorPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const projectId = id!;
  const queryClient = useQueryClient();
  const toast = useToast();

  const projectQuery = useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const [step, setStep] = useState<StepId>(1);
  const [draft, setDraft] = useState<ConfiguratorDraft | null>(null);
  const [prepromptGenerated, setPrepromptGenerated] = useState(false);

  // Initialize the draft once from the loaded project data.
  useEffect(() => {
    if (projectQuery.data && !draft) {
      setDraft(toDraft(projectQuery.data));
      setPrepromptGenerated(Boolean(projectQuery.data.preprompt));
    }
  }, [projectQuery.data, draft]);

  const saveMutation = useMutation({
    mutationFn: (data: ConfiguratorDraft) =>
      projectsApi.update(projectId, {
        title: data.title,
        description: data.description,
        avatarModelId: data.avatarModelId,
        builtinAvatar: data.builtinAvatar,
        avatarBackgroundId: data.avatarBackgroundId,
        chatLayout: data.chatLayout,
        motionEnabled: data.motionEnabled,
        thinkingMotionsEnabled: data.thinkingMotionsEnabled,
        gradeLevel: data.gradeLevel || null,
        preprompt: data.preprompt,
        startPrompt: data.startPrompt,
        llmApiKeyId: data.llmApiKeyId,
        temperature: data.temperature,
        topP: data.topP,
        thinkingMode: data.thinkingMode,
        saveConversations: data.saveConversations,
        requireVisitorName: data.requireVisitorName,
        surveyBeforeUrl: data.surveyBeforeUrl,
        surveyBeforeEnabled: data.surveyBeforeEnabled,
        surveyAfterUrl: data.surveyAfterUrl,
        surveyAfterEnabled: data.surveyAfterEnabled,
        spokenLanguage: data.spokenLanguage,
        ttsEnabled: data.ttsEnabled,
        ttsApiKeyId: data.ttsApiKeyId,
        ttsVoice: data.ttsVoice || null,
        ttsVoiceClipId: data.ttsVoiceClipId,
        sttApiKeyId: data.sttApiKeyId,
        sttServerEngine: data.sttServerEngine,
        sttEnabled: data.sttEnabled,
        sttBrowserEnabled: data.sttBrowserEnabled,
        ttsBrowserEnabled: data.ttsBrowserEnabled,
        streamingEnabled: data.streamingEnabled,
        knowledgeMode: data.knowledgeMode,
        knowledgeTopK: data.knowledgeTopK,
        knowledgeBaseIds: data.knowledgeBaseIds,
        checklistEnabled: data.checklistEnabled,
        checklistVisible: data.checklistVisible,
        checklistOrdered: data.checklistOrdered,
        checklistItems: normalizeChecklist(data.checklistItems),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["projects", projectId], updated);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.show(t("configurator.progressSaved"));
    },
  });

  if (projectQuery.isLoading || !draft) {
    return <p>{t("common.loading")}</p>;
  }

  if (projectQuery.isError || !projectQuery.data) {
    return <Callout variant="danger">{t("configurator.loadError")}</Callout>;
  }

  const project = projectQuery.data;

  function updateDraft(patch: Partial<ConfiguratorDraft>) {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  }

  return (
    <div className={styles.page}>
      <div className={styles.topbar}>
        <div className={styles.titleGroup}>
          <h2 className={styles.title}>{draft.title || t("configurator.newProject")}</h2>
          <Badge variant={project.published ? "accent" : "default"}>
            {project.published ? t("overview.card.published") : t("configurator.draftUnpublished")}
          </Badge>
        </div>
        <Button variant="accent" onClick={() => saveMutation.mutate(draft)} disabled={saveMutation.isPending}>
          {t("common.save")}
        </Button>
      </div>

      {saveMutation.isError && <Callout variant="danger">{t("configurator.saveError")}</Callout>}

      <nav className={styles.stepper} aria-label={t("configurator.stepsAriaLabel")}>
        {STEPS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`${styles.step} ${step === s.id ? styles.stepActive : ""}`}
            onClick={() => setStep(s.id)}
          >
            <span className={styles.stepNumber}>{s.id}</span>
            {t(s.labelKey)}
          </button>
        ))}
      </nav>

      <div className={styles.stepContent}>
        {step === 1 && <Step1Appearance draft={draft} onChange={updateDraft} />}
        {step === 2 && <Step2Technical draft={draft} onChange={updateDraft} />}
        {step === 3 && (
          <Step3Behavior
            draft={draft}
            onChange={updateDraft}
            autoGenerate={!prepromptGenerated}
            onGenerated={() => setPrepromptGenerated(true)}
            projectId={projectId}
            savedStartPrompt={project.startPrompt ?? ""}
            startAudioUrl={project.startAudioUrl}
            hasUnsavedChanges={isDirty(draft, project)}
            onSaveDraft={() => saveMutation.mutateAsync(draft)}
          />
        )}
        {step === 4 && (
          // avatarModelUrl/ttsEnabled deliberately come from project (the saved state), not from
          // draft — the preview chat route (/projects/{id}/chat/messages) also works on the
          // persisted project data on the server side (see the comment in Step4Preview.tsx).
          <Step4Preview
            projectId={projectId}
            title={draft.title}
            startPrompt={project.startPrompt}
            avatarModelUrl={project.avatarModelUrl}
            avatarBackgroundUrl={project.avatarBackgroundUrl}
            ttsEnabled={project.ttsEnabled}
            motionEnabled={project.motionEnabled}
            thinkingMotionsEnabled={project.thinkingMotionsEnabled}
            checklist={
              project.checklistEnabled && project.checklistItems.length
                ? { ordered: project.checklistOrdered, items: project.checklistItems }
                : null
            }
            hasUnsavedChanges={isDirty(draft, project)}
          />
        )}
        {step === 5 && (
          <Step5Publish
            draft={draft}
            onChange={updateDraft}
            project={project}
            projectId={projectId}
            hasUnsavedChanges={isDirty(draft, project)}
            onSaveDraft={() => saveMutation.mutateAsync(draft)}
          />
        )}
      </div>

      <div className={styles.stepNav}>
        <span>
          {step > 1 && <Button onClick={() => setStep((s) => (s - 1) as StepId)}>{t("common.back")}</Button>}
        </span>
        {step < 5 && (
          <Button variant="accent" onClick={() => setStep((s) => (s + 1) as StepId)}>
            {t("configurator.next")}
          </Button>
        )}
      </div>

      {toast.message && <Toast message={toast.message} onDismiss={toast.dismiss} />}
    </div>
  );
}
