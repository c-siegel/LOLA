import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import type { TalkingHeadAvatarHandle } from "./TalkingHeadAvatar";

interface AudioOnlySpeakerProps {
  /** Called once the audio output exists — same role as TalkingHeadAvatar's onReady (e.g. to
   * autoplay the greeting), just without a 3D model to wait for. */
  onReady?: () => void;
}

// Stand-in for TalkingHeadAvatar on a "chat only" page (see pages/PublicChat/index.tsx): plays the
// same speech through a plain AudioContext, with the same handle, so the chat code doesn't care
// which one it talks to — but without downloading or rendering a 3D model. Renders nothing; the
// avatar-only methods (gaze, listening, FPS) are no-ops.
export const AudioOnlySpeaker = forwardRef<TalkingHeadAvatarHandle, AudioOnlySpeakerProps>(function AudioOnlySpeaker(
  { onReady },
  ref,
) {
  const audioCtxRef = useRef<AudioContext | null>(null);
  // Every scheduled source until it ends — non-empty means "speaking" (TalkingHead's isSpeaking).
  const sourcesRef = useRef(new Set<AudioBufferSourceNode>());
  // When the last queued buffer finishes, so consecutive buffers (a streamed reply's chunks) play
  // one after another instead of on top of each other — TalkingHead queues them the same way.
  const queueEndRef = useRef(0);

  useEffect(() => {
    const audioCtx = new AudioContext();
    audioCtxRef.current = audioCtx;
    onReady?.();
    return () => {
      audioCtxRef.current = null;
      sourcesRef.current.clear();
      void audioCtx.close();
    };
    // onReady deliberately not in the deps — same reason as in TalkingHeadAvatar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useImperativeHandle(ref, () => {
    function speakBuffer(audioBuffer: AudioBuffer) {
      const audioCtx = audioCtxRef.current;
      if (!audioCtx) return;
      const source = audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioCtx.destination);
      const startAt = Math.max(audioCtx.currentTime, queueEndRef.current);
      source.start(startAt);
      queueEndRef.current = startAt + audioBuffer.duration;
      sourcesRef.current.add(source);
      source.onended = () => sourcesRef.current.delete(source);
    }

    return {
      async decodeAudio(audioBase64: string) {
        const audioCtx = audioCtxRef.current;
        if (!audioCtx) throw new Error("Audioausgabe noch nicht bereit.");
        const bytes = Uint8Array.from(atob(audioBase64), (c) => c.charCodeAt(0));
        return audioCtx.decodeAudioData(bytes.buffer);
      },
      speakBuffer,
      // speakBuffer already plays consecutive buffers without a gap here.
      queueBuffer: speakBuffer,
      stopSpeaking() {
        for (const source of sourcesRef.current) {
          source.onended = null;
          source.stop();
        }
        sourcesRef.current.clear();
        queueEndRef.current = 0;
      },
      async waitUntilIdle(signal?: AbortSignal) {
        const POLL_INTERVAL_MS = 50;
        while (sourcesRef.current.size > 0 && !signal?.aborted) {
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        }
      },
      // Same contract as TalkingHeadAvatar's speakFromUrl (see there): resume before scheduling,
      // report whether it's actually audible, and don't start a second copy of the greeting.
      async speakFromUrl(url: string) {
        const audioCtx = audioCtxRef.current;
        if (!audioCtx) return false;
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`speakFromUrl: ${url} responded with ${response.status}`);
        }
        const audioBuffer = await audioCtx.decodeAudioData(await response.arrayBuffer());
        if (audioCtx.state === "suspended") {
          try {
            await audioCtx.resume();
          } catch {
            // Browser refused — reported as "not audible" below.
          }
        }
        if (audioCtx.state !== "running") return false;
        if (sourcesRef.current.size === 0) {
          speakBuffer(audioBuffer);
        }
        return true;
      },
      unlockAudio() {
        const audioCtx = audioCtxRef.current;
        if (audioCtx?.state === "suspended") {
          audioCtx.resume().catch(() => {});
        }
      },
      startListening() {},
      stopListening() {},
      startThinking() {},
      stopThinking() {},
      playMotions() {},
      stopMotion() {},
      startFpsTracking() {},
      stopFpsTracking() {
        return null;
      },
    };
  }, []);

  return null;
});
