// On-device speech recognition on its own: runs the loaded model on the microphone or an audio
// file, without a project, LLM or TTS, and shows how fast it keeps up on this device — time to the
// first live text, re-decode times, and the delay between stopping and the final text. The
// numbers docs/stt-device-test.md asks for; nothing is sent to the server.

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Mic, Square, Upload } from "lucide-react";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { HeadingWithInfo } from "@/components/InfoTip";
import type { DecodeStats, ParakeetSttEngine, StreamingSession } from "@/lib/parakeetStt";
import styles from "./LatencyLab.module.css";

interface RunStats {
  firstPartialMs: number | null;
  decodes: DecodeStats[];
  finalizeMs: number | null;
}

const emptyRun = (): RunStats => ({ firstPartialMs: null, decodes: [], finalizeMs: null });
const ms = (n: number | null | undefined) => (n == null ? "–" : `${Math.round(n)} ms`);

interface SttCheckProps {
  /** The page's loaded engine; null or not ready until "Load" has finished. */
  engine: ParakeetSttEngine | null;
  ready: boolean;
  loadSeconds: number | null;
  /** Another measurement is running. */
  disabled: boolean;
  onRunningChange: (running: boolean) => void;
}

export function SttCheck({ engine, ready, loadSeconds, disabled, onRunningChange }: SttCheckProps) {
  const { t } = useTranslation();
  const sessionRef = useRef<StreamingSession | null>(null);
  const releaseRef = useRef<(() => void) | null>(null);
  const startedAtRef = useRef(0);
  const [running, setRunning] = useState(false);
  const [partial, setPartial] = useState("");
  const [finalText, setFinalText] = useState("");
  const [run, setRun] = useState<RunStats>(emptyRun);
  const [error, setError] = useState<string | null>(null);

  useEffect(
    () => () => {
      void sessionRef.current?.stop().catch(() => undefined);
      releaseRef.current?.();
    },
    [],
  );

  function changeRunning(value: boolean) {
    setRunning(value);
    onRunningChange(value);
  }

  function onPartial(text: string) {
    setPartial(text);
    setRun((prev) =>
      prev.firstPartialMs == null && text ? { ...prev, firstPartialMs: performance.now() - startedAtRef.current } : prev,
    );
  }

  async function start(stream: MediaStream, audioContext: AudioContext, release: () => void) {
    setPartial("");
    setFinalText("");
    setRun(emptyRun());
    setError(null);
    releaseRef.current = release;
    engine!.onStats = (stats) => setRun((prev) => ({ ...prev, decodes: [...prev.decodes, stats] }));
    startedAtRef.current = performance.now();
    sessionRef.current = await engine!.startStreaming(stream, audioContext, onPartial);
    changeRunning(true);
  }

  async function startMic() {
    // Created before the permission prompt: iPadOS only starts audio from the click itself.
    const audioContext = new AudioContext();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      await start(stream, audioContext, () => stream.getTracks().forEach((track) => track.stop()));
    } catch (err) {
      fail(err);
    }
  }

  // Plays a recorded clip through the speakers and the recognizer at real-time speed, so a device
  // can be measured with the same known audio every time.
  async function startFile(file: File) {
    const audioContext = new AudioContext();
    const playback = new AudioContext();
    const url = URL.createObjectURL(file);
    const element = new Audio(url);
    const source = playback.createMediaElementSource(element);
    const destination = playback.createMediaStreamDestination();
    source.connect(destination);
    source.connect(playback.destination);
    element.onended = () => void stop();
    try {
      await start(destination.stream, audioContext, () => {
        element.pause();
        void playback.close();
        URL.revokeObjectURL(url);
      });
      await element.play();
    } catch (err) {
      fail(err);
    }
  }

  async function stop() {
    const session = sessionRef.current;
    if (!session) return;
    sessionRef.current = null;
    const stoppedAt = performance.now();
    try {
      const result = await session.stop();
      setFinalText(result.text);
      setRun((prev) => ({ ...prev, finalizeMs: performance.now() - stoppedAt }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      finish();
    }
  }

  function fail(err: unknown) {
    setError(err instanceof Error ? err.message : String(err));
    void sessionRef.current?.stop().catch(() => undefined);
    sessionRef.current = null;
    finish();
  }

  function finish() {
    releaseRef.current?.();
    releaseRef.current = null;
    if (engine) engine.onStats = null;
    changeRunning(false);
  }

  const decodeTimes = run.decodes.map((d) => d.decodeMs);
  const last = run.decodes[run.decodes.length - 1];
  const average = decodeTimes.length ? decodeTimes.reduce((a, b) => a + b, 0) / decodeTimes.length : null;
  const max = decodeTimes.length ? Math.max(...decodeTimes) : null;

  // Plain "key: value" lines for the device notes, in the names the runbook's table uses.
  const report = [
    `userAgent: ${navigator.userAgent}`,
    `modelReadySeconds: ${loadSeconds?.toFixed(1) ?? "-"}`,
    `firstPartialMs: ${run.firstPartialMs != null ? Math.round(run.firstPartialMs) : "-"}`,
    `decodeMs last/avg/max: ${ms(last?.decodeMs)} / ${ms(average)} / ${ms(max)} (${run.decodes.length} decodes)`,
    `lastDecodedAudioSeconds: ${last ? last.audioSeconds.toFixed(1) : "-"}`,
    `finalizeMs: ${run.finalizeMs != null ? Math.round(run.finalizeMs) : "-"}`,
    `final: ${finalText}`,
  ].join("\n");

  return (
    <div className={styles.engine}>
      <HeadingWithInfo
        as="h4"
        className={styles.engineTitle}
        title={t("latencyLab.sttCheck.title")}
        info={t("latencyLab.sttCheck.hint")}
      />
      {!ready && <p className={styles.hint}>{t("latencyLab.sttCheck.needModel")}</p>}
      <div className={styles.buttons}>
        {running ? (
          <Button variant="danger" size="sm" onClick={() => void stop()}>
            <Square size={14} /> {t("latencyLab.sttCheck.stop")}
          </Button>
        ) : (
          <>
            <Button size="sm" onClick={() => void startMic()} disabled={!ready || disabled}>
              <Mic size={14} /> {t("latencyLab.sttCheck.startMic")}
            </Button>
            <label className={styles.fileButton} aria-disabled={!ready || disabled}>
              <Upload size={14} /> {t("latencyLab.sttCheck.playFile")}
              <input
                type="file"
                accept="audio/*"
                disabled={!ready || disabled}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) void startFile(file);
                }}
              />
            </label>
          </>
        )}
      </div>
      {error && <Callout variant="danger">{t("latencyLab.sttCheck.error", { message: error })}</Callout>}

      <p className={styles.label}>{t("latencyLab.sttCheck.partial")}</p>
      <p className={styles.transcript}>{partial || "…"}</p>
      <p className={styles.label}>{t("latencyLab.sttCheck.final")}</p>
      <p className={styles.transcript}>{finalText || "…"}</p>

      <dl className={styles.facts}>
        <dt>{t("latencyLab.sttCheck.firstPartial")}</dt>
        <dd>{ms(run.firstPartialMs)}</dd>
        <dt>{t("latencyLab.sttCheck.decode")}</dt>
        <dd>
          {ms(last?.decodeMs)} / {ms(average)} / {ms(max)}
        </dd>
        <dt>{t("latencyLab.sttCheck.decodedAudio")}</dt>
        <dd>{last ? `${last.audioSeconds.toFixed(1)} s` : "–"}</dd>
        <dt>{t("latencyLab.sttCheck.finalize")}</dt>
        <dd>{ms(run.finalizeMs)}</dd>
      </dl>
      <div className={styles.buttons}>
        <Button size="sm" onClick={() => void navigator.clipboard.writeText(report)} disabled={running}>
          {t("latencyLab.sttCheck.copy")}
        </Button>
      </div>
    </div>
  );
}
