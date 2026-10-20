// Dashboard tab "Latency test": simulates student conversations with one of the teacher's own
// projects and measures every module (speech recognition, LLM, TTS, playback) and the end-to-end
// wait — above all, how long after the student stops speaking the avatar starts answering. The
// configuration (STT on the device or the server, LLM model, streaming, TTS path, avatar on/off)
// can be switched without editing the project, so devices and setups can be compared. Messages
// go through backend features/chat/latency_router.py and are never saved; results stay on this
// device until exported.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Mic, Play, Square, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { HeadingWithInfo } from "@/components/InfoTip";
import { Input, Textarea } from "@/components/Input";
import { AudioOnlySpeaker, TalkingHeadAvatar, type TalkingHeadAvatarHandle } from "@/components/TalkingHeadAvatar";
import { apiKeysApi } from "@/api/apiKeys";
import { projectsApi } from "@/api/projects";
import { voiceClipsApi } from "@/api/voiceClips";
import { ParakeetSttEngine, isWebGpuAvailable, type LoadProgress } from "@/lib/parakeetStt";
import { modelLabel, useBrowserSttStatus, useLocalTtsStatus, useProviders, useServerSttStatus } from "@/lib/providers";
import { PocketBrowserTts } from "@/lib/pocketTts";
import { SoproBrowserTts } from "@/lib/soproTts";
import type { ChatMessage } from "@/types/chat";
import {
  METRIC_COLUMNS,
  downloadFile,
  summarize,
  toCsv,
  type DeviceInfo,
  type LabConfig,
  type MetricColumn,
  type SttMode,
  type TtsMode,
  type TurnResult,
} from "./metrics";
import { runTurn, type Clip, type TurnContext, type TurnInput } from "./turnRunner";
import styles from "./LatencyLab.module.css";
import { sitePath } from "@/lib/basePath";

const PARAKEET_MODEL_URL = sitePath("/models/parakeet-redux/v1/");
// Written by scripts/fetch-tts-model.sh (German Pocket TTS with the voice "alba").
const POCKET_MODEL_URL = sitePath("/models/pocket-tts-de/v1/");
const SETTINGS_KEY = "lola.latencyLab";
const DEFAULT_SCRIPT = "Hallo! Wer bist du?\nKannst du mir das in einem Satz erklären?";

const DEFAULT_CONFIG: LabConfig = { sttMode: "device", llmKeyId: null, streaming: true, ttsMode: "project", avatar: true };

interface StoredSettings {
  projectId?: string;
  config?: LabConfig;
  script?: string;
  repeats?: number;
  pauseMs?: number;
  soproModelUrl?: string;
}

// Per-viewer convenience only: the page works the same with nothing stored (private browsing).
function loadSettings(): StoredSettings {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}") as StoredSettings;
  } catch {
    return {};
  }
}

function saveSettings(settings: StoredSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Storage blocked — the settings just aren't remembered.
  }
}

interface EngineState {
  status: string;
  progress: LoadProgress | null;
  loadSeconds: number | null;
  error: string | null;
}

const idleEngine: EngineState = { status: "idle", progress: null, loadSeconds: null, error: null };

const ms = (value: number | null | undefined) => (value == null ? "–" : String(Math.round(value)));
const percent = (progress: LoadProgress | null) =>
  progress && progress.totalBytes ? Math.floor((progress.loadedBytes / progress.totalBytes) * 100) : 0;
const megabytes = (bytes: number) => `${Math.round(bytes / 1_000_000)} MB`;

/** One script line: plain text, or `clip: <name>` for an uploaded clip. Empty lines and lines
 * starting with # are skipped. */
function parseScript(script: string, clips: Clip[]): { inputs: TurnInput[]; missing: string[] } {
  const inputs: TurnInput[] = [];
  const missing: string[] = [];
  for (const raw of script.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const clipMatch = /^clip:\s*(.+)$/i.exec(line);
    if (clipMatch) {
      const clip = clips.find((c) => c.name === clipMatch[1].trim());
      if (clip) inputs.push({ kind: "clip", clip });
      else missing.push(clipMatch[1].trim());
    } else {
      inputs.push({ kind: "text", text: line });
    }
  }
  return { inputs, missing };
}

