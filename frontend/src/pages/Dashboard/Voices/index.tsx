// Dashboard tab "Voices": a teacher's private library of voice clips for local voice cloning —
// add one (upload or record), listen to it, hear a sample sentence in the cloned voice, delete it.
// Projects pick a clip in the Configurator (step 2).

import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2, Play, Trash2 } from "lucide-react";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { HeadingWithInfo } from "@/components/InfoTip";
import { Input } from "@/components/Input";
import { errorMessage } from "@/api/client";
import { voiceClipsApi, type VoiceClip } from "@/api/voiceClips";
import { numberLocale } from "@/lib/format";
import { useLocalTtsStatus } from "@/lib/providers";
import type { SpokenLanguage } from "@/types/project";
import { VoiceRecorder, type Recording } from "./VoiceRecorder";
import styles from "./Voices.module.css";

type Source = "upload" | "record";

/** The voice library tab. */
export function VoicesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const localTts = useLocalTtsStatus().data;
  const localTtsAvailable = localTts?.available ?? false;
  // Clips only become voices when the sidecar can clone (its TTS_HF_TOKEN); the preview would
  // otherwise play a preset and suggest the clip works.
  const cloningOff = Boolean(localTts?.reachable && !localTts.cloning);
  const clipsQuery = useQuery({ queryKey: ["voice-clips"], queryFn: voiceClipsApi.list });
  const clips = clipsQuery.data ?? [];

  const removeMutation = useMutation({
    mutationFn: (id: string) => voiceClipsApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voice-clips"] });
      // Projects that used the clip went back to the default voice.
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  function handleRemove(clip: VoiceClip) {
    if (window.confirm(t("voices.removeConfirm", { name: clip.name }))) removeMutation.mutate(clip.id);
  }

  return (
    <div className={styles.page}>
      <HeadingWithInfo as="h2" className={styles.header} title={t("voices.title")} info={t("voices.subtitle")} />

      {!localTtsAvailable && <Callout variant="warning">{t("voices.localTtsUnavailable")}</Callout>}
      {cloningOff && <Callout variant="warning">{t("voices.cloningOff")}</Callout>}

      <AddVoiceClip />

      <div className={styles.card}>
        <h3>{t("voices.listTitle")}</h3>
        {clipsQuery.isLoading && <p className={styles.hint}>{t("common.loading")}</p>}
        {!clipsQuery.isLoading && clips.length === 0 && <p className={styles.hint}>{t("voices.empty")}</p>}
        {removeMutation.isError && (
          <Callout variant="danger">{errorMessage(removeMutation.error, t("voices.removeFailed"))}</Callout>
        )}
        <ul className={styles.clipList}>
          {clips.map((clip) => (
            <li key={clip.id} className={styles.clip}>
              <div className={styles.clipTop}>
                <div>
                  <strong>{clip.name}</strong>
                  <div className={styles.hint}>
                    {t("voices.clipMeta", {
                      seconds: clip.durationSeconds.toLocaleString(numberLocale(), { maximumFractionDigits: 1 }),
                      date: new Date(clip.createdAt).toLocaleDateString(numberLocale()),
                    })}
                  </div>
                </div>
                <Button
                  size="sm"
                  onClick={() => handleRemove(clip)}
                  disabled={removeMutation.isPending}
                  aria-label={t("voices.remove")}
                  title={t("voices.remove")}
                >
                  <Trash2 size={14} />
                </Button>
              </div>
              <audio controls preload="none" src={clip.fileUrl} className={styles.audio} />
              {localTtsAvailable && !cloningOff && <VoicePreview clip={clip} />}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Form to add a clip: a name, an uploaded file or a recording, and the consent confirmation. */
function AddVoiceClip() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [source, setSource] = useState<Source>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [recording, setRecording] = useState<Recording | null>(null);
  const [consent, setConsent] = useState(false);
  // Remounts the file input and recorder after a successful upload, clearing what they hold.
  const [formKey, setFormKey] = useState(0);

  const audio = source === "upload" ? (file ? { blob: file, filename: file.name } : null) : recording;

  const uploadMutation = useMutation({
    mutationFn: () => voiceClipsApi.upload(name.trim(), audio!.blob, audio!.filename, consent),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voice-clips"] });
      setName("");
      setFile(null);
      setRecording(null);
      setConsent(false);
      setFormKey((key) => key + 1);
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (audio && name.trim() && consent) uploadMutation.mutate();
  }

  return (
    <form className={styles.card} onSubmit={handleSubmit}>
      <h3>{t("voices.addTitle")}</h3>
      <ul className={styles.tips}>
        <li>{t("voices.tipLength")}</li>
        <li>{t("voices.tipQuiet")}</li>
        <li>{t("voices.tipNatural")}</li>
      </ul>

      <Input
        label={t("voices.name")}
        placeholder={t("voices.namePlaceholder")}
        value={name}
        maxLength={100}
        onChange={(e) => setName(e.target.value)}
      />

      <div className={styles.sourceToggle} role="radiogroup" aria-label={t("voices.source")}>
        {(["upload", "record"] as const).map((value) => (
          <label key={value} className={styles.sourceOption}>
            <input type="radio" name="voice-source" checked={source === value} onChange={() => setSource(value)} />
            {value === "upload" ? t("voices.sourceUpload") : t("voices.sourceRecord")}
          </label>
        ))}
      </div>

      {source === "upload" ? (
        <input
          key={formKey}
          type="file"
          accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm,.flac"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      ) : (
        <VoiceRecorder key={formKey} onRecorded={setRecording} />
      )}

      <label className={styles.consent}>
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>{t("voices.consent")}</span>
      </label>

      {uploadMutation.isError && (
        <Callout variant="danger">{errorMessage(uploadMutation.error, t("voices.uploadFailed"))}</Callout>
      )}

      <div>
        <Button
          type="submit"
          variant="accent"
          disabled={!audio || !name.trim() || !consent || uploadMutation.isPending}
        >
          {uploadMutation.isPending ? t("voices.saving") : t("voices.save")}
        </Button>
      </div>
    </form>
  );
}

/** Speaks a short, editable sentence in a clip's cloned voice. */
function VoicePreview({ clip }: { clip: VoiceClip }) {
  const { t, i18n } = useTranslation();
  const [language, setLanguage] = useState<SpokenLanguage>(i18n.language.startsWith("en") ? "en" : "de");
  const [text, setText] = useState(() => t("voices.previewSample", { lng: language }));
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  useEffect(() => () => {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
  }, [audioUrl]);

  const previewMutation = useMutation({
    mutationFn: () => voiceClipsApi.preview(clip.id, text.trim(), language),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      setAudioUrl(url);
      void new Audio(url).play().catch(() => undefined); // the <audio> below stays as a fallback
    },
  });

  function changeLanguage(next: SpokenLanguage) {
    // Swap the sample sentence along with the language, unless the teacher already wrote their own.
    if (text === t("voices.previewSample", { lng: language })) setText(t("voices.previewSample", { lng: next }));
    setLanguage(next);
  }

  return (
    <div className={styles.preview}>
      <div className={styles.previewRow}>
        <input
          className={styles.previewText}
          value={text}
          maxLength={300}
          onChange={(e) => setText(e.target.value)}
          aria-label={t("voices.previewText")}
        />
        <select
          className={styles.previewLanguage}
          value={language}
          onChange={(e) => changeLanguage(e.target.value as SpokenLanguage)}
          aria-label={t("voices.previewLanguage")}
        >
          <option value="de">{t("configurator.step2.spokenLanguageOptions.de")}</option>
          <option value="en">{t("configurator.step2.spokenLanguageOptions.en")}</option>
        </select>
        <Button size="sm" onClick={() => previewMutation.mutate()} disabled={!text.trim() || previewMutation.isPending}>
          {previewMutation.isPending ? <Loader2 size={14} className={styles.spin} /> : <Play size={14} />}{" "}
          {t("voices.previewButton")}
        </Button>
      </div>
      {previewMutation.isPending && <p className={styles.hint}>{t("voices.previewSlow")}</p>}
      {previewMutation.isError && (
        <p className={styles.error}>{errorMessage(previewMutation.error, t("voices.previewFailed"))}</p>
      )}
      {audioUrl && !previewMutation.isPending && <audio controls src={audioUrl} className={styles.audio} />}
    </div>
  );
}
