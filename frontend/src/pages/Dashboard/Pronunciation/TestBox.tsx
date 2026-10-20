// The test box: type a sentence, see exactly what the TTS will be given (live, with the word list
// and the built-in rules applied), and hear it with a TTS model, voice and language of your choice
// — or the voice settings of one of your projects.

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2, Play } from "lucide-react";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Textarea } from "@/components/Input";
import { apiKeysApi } from "@/api/apiKeys";
import { errorMessage } from "@/api/client";
import { projectsApi } from "@/api/projects";
import { pronunciationApi } from "@/api/pronunciation";
import { voiceClipsApi } from "@/api/voiceClips";
import { findProvider, keyDisplayName, modelLabel, useLocalTtsStatus, useProviders } from "@/lib/providers";
import type { SpokenLanguage } from "@/types/project";
import { PRONUNCIATION_QUERY_KEY } from "./queries";
import styles from "./Pronunciation.module.css";

// The <select> value for "the local TTS service" — a key id is a UUID, so it can't collide.
const LOCAL_TTS = "";
const LIVE_PREVIEW_DELAY_MS = 400;

function base64ToBlob(base64: string, contentType: string): Blob {
  const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  return new Blob([bytes], { type: contentType });
}

export function TestBox({ language: pageLanguage }: { language: SpokenLanguage }) {
  const { t } = useTranslation();
  const [language, setLanguage] = useState<SpokenLanguage>(pageLanguage);
  const [text, setText] = useState(() => t("pronunciation.test.sample", { lng: pageLanguage }));
  const [debouncedText, setDebouncedText] = useState(text);
  const [ttsKeyId, setTtsKeyId] = useState<string>(LOCAL_TTS);
  const [voice, setVoice] = useState("");
  const [voiceClipId, setVoiceClipId] = useState("");
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const localTts = useLocalTtsStatus().data;
  const localTtsAvailable = localTts?.available ?? false;
  const specs = useProviders().data ?? [];
  const keysQuery = useQuery({ queryKey: ["api-keys"], queryFn: apiKeysApi.list });
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const clipsQuery = useQuery({ queryKey: ["voice-clips"], queryFn: voiceClipsApi.list, enabled: localTtsAvailable });
  // Clips only speak in their own voice where the sidecar clones voices; otherwise it would
  // quietly use the default voice.
  const offeredClips = localTts?.cloning ? (clipsQuery.data ?? []) : [];
  // Same filter as the Configurator (Step2Technical.tsx): a TTS key needs a model, unless its
  // provider has a fixed one.
  const ttsKeys = (keysQuery.data ?? []).filter(
    (key) => key.keyType === "tts" && (key.modelId || findProvider(specs, key.provider)?.ttsModelFixed),
  );
  const projects = (projectsQuery.data ?? []).filter((project) => project.ttsEnabled);
  const usesLocalTts = ttsKeyId === LOCAL_TTS;
  const canSynthesize = usesLocalTts ? localTtsAvailable : true;

  // The test box follows the page's language tab, swapping the sample sentence along with it
  // unless the teacher already wrote their own.
  useEffect(() => {
    changeLanguage(pageLanguage);
  }, [pageLanguage]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedText(text), LIVE_PREVIEW_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  useEffect(
    () => () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    },
    [audioUrl],
  );

  // Text-only, so free and instant — re-runs whenever the sentence, the language or (through the
  // shared query key prefix) the word list changes.
  const liveQuery = useQuery({
    queryKey: [...PRONUNCIATION_QUERY_KEY, "preview", language, debouncedText],
    queryFn: () =>
      pronunciationApi.preview({
        text: debouncedText.trim(),
        language,
        synthesize: false,
        ttsApiKeyId: null,
        ttsVoice: null,
        voiceClipId: null,
      }),
    enabled: debouncedText.trim() !== "",
    placeholderData: (previous) => previous,
  });

  const playMutation = useMutation({
    mutationFn: () =>
      pronunciationApi.preview({
        text: text.trim(),
        language,
        synthesize: true,
        ttsApiKeyId: usesLocalTts ? null : ttsKeyId,
        ttsVoice: usesLocalTts ? null : voice.trim() || null,
        voiceClipId: usesLocalTts ? voiceClipId || null : null,
      }),
    onSuccess: (result) => {
      if (!result.audioBase64 || !result.contentType) return;
      const url = URL.createObjectURL(base64ToBlob(result.audioBase64, result.contentType));
      setAudioUrl(url);
      void new Audio(url).play().catch(() => undefined); // the <audio> below stays as a fallback
    },
  });

  function changeLanguage(next: SpokenLanguage) {
    if (text === t("pronunciation.test.sample", { lng: language })) {
      setText(t("pronunciation.test.sample", { lng: next }));
    }
    setLanguage(next);
  }

  function applyProjectSettings(projectId: string) {
    const project = projects.find((p) => p.id === projectId);
    if (!project) return;
    setTtsKeyId(project.ttsApiKeyId ?? LOCAL_TTS);
    setVoice(project.ttsVoice ?? "");
    setVoiceClipId(project.ttsVoiceClipId ?? "");
    changeLanguage(project.spokenLanguage);
  }

  const live = liveQuery.data;

  return (
    <div className={styles.card}>
      <h3>{t("pronunciation.test.title")}</h3>
      <p className={styles.hint}>{t("pronunciation.test.text")}</p>

      <Textarea
        label={t("pronunciation.test.sentence")}
        value={text}
        maxLength={300}
        rows={2}
        onChange={(e) => setText(e.target.value)}
      />

      <div className={styles.spokenBox} aria-live="polite">
        <span className={styles.spokenLabel}>{t("pronunciation.test.spokenAs")}</span>
        <p className={styles.spokenText}>{debouncedText.trim() ? (live?.spokenText ?? "…") : "—"}</p>
        {live && live.appliedTerms.length > 0 && (
          <div className={styles.appliedTerms}>
            <span className={styles.hint}>{t("pronunciation.test.applied")}</span>
            {live.appliedTerms.map((term) => (
              <Badge key={term}>{term}</Badge>
            ))}
          </div>
        )}
      </div>
      {liveQuery.isError && (
        <p className={styles.error}>{errorMessage(liveQuery.error, t("pronunciation.test.previewFailed"))}</p>
      )}

      <div className={styles.fieldRow}>
        {projects.length > 0 && (
          <label className={styles.selectField}>
            <span className={styles.selectLabel}>{t("pronunciation.test.fromProject")}</span>
            <select className={styles.select} value="" onChange={(e) => applyProjectSettings(e.target.value)}>
              <option value="">{t("pronunciation.test.fromProjectPlaceholder")}</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className={styles.selectField}>
          <span className={styles.selectLabel}>{t("pronunciation.test.language")}</span>
          <select
            className={styles.select}
            value={language}
            onChange={(e) => changeLanguage(e.target.value as SpokenLanguage)}
          >
            <option value="de">{t("configurator.step2.spokenLanguageOptions.de")}</option>
            <option value="en">{t("configurator.step2.spokenLanguageOptions.en")}</option>
          </select>
        </label>
      </div>

      <div className={styles.fieldRow}>
        <label className={styles.selectField}>
          <span className={styles.selectLabel}>{t("pronunciation.test.ttsModel")}</span>
          <select className={styles.select} value={ttsKeyId} onChange={(e) => setTtsKeyId(e.target.value)}>
            <option value={LOCAL_TTS} disabled={!localTtsAvailable}>
              {localTtsAvailable ? t("pronunciation.test.localTts") : t("pronunciation.test.localTtsUnavailable")}
            </option>
            {ttsKeys.map((key) => (
              <option key={key.id} value={key.id}>
                {keyDisplayName(key, specs)} · {modelLabel(key, specs)}
              </option>
            ))}
          </select>
        </label>
        {usesLocalTts ? (
          localTtsAvailable && (
            <label className={styles.selectField}>
              <span className={styles.selectLabel}>{t("pronunciation.test.voiceClip")}</span>
              <select className={styles.select} value={voiceClipId} onChange={(e) => setVoiceClipId(e.target.value)}>
                <option value="">{t("configurator.step2.voiceClipDefault")}</option>
                {offeredClips.map((clip) => (
                  <option key={clip.id} value={clip.id}>
                    {clip.name}
                  </option>
                ))}
              </select>
            </label>
          )
        ) : (
          <label className={styles.selectField}>
            <span className={styles.selectLabel}>{t("pronunciation.test.voice")}</span>
            <input
              className={styles.select}
              value={voice}
              maxLength={200}
              placeholder={t("configurator.step2.voicePlaceholder")}
              onChange={(e) => setVoice(e.target.value)}
            />
          </label>
        )}
      </div>

      {!canSynthesize && ttsKeys.length === 0 && (
        <Callout variant="warning">{t("pronunciation.test.noTts")}</Callout>
      )}

      <div className={styles.actions}>
        <Button onClick={() => playMutation.mutate()} disabled={!text.trim() || !canSynthesize || playMutation.isPending}>
          {playMutation.isPending ? <Loader2 size={14} className={styles.spin} /> : <Play size={14} />}{" "}
          {t("pronunciation.test.play")}
        </Button>
      </div>
      {playMutation.isPending && usesLocalTts && <p className={styles.hint}>{t("voices.previewSlow")}</p>}
      {playMutation.isError && (
        <Callout variant="danger">{errorMessage(playMutation.error, t("pronunciation.test.playFailed"))}</Callout>
      )}
      {audioUrl && !playMutation.isPending && <audio controls src={audioUrl} className={styles.audio} />}
    </div>
  );
}