/** The latency test tab. */
export function LatencyLabPage() {
  const { t } = useTranslation();
  const stored = useMemo(loadSettings, []);
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: projectsApi.list });
  const keysQuery = useQuery({ queryKey: ["api-keys"], queryFn: apiKeysApi.list });
  const clipsQuery = useQuery({ queryKey: ["voice-clips"], queryFn: voiceClipsApi.list });
  const specs = useProviders().data ?? [];
  const browserSttAvailable = useBrowserSttStatus().data?.available ?? false;
  const localTtsAvailable = useLocalTtsStatus().data?.available ?? false;
  const parakeetServerAvailable = useServerSttStatus().data?.parakeetAvailable ?? false;

  const projects = projectsQuery.data ?? [];
  const llmKeys = (keysQuery.data ?? []).filter((key) => key.keyType === "llm");
  const [projectId, setProjectId] = useState(stored.projectId ?? "");
  const project = projects.find((p) => p.id === projectId) ?? null;
  const [config, setConfig] = useState<LabConfig>({ ...DEFAULT_CONFIG, ...stored.config });
  const [script, setScript] = useState(stored.script ?? DEFAULT_SCRIPT);
  const [repeats, setRepeats] = useState(stored.repeats ?? 3);
  const [pauseMs, setPauseMs] = useState(stored.pauseMs ?? 1500);
  const [soproModelUrl, setSoproModelUrl] = useState(stored.soproModelUrl ?? "");

  useEffect(() => {
    if (!projectId && projects.length) setProjectId(projects[0].id);
  }, [projectId, projects]);

  useEffect(() => {
    saveSettings({ projectId, config, script, repeats, pauseMs, soproModelUrl });
  }, [projectId, config, script, repeats, pauseMs, soproModelUrl]);

  // ---- Device and engines ----
  const [device, setDevice] = useState<DeviceInfo>({
    userAgent: navigator.userAgent,
    webGpu: false,
    deviceMemoryGb: (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? null,
    cpuCores: navigator.hardwareConcurrency ?? null,
    pixelRatio: window.devicePixelRatio,
    screen: `${window.screen.width}×${window.screen.height}`,
  });
  useEffect(() => {
    void isWebGpuAvailable().then((webGpu) => setDevice((prev) => ({ ...prev, webGpu })));
  }, []);

  const parakeetRef = useRef<ParakeetSttEngine | null>(null);
  const soproRef = useRef<SoproBrowserTts | null>(null);
  const pocketRef = useRef<PocketBrowserTts | null>(null);
  const [parakeet, setParakeet] = useState<EngineState>(idleEngine);
  const [sopro, setSopro] = useState<EngineState & { backend: string | null }>({ ...idleEngine, backend: null });
  const [pocket, setPocket] = useState<EngineState & { backend: string | null }>({ ...idleEngine, backend: null });
  const [voice, setVoice] = useState<{ name: string; prepareMs: number } | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  useEffect(
    () => () => {
      parakeetRef.current?.dispose();
      soproRef.current?.dispose();
      pocketRef.current?.dispose();
    },
    [],
  );

  async function loadParakeet() {
    parakeetRef.current?.dispose();
    const engine = new ParakeetSttEngine(PARAKEET_MODEL_URL);
    parakeetRef.current = engine;
    const started = performance.now();
    setParakeet({ ...idleEngine, status: "loading" });
    await engine.ensureReady((progress) => setParakeet((prev) => ({ ...prev, progress })));
    setParakeet({
      status: engine.status,
      progress: null,
      loadSeconds: engine.status === "ready" ? (performance.now() - started) / 1000 : null,
      error: engine.errorMessage,
    });
  }

  async function loadSopro() {
    const engine = soproRef.current ?? new SoproBrowserTts();
    soproRef.current = engine;
    setVoice(null);
    const started = performance.now();
    setSopro({ ...idleEngine, status: "loading", backend: null });
    try {
      await engine.load(soproModelUrl.trim() || null, (progress) => setSopro((prev) => ({ ...prev, progress })));
      setSopro({ status: "ready", progress: null, loadSeconds: (performance.now() - started) / 1000, error: null, backend: engine.backend });
    } catch (error) {
      setSopro({ ...idleEngine, status: "error", error: error instanceof Error ? error.message : String(error), backend: null });
    }
  }

  async function loadPocket() {
    const engine = pocketRef.current ?? new PocketBrowserTts();
    pocketRef.current = engine;
    const started = performance.now();
    setPocket({ ...idleEngine, status: "loading", backend: null });
    try {
      await engine.load(POCKET_MODEL_URL, (progress) => setPocket((prev) => ({ ...prev, progress })));
      setPocket({ status: "ready", progress: null, loadSeconds: (performance.now() - started) / 1000, error: null, backend: engine.backend });
    } catch (error) {
      setPocket({ ...idleEngine, status: "error", error: error instanceof Error ? error.message : String(error), backend: null });
    }
  }

  async function setSoproVoice(name: string, blob: Blob) {
    setVoiceError(null);
    try {
      const prepareMs = await soproRef.current!.setReference(blob);
      setVoice({ name, prepareMs });
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : String(error));
    }
  }

  async function pickVoiceClip(clipId: string) {
    const clip = clipsQuery.data?.find((c) => c.id === clipId);
    if (!clip) return;
    const response = await fetch(clip.fileUrl, { credentials: "include" });
    if (!response.ok) {
      setVoiceError(`HTTP ${response.status}`);
      return;
    }
    await setSoproVoice(clip.name, await response.blob());
  }

  // ---- Clips ----
  const [clips, setClips] = useState<Clip[]>([]);
  function addClips(files: FileList | null) {
    if (!files) return;
    const added = [...files].map((file) => ({ id: crypto.randomUUID(), name: file.name, blob: file as Blob }));
    setClips((prev) => [...prev, ...added]);
  }

  // ---- Running turns ----
  const speakerRef = useRef<TalkingHeadAvatarHandle>(null);
  const inputContextRef = useRef<AudioContext | null>(null);
  const historyRef = useRef<ChatMessage[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const stopMicRef = useRef<(() => void) | null>(null);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [progressText, setProgressText] = useState<string | null>(null);
  const [partial, setPartial] = useState("");
  const [reply, setReply] = useState("");
  const [text, setText] = useState("");
  const [results, setResults] = useState<TurnResult[]>([]);
  const [setupError, setSetupError] = useState<string | null>(null);

  const selectedKey = llmKeys.find((key) => key.id === config.llmKeyId) ?? null;
  const projectKey = llmKeys.find((key) => key.id === project?.llmApiKeyId) ?? null;
  const llmModel =
    (selectedKey && (modelLabel(selectedKey, specs) ?? selectedKey.modelId)) ||
    (projectKey && (modelLabel(projectKey, specs) ?? projectKey.modelId)) ||
    project?.llmModel ||
    "?";
  const configLabel = [
    `STT ${config.sttMode}`,
    llmModel,
    config.streaming ? "stream" : "plain",
    `TTS ${config.ttsMode}`,
    config.avatar ? "avatar" : "audio only",
  ].join(" · ");

  /** Checks what the chosen configuration needs; a translated problem, or null. */
  function setupProblem(spoken: boolean): string | null {
    if (!project) return t("latencyLab.noProject");
    if (spoken && config.sttMode === "device" && parakeetRef.current?.status !== "ready") return t("latencyLab.needParakeet");
    if (config.ttsMode === "browser" && (sopro.status !== "ready" || !voice)) return t("latencyLab.needSopro");
    if (config.ttsMode === "browser-pocket" && pocket.status !== "ready") return t("latencyLab.needPocket");
    return null;
  }

  /** Must run inside the click: iPadOS only starts audio from a user gesture. */
  function unlockAudio(): AudioContext {
    speakerRef.current?.unlockAudio();
    if (!inputContextRef.current || inputContextRef.current.state === "closed") {
      inputContextRef.current = new AudioContext();
    }
    void inputContextRef.current.resume();
    return inputContextRef.current;
  }

  function turnContext(signal: AbortSignal): TurnContext {
    return {
      projectId: project!.id,
      language: project!.spokenLanguage,
      config,
      configLabel,
      llmModel,
      speaker: speakerRef.current!,
      inputContext: inputContextRef.current!,
      parakeet: parakeetRef.current,
      browserTts: config.ttsMode === "browser" ? soproRef.current : config.ttsMode === "browser-pocket" ? pocketRef.current : null,
      history: historyRef.current,
      signal,
      onPartial: setPartial,
      onReplyText: setReply,
    };
  }

  /** Runs the inputs one after another; `freshConversation(i)` starts a new conversation before
   * input i. */
  async function runInputs(
    inputs: TurnInput[],
    label: (index: number) => string,
    freshConversation: (index: number) => boolean = () => false,
  ) {
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    const runId = new Date().toISOString();
    try {
      for (let i = 0; i < inputs.length && !controller.signal.aborted; i++) {
        setProgressText(label(i));
        if (freshConversation(i)) historyRef.current.length = 0;
        setPartial("");
        setReply("");
        const result = await runTurn(turnContext(controller.signal), inputs[i], runId, i + 1);
        setResults((prev) => [...prev, result]);
        if (i < inputs.length - 1 && !controller.signal.aborted) await new Promise((r) => setTimeout(r, pauseMs));
      }
    } finally {
      setBusy(false);
      setProgressText(null);
      abortRef.current = null;
    }
  }

  function start(spoken: boolean, run: () => Promise<void>) {
    const problem = setupProblem(spoken);
    setSetupError(problem);
    if (problem) return;
    unlockAudio();
    void run();
  }

  function sendText() {
    const message = text.trim();
    if (!message) return;
    start(false, async () => {
      setText("");
      await runInputs([{ kind: "text", text: message }], () => t("latencyLab.runningOne"));
    });
  }

  function sendClip(clip: Clip) {
    start(true, () => runInputs([{ kind: "clip", clip }], () => t("latencyLab.runningOne")));
  }

  function toggleMic() {
    if (recording) {
      stopMicRef.current?.();
      return;
    }
    start(true, async () => {
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      } catch (error) {
        setSetupError(error instanceof Error ? error.message : String(error));
        return;
      }
      const stopped = new Promise<void>((resolve) => (stopMicRef.current = resolve));
      setRecording(true);
      void stopped.then(() => setRecording(false));
      try {
        await runInputs([{ kind: "mic", stream, stopped }], () => t("latencyLab.recordingNow"));
      } finally {
        stream.getTracks().forEach((track) => track.stop());
        stopMicRef.current = null;
        setRecording(false);
      }
    });
  }

  const parsedScript = parseScript(script, clips);
  function runScript() {
    const { inputs, missing } = parsedScript;
    if (missing.length) {
      setSetupError(t("latencyLab.missingClips", { names: missing.join(", ") }));
      return;
    }
    if (!inputs.length) return;
    const spoken = inputs.some((input) => input.kind !== "text");
    start(spoken, async () => {
      const all: TurnInput[] = [];
      for (let r = 0; r < repeats; r++) all.push(...inputs);
      await runInputs(
        all,
        (i) =>
          t("latencyLab.runningScript", {
            turn: (i % inputs.length) + 1,
            turns: inputs.length,
            repeat: Math.floor(i / inputs.length) + 1,
            repeats,
          }),
        // Each repeat is a new conversation, so every repeat measures the same thing.
        (i) => i % inputs.length === 0,
      );
    });
  }

  function stop() {
    stopMicRef.current?.();
    abortRef.current?.abort();
    speakerRef.current?.stopSpeaking();
  }

  function resetConversation() {
    historyRef.current.length = 0;
    setPartial("");
    setReply("");
  }

  const summary = summarize(results);
  const headlineColumns: MetricColumn[] = ["sttMs", "llmFirstTokenMs", "firstChunkTtsMs", "requestToFirstChunkMs", "firstAudioMs", "playbackEndMs", "playbackGapMs", "avatarFps"];
  const exportName = `latency-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}`;

  const set = <K extends keyof LabConfig>(key: K, value: LabConfig[K]) => setConfig((prev) => ({ ...prev, [key]: value }));

  return (
    <div className={styles.page}>
      <HeadingWithInfo as="h2" className={styles.header} title={t("latencyLab.title")} info={t("latencyLab.subtitle")} />

      <section className={styles.card}>
        <h3>{t("latencyLab.setupTitle")}</h3>
        <div className={styles.grid}>
          <label className={styles.field}>
            <span className={styles.label}>{t("latencyLab.project")}</span>
            <select className={styles.select} value={projectId} onChange={(e) => setProjectId(e.target.value)} disabled={busy}>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>{t("latencyLab.stt")}</span>
            <select
              className={styles.select}
              value={config.sttMode}
              onChange={(e) => set("sttMode", e.target.value as SttMode)}
              disabled={busy}
            >
              <option value="device" disabled={!browserSttAvailable}>
                {t("latencyLab.sttDevice")}
              </option>
              <option value="server-project">{t("latencyLab.sttServerProject")}</option>
              <option value="server-whisper">{t("latencyLab.sttServerWhisper")}</option>
              <option value="server-parakeet" disabled={!parakeetServerAvailable}>
                {t("latencyLab.sttServerParakeet")}
              </option>
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>{t("latencyLab.llm")}</span>
            <select
              className={styles.select}
              value={config.llmKeyId ?? ""}
              onChange={(e) => set("llmKeyId", e.target.value || null)}
              disabled={busy}
            >
              <option value="">{t("latencyLab.llmProject")}</option>
              {llmKeys.map((key) => (
                <option key={key.id} value={key.id}>
                  {modelLabel(key, specs) ?? key.modelId ?? key.provider}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>{t("latencyLab.tts")}</span>
            <select
              className={styles.select}
              value={config.ttsMode}
              onChange={(e) => set("ttsMode", e.target.value as TtsMode)}
              disabled={busy}
            >
              <option value="project">{t("latencyLab.ttsProject")}</option>
              <option value="local" disabled={!localTtsAvailable}>
                {t("latencyLab.ttsLocal")}
              </option>
              <option value="browser">{t("latencyLab.ttsBrowser")}</option>
              <option value="browser-pocket">{t("latencyLab.ttsBrowserPocket")}</option>
              <option value="none">{t("latencyLab.ttsNone")}</option>
            </select>
          </label>
        </div>
        <div className={styles.checks}>
          <label>
            <input type="checkbox" checked={config.streaming} onChange={(e) => set("streaming", e.target.checked)} disabled={busy} />{" "}
            {t("latencyLab.streaming")}
          </label>
          <label>
            <input type="checkbox" checked={config.avatar} onChange={(e) => set("avatar", e.target.checked)} disabled={busy} />{" "}
            {t("latencyLab.avatar")}
          </label>
        </div>
        {project && config.ttsMode === "project" && !project.ttsEnabled && (
          <Callout variant="warning">{t("latencyLab.projectTtsOff")}</Callout>
        )}
        <p className={styles.hint}>{t("latencyLab.notSaved")}</p>
      </section>

      <section className={styles.card}>
        <h3>{t("latencyLab.deviceTitle")}</h3>
        <dl className={styles.facts}>
          <dt>{t("latencyLab.browser")}</dt>
          <dd className={styles.mono}>{device.userAgent}</dd>
          <dt>WebGPU</dt>
          <dd>{device.webGpu ? t("latencyLab.yes") : t("latencyLab.no")}</dd>
          <dt>{t("latencyLab.memory")}</dt>
          <dd>{device.deviceMemoryGb != null ? `≥ ${device.deviceMemoryGb} GB` : "–"}</dd>
          <dt>{t("latencyLab.cores")}</dt>
          <dd>{device.cpuCores ?? "–"}</dd>
          <dt>{t("latencyLab.screen")}</dt>
          <dd>
            {device.screen} @ {device.pixelRatio}x
          </dd>
        </dl>

        <div className={styles.engine}>
          <div className={styles.engineHead}>
            <strong>{t("latencyLab.parakeetTitle")}</strong>
            <Button size="sm" onClick={() => void loadParakeet()} disabled={busy || parakeet.status === "loading"}>
              {t("latencyLab.load")}
            </Button>
          </div>
          <EngineLine state={parakeet} />
        </div>

        <div className={styles.engine}>
          <div className={styles.engineHead}>
            <HeadingWithInfo
              as="h4"
              className={styles.engineTitle}
              title={t("latencyLab.soproTitle")}
              info={t("latencyLab.soproHint")}
            />
            <Button size="sm" onClick={() => void loadSopro()} disabled={busy || sopro.status === "loading"}>
              {t("latencyLab.load")}
            </Button>
          </div>
          <Input
            label={t("latencyLab.soproModelUrl")}
            placeholder="https://huggingface.co/samuel-vitorino/sopro-v2-turbo-onnx/resolve/main"
            value={soproModelUrl}
            onChange={(e) => setSoproModelUrl(e.target.value)}
          />
          <EngineLine state={sopro} extra={sopro.backend ? t("latencyLab.soproBackend", { backend: sopro.backend }) : null} />
          {sopro.status === "ready" && (
            <div className={styles.voiceRow}>
              <label className={styles.field}>
                <span className={styles.label}>{t("latencyLab.soproVoice")}</span>
                <select className={styles.select} defaultValue="" onChange={(e) => void pickVoiceClip(e.target.value)} disabled={busy}>
                  <option value="" disabled>
                    {t("latencyLab.soproVoicePick")}
                  </option>
                  {(clipsQuery.data ?? []).map((clip) => (
                    <option key={clip.id} value={clip.id}>
                      {clip.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className={styles.fileButton}>
                <Upload size={16} /> {t("latencyLab.soproVoiceUpload")}
                <input
                  type="file"
                  accept="audio/*"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void setSoproVoice(file.name, file);
                  }}
                />
              </label>
            </div>
          )}
          {voice && <p className={styles.hint}>{t("latencyLab.soproVoiceReady", { name: voice.name, ms: ms(voice.prepareMs) })}</p>}
          {voiceError && <p className={styles.error}>{voiceError}</p>}
        </div>

        <div className={styles.engine}>
          <div className={styles.engineHead}>
            <HeadingWithInfo
              as="h4"
              className={styles.engineTitle}
              title={t("latencyLab.pocketTitle")}
              info={t("latencyLab.pocketHint")}
            />
            <Button size="sm" onClick={() => void loadPocket()} disabled={busy || pocket.status === "loading"}>
              {t("latencyLab.load")}
            </Button>
          </div>
          <EngineLine state={pocket} extra={pocket.backend ? t("latencyLab.soproBackend", { backend: pocket.backend }) : null} />
          {pocket.status === "ready" && pocketRef.current?.voice && (
            <p className={styles.hint}>
              {t("latencyLab.pocketVoice", { voice: pocketRef.current.voice, credit: pocketRef.current.voiceCredit ?? "" })}
            </p>
          )}
          {project && project.spokenLanguage !== "de" && <p className={styles.hint}>{t("latencyLab.pocketGermanOnly")}</p>}
        </div>
      </section>

      <div className={styles.stageRow}>
        <div className={styles.stage}>
          {config.avatar ? (
            <TalkingHeadAvatar
              key={project?.avatarModelUrl ?? "default"}
              ref={speakerRef}
              avatarUrl={project?.avatarModelUrl ?? undefined}
              speechEnabled
              fallback={<span className={styles.hint}>{t("latencyLab.avatarFallback")}</span>}
            />
          ) : (
            <>
              <AudioOnlySpeaker ref={speakerRef} />
              <span className={styles.hint}>{t("latencyLab.audioOnly")}</span>
            </>
          )}
        </div>
        <div className={styles.live}>
          <h3>{t("latencyLab.liveTitle")}</h3>
          {progressText && <p className={styles.progress}>{progressText}</p>}
          <p className={styles.label}>{t("latencyLab.transcript")}</p>
          <p className={styles.transcript}>{partial || "…"}</p>
          <p className={styles.label}>{t("latencyLab.reply")}</p>
          <p className={styles.transcript}>{reply || "…"}</p>
          <div className={styles.buttons}>
            {busy && (
              <Button variant="danger" size="sm" onClick={stop}>
                <Square size={14} /> {t("latencyLab.stop")}
              </Button>
            )}
            <Button size="sm" onClick={resetConversation} disabled={busy}>
              {t("latencyLab.resetConversation")}
            </Button>
          </div>
        </div>
      </div>

      {setupError && <Callout variant="warning">{setupError}</Callout>}

      <section className={styles.card}>
        <HeadingWithInfo title={t("latencyLab.singleTitle")} info={t("latencyLab.clipsHint")} />
        <form
          className={styles.sendRow}
          onSubmit={(e) => {
            e.preventDefault();
            sendText();
          }}
        >
          <Input label={t("latencyLab.textMessage")} value={text} onChange={(e) => setText(e.target.value)} disabled={busy} />
          <Button type="submit" variant="accent" disabled={busy || !text.trim()}>
            {t("latencyLab.send")}
          </Button>
        </form>
        <div className={styles.buttons}>
          <Button variant={recording ? "danger" : "default"} onClick={toggleMic} disabled={busy && !recording}>
            {recording ? <Square size={16} /> : <Mic size={16} />}{" "}
            {recording ? t("latencyLab.micStop") : t("latencyLab.micStart")}
          </Button>
          <label className={styles.fileButton}>
            <Upload size={16} /> {t("latencyLab.addClips")}
            <input type="file" accept="audio/*" multiple onChange={(e) => {
              addClips(e.target.files);
              e.target.value = "";
            }} />
          </label>
        </div>
        {clips.length > 0 && (
          <ul className={styles.clipList}>
            {clips.map((clip) => (
              <li key={clip.id}>
                <span className={styles.mono}>{clip.name}</span>
                <span className={styles.buttons}>
                  <Button size="sm" onClick={() => sendClip(clip)} disabled={busy} aria-label={t("latencyLab.playClip")} title={t("latencyLab.playClip")}>
                    <Play size={14} />
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => setClips((prev) => prev.filter((c) => c.id !== clip.id))}
                    disabled={busy}
                    aria-label={t("latencyLab.removeClip")}
                    title={t("latencyLab.removeClip")}
                  >
                    <Trash2 size={14} />
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.card}>
        <HeadingWithInfo title={t("latencyLab.scriptTitle")} info={t("latencyLab.scriptHint")} />
        <Textarea label={t("latencyLab.script")} value={script} onChange={(e) => setScript(e.target.value)} rows={6} disabled={busy} />
        <div className={styles.grid}>
          <Input
            label={t("latencyLab.repeats")}
            type="number"
            min={1}
            max={50}
            value={repeats}
            onChange={(e) => setRepeats(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            disabled={busy}
          />
          <Input
            label={t("latencyLab.pause")}
            type="number"
            min={0}
            step={500}
            value={pauseMs}
            onChange={(e) => setPauseMs(Math.max(0, Number(e.target.value) || 0))}
            disabled={busy}
          />
        </div>
        <p className={styles.hint}>
          {t("latencyLab.scriptCount", { turns: parsedScript.inputs.length * repeats })}
        </p>
        <div className={styles.buttons}>
          <Button variant="accent" onClick={runScript} disabled={busy || !parsedScript.inputs.length}>
            <Play size={16} /> {t("latencyLab.runScript")}
          </Button>
        </div>
      </section>

      <section className={styles.card}>
        <div className={styles.engineHead}>
          <HeadingWithInfo title={t("latencyLab.resultsTitle")} info={t("latencyLab.resultsHint")} />
          <div className={styles.buttons}>
            <Button size="sm" disabled={!results.length} onClick={() => downloadFile(toCsv(results, device), `${exportName}.csv`, "text/csv")}>
              CSV
            </Button>
            <Button
              size="sm"
              disabled={!results.length}
              onClick={() => downloadFile(JSON.stringify({ device, results }, null, 2), `${exportName}.json`, "application/json")}
            >
              JSON
            </Button>
            <Button size="sm" disabled={!results.length} onClick={() => void navigator.clipboard.writeText(toCsv(results, device))}>
              {t("latencyLab.copy")}
            </Button>
            <Button size="sm" disabled={!results.length || busy} onClick={() => setResults([])}>
              {t("latencyLab.clear")}
            </Button>
          </div>
        </div>

        {summary.length > 0 && (
          <>
            <HeadingWithInfo as="h4" title={t("latencyLab.summaryTitle")} info={t("latencyLab.summaryHint")} />
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>{t("latencyLab.config")}</th>
                    <th>n</th>
                    {headlineColumns.map((column) => (
                      <th key={column}>{t(`latencyLab.metric.${column}`)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {summary.map((row) => (
                    <tr key={row.config}>
                      <td className={styles.configCell}>{row.config}</td>
                      <td>
                        {row.turns}
                        {row.errors > 0 && <span className={styles.error}> ({t("latencyLab.errors", { count: row.errors })})</span>}
                      </td>
                      {headlineColumns.map((column) => (
                        <td key={column} className={column === "firstAudioMs" ? styles.headline : undefined}>
                          {ms(row.median[column])}
                          <span className={styles.p90}> / {ms(row.p90[column])}</span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {results.length > 0 && (
          <>
            <h4>{t("latencyLab.turnsTitle")}</h4>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t("latencyLab.config")}</th>
                    <th>{t("latencyLab.input")}</th>
                    {METRIC_COLUMNS.map((column) => (
                      <th key={column}>{t(`latencyLab.metric.${column}`)}</th>
                    ))}
                    <th>{t("latencyLab.chunks")}</th>
                    <th>{t("latencyLab.contextLosses")}</th>
                    <th>{t("latencyLab.errorColumn")}</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((row, index) => (
                    <tr key={`${row.runId}-${row.turn}-${index}`}>
                      <td>{row.turn}</td>
                      <td className={styles.configCell}>{row.config}</td>
                      <td className={styles.configCell} title={row.transcript ?? undefined}>
                        {row.inputKind === "text" ? row.input : `${row.input} → ${row.transcript ?? "?"}`}
                      </td>
                      {METRIC_COLUMNS.map((column) => (
                        <td key={column} className={column === "firstAudioMs" ? styles.headline : undefined}>
                          {column === "avatarFps" ? (row.avatarFps?.toFixed(1) ?? "–") : ms(row[column])}
                        </td>
                      ))}
                      <td>{row.chunks}</td>
                      <td>{row.contextLosses}</td>
                      <td className={styles.error}>{row.error ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <details className={styles.glossary}>
          <summary>{t("latencyLab.glossaryTitle")}</summary>
          <dl>
            {METRIC_COLUMNS.map((column) => (
              <div key={column}>
                <dt>{t(`latencyLab.metric.${column}`)}</dt>
                <dd>{t(`latencyLab.metricHelp.${column}`)}</dd>
              </div>
            ))}
          </dl>
        </details>
      </section>
    </div>
  );
}

function EngineLine({ state, extra = null }: { state: EngineState; extra?: string | null }) {
  const { t } = useTranslation();
  return (
    <p className={styles.mono}>
      {t("latencyLab.engineStatus", { status: state.status })}
      {state.status === "loading" && state.progress && state.progress.totalBytes > 0 &&
        ` — ${percent(state.progress)}% (${megabytes(state.progress.loadedBytes)} / ${megabytes(state.progress.totalBytes)})`}
      {state.loadSeconds != null && ` — ${t("latencyLab.loadedIn", { seconds: state.loadSeconds.toFixed(1) })}`}
      {extra && ` — ${extra}`}
      {state.error && <span className={styles.error}> — {state.error}</span>}
    </p>
  );
}
