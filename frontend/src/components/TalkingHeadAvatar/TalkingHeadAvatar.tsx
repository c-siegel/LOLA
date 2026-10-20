import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react";
import type { TalkingHead } from "@met4citizen/talkinghead";
import { Loader2 } from "lucide-react";
import type { MotionEngine } from "@/vendor/motion-engine/MotionEngine.js";
import { attachHeadAudio } from "./headAudioIntegration";
import styles from "./TalkingHeadAvatar.module.css";
import { sitePath } from "@/lib/basePath";

// Self-hosted under public/avatars/ (see ATTRIBUTION.md there) instead of loaded live from GitHub —
// for the same privacy reason as the fonts (no third-party request on every page view).
// Alternative in the same repo: david.glb.
const DEFAULT_AVATAR_URL = sitePath("/avatars/julia.glb");

type Status = "loading" | "ready" | "error" | "skipped";

// How long the "thinking" gaze cue holds before it self-clears back to idle (see startThinking).
// Not a measured value — a reasonable guess for a typical LLM+TTS round trip; if the real reply
// usually takes longer, the avatar just reads as idle again for the remainder of the wait.
const THINKING_LOOK_MS = 4000;

// Gestures that read as "pondering" while a reply is being generated (see startThinking and the
// thinkingMotionsEnabled prop). Names from vendor/motion-engine/motions.json; one is picked at
// random each round, never the same twice in a row.
const THINKING_MOTIONS = ["thinking_face", "raise_eyebrows"];
// Random pause between two thinking gestures, so it doesn't look like a loop.
const THINKING_PAUSE_MIN_MS = 800;
const THINKING_PAUSE_MAX_MS = 2200;

// TalkingHead throttles its own internal render loop to this rate (its "modelFPS" option,
// currently at its own default) — passed explicitly here so a future change to that library
// default can't silently desync the dropped-frame math in startFpsTracking/stopFpsTracking below.
const MODEL_FPS = 30;
const TARGET_FRAME_INTERVAL_MS = 1000 / MODEL_FPS;

// TalkingHead renders at the full devicePixelRatio with 4x MSAA, so on an iPad (ratio 2) the
// canvas's GPU buffers are ~1.8x the size they'd be at 1.5. The public chat shares the GPU with
// the on-device speech recognition model (lib/parakeetStt.ts, ~350 MB on WebGPU), and under that
// memory pressure iPadOS drops the WebGL context — the avatar turns black. The face is a small
// part of the screen, so the slightly softer image is the better trade.
const MAX_PIXEL_RATIO = 1.5;

// After a lost WebGL context, three.js restores it by itself once the browser hands it back. If
// that hasn't happened within this time, the avatar is rebuilt from scratch instead of staying
// black — but only between replies, never cutting off the avatar's speech.
const CONTEXT_RESTORE_WAIT_MS = 3000;
// A device that keeps losing the context would otherwise reload the avatar in a loop; after this
// many rebuilds the fallback image stays up.
const MAX_CONTEXT_REBUILDS = 2;

interface FpsTracker {
  running: boolean;
  startedAt: number;
  lastTickAt: number;
  sampleCount: number;
  droppedFrames: number;
}

export interface FpsTrackingResult {
  avgFps: number | null;
  droppedFrames: number;
  sampleCount: number;
}

export interface TalkingHeadAvatarHandle {
  /**
   * Decodes base64 MP3 audio (from features/ai/tts, one chunk for a streamed reply, see
   * api/publicChat.ts::sendMessageStream) into a playable buffer, without starting playback —
   * split from speakBuffer() so callers can time/sequence playback themselves (wait out
   * audioBuffer.duration, then call stopSpeaking() — see pages/PublicChat/index.tsx).
   */
  decodeAudio: (audioBase64: string) => Promise<AudioBuffer>;
  /** Plays an already-decoded buffer from decodeAudio(). */
  speakBuffer: (audioBuffer: AudioBuffer) => void;
  /**
   * Plays `audioBuffer` seamlessly after the previous queueBuffer() call's audio — for speech that
   * arrives in short pieces cut mid-word (an in-browser TTS engine, see lib/browserTts.ts).
   * speakBuffer() can't do that on the avatar: TalkingHead only starts its next buffer from the
   * previous one's `ended` event, which leaves an audible gap at every seam.
   */
  queueBuffer: (audioBuffer: AudioBuffer) => void;
  /** Immediately stops any audio currently playing (mid-sentence included), clears the speech
   * queue, and resets the mouth to its idle shape. Used both when the student interrupts a reply,
   * and — just as important — right after a reply's last chunk naturally finishes: HeadAudio drives
   * the mouth from the live audio signal (see headAudioIntegration.ts), not from TalkingHead's own
   * keyframe timeline, so nothing else puts the mouth back to idle once that signal goes quiet.
   * Safe to call even when nothing is playing. See pages/PublicChat/index.tsx. */
  stopSpeaking: () => void;
  /**
   * Waits until TalkingHead has actually finished playing everything it has queued — not just
   * the caller's own duration estimate. speakAudio() inserts a short pause between consecutive
   * speech-queue items (see the vendored talkinghead.mjs), so chaining playback purely off each
   * chunk's own AudioBuffer.duration (see playStreamedChunk in pages/PublicChat/index.tsx) drifts
   * increasingly behind real playback the more chunks a reply has. Calling stopSpeaking() as soon
   * as that drifted timer runs out then cuts off whatever TalkingHead is still catching up on —
   * typically the tail of the last chunk. Resolves early if `signal` aborts.
   */
  waitUntilIdle: (signal?: AbortSignal) => Promise<void>;
  /**
   * Fetches an audio file (e.g. a project's pre-generated start-prompt audio, see
   * api/projects.ts::generateStartAudio) and plays it directly — skips the base64 round trip
   * decodeAudio() needs, since here the audio never has to travel through a JSON response first.
   * Resolves to whether the audio is actually audible (the AudioContext is "running"), not just
   * whether decoding/scheduling succeeded — starting an audio source never throws even while the
   * browser has the context suspended pending a user gesture, so a plain success/failure result
   * can't tell a caller apart from silent playback that never actually made a sound.
   */
  speakFromUrl: (url: string) => Promise<boolean>;
  /**
   * Resumes the avatar's suspended AudioContext right away. Call it synchronously from a click
   * handler: some browsers (Safari) only allow that during the gesture itself, and speakFromUrl
   * only gets to its own resume() after fetching and decoding the audio. A no-op before the avatar
   * has loaded.
   */
  unlockAudio: () => void;
  /** Starts the library's "listening" eye-contact mode, fed by the microphone stream. */
  startListening: (stream: MediaStream) => void;
  /** Ends listening mode (e.g. when the recording is stopped). */
  stopListening: () => void;
  /** Short gaze cue for the wait between sending and the reply ("thinking") — not a native
   * TalkingHead feature, synthesized from lookAt() (see THINKING_LOOK_MS). With
   * thinkingMotionsEnabled (and motionEnabled) it also plays random thinking gestures until
   * stopThinking(). */
  startThinking: () => void;
  /** Ends the thinking gestures (the gaze cue resets itself after THINKING_LOOK_MS). A gesture
   * that is not a thinking one — e.g. a closing thumbs up of the reply — is left alone. */
  stopThinking: () => void;
  /** Plays the gestures the LLM marked in a reply (backend features/chat/motion.py), one after
   * the other. A no-op without motionEnabled or before the avatar has loaded. */
  playMotions: (names: string[]) => void;
  /** Cuts off a running gesture — for an interrupted reply. Deliberately not part of
   * stopSpeaking(), which also runs at a reply's natural end, where a closing gesture (e.g. a
   * thumbs up after the last sentence) should still finish. */
  stopMotion: () => void;
  /** Starts (or restarts) one FPS/dropped-frame measurement window — see stopFpsTracking. */
  startFpsTracking: () => void;
  /** Ends the current measurement window and returns its stats, or null if never started or the
   * avatar never actually rendered a real (non-throttled) frame during it (failed to load,
   * prefers-reduced-motion, or speechEnabled was false — head.opt.update is only wrapped when
   * speech is enabled, see the load() effect below). */
  stopFpsTracking: () => FpsTrackingResult | null;
}

interface TalkingHeadAvatarProps {
  /** Shown while loading, on errors, and when prefers-reduced-motion is active. */
  fallback: ReactNode;
  /** For the configurator preview (1e) and the public chat (1i) instead of the landing-page default. */
  avatarUrl?: string;
  /** Called once the avatar has actually finished loading (status "ready") — e.g. to trigger
   * autoplay of a project's spoken greeting, see pages/PublicChat/index.tsx. */
  onReady?: () => void;
  /**
   * Keeps the `fallback` covering the avatar even once it's loaded (status "ready") — default
   * true. Set to false while something the avatar is about to do (e.g. speaking a greeting)
   * hasn't started yet, so students see a static "poster frame" instead of the avatar idling in
   * the background behind a play button, like a paused video — see pages/PublicChat/index.tsx.
   */
  revealed?: boolean;
  /**
   * Enables HeadAudio (real-time audio-driven lipsync, see headAudioIntegration.ts) for actual
   * speech. Left out (landing-page use): no AudioWorklet/model loading overhead, since nothing is
   * ever spoken there.
   */
  speechEnabled?: boolean;
  /**
   * Optional background image — rendered HERE (not by the parent), as a sibling div right next to
   * the canvas, both with the same `position:absolute;inset:0` in the same parent. That way both
   * ALWAYS have exactly the same box, regardless of the canvas's current internal size — a size
   * mismatch between "avatar window" and "background window" (see earlier, failed attempts with the
   * background on the outer .avatarStage element) is ruled out structurally instead of only
   * happening to fit.
   */
  backgroundImageUrl?: string;
  /**
   * Loads the MotionEngine (src/vendor/motion-engine) so playMotions() can make the avatar
   * gesture — the project's motionEnabled setting. Left out (landing page): nothing to gesture to.
   */
  motionEnabled?: boolean;
  /** Random thinking gestures while the reply is generated (see startThinking). Needs motionEnabled. */
  thinkingMotionsEnabled?: boolean;
}

// 3D avatar rendering (met4citizen/TalkingHead). Purely idle on the landing page (no speech);
// in 1e/1i with speechEnabled for actual speech via HeadAudio (see headAudioIntegration.ts).
export const TalkingHeadAvatar = forwardRef<TalkingHeadAvatarHandle, TalkingHeadAvatarProps>(
  function TalkingHeadAvatar(
    {
      fallback,
      avatarUrl = DEFAULT_AVATAR_URL,
      onReady,
      revealed = true,
      speechEnabled = false,
      backgroundImageUrl,
      motionEnabled = false,
      thinkingMotionsEnabled = false,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null);
    const headRef = useRef<TalkingHead | null>(null);
    // queueBuffer()'s own playback, outside TalkingHead's speech queue: every source until it
    // ends, and when the last one finishes, so the next piece starts exactly there.
    const queuedSourcesRef = useRef(new Set<AudioBufferSourceNode>());
    const queueEndRef = useRef(0);
    const stopQueuedSources = () => {
      for (const source of queuedSourcesRef.current) {
        source.onended = null;
        try {
          source.stop();
        } catch {
          // Its context is already closed.
        }
        source.disconnect();
      }
      queuedSourcesRef.current.clear();
      queueEndRef.current = 0;
    };
    const motionEngineRef = useRef<MotionEngine | null>(null);
    // The imperative handle below is created once, so it reads the prop through a ref.
    const thinkingMotionsEnabledRef = useRef(thinkingMotionsEnabled);
    thinkingMotionsEnabledRef.current = thinkingMotionsEnabled;
    // Identifies the running thinking-gesture loop; changing it ends that loop.
    const thinkingRunRef = useRef(0);
    // Whether a thinking gesture is playing right now — only then does stopThinking() cut the
    // engine's action off.
    const thinkingGestureActiveRef = useRef(false);
    const [status, setStatus] = useState<Status>("loading");
    // Plain ref, not React state — ticked at ~MODEL_FPS Hz from inside TalkingHead's own render
    // loop, so must never trigger a re-render.
    const fpsTrackerRef = useRef<FpsTracker | null>(null);
    // Bumped to rebuild the avatar after a WebGL context loss that wasn't restored (see
    // CONTEXT_RESTORE_WAIT_MS).
    const [rebuildCount, setRebuildCount] = useState(0);
    // Which avatar onReady was last reported for: a rebuild must not report it again, or the
    // public chat would replay its spoken greeting.
    const readyReportedForRef = useRef<string | null>(null);

    useImperativeHandle(
      ref,
      () => ({
        async decodeAudio(audioBase64: string) {
          const head = headRef.current;
          if (!head) throw new Error("Avatar noch nicht geladen.");
          const bytes = Uint8Array.from(atob(audioBase64), (c) => c.charCodeAt(0));
          return head.audioCtx.decodeAudioData(bytes.buffer);
        },
        speakBuffer(audioBuffer: AudioBuffer) {
          // No words/wtimes/wdurations needed — HeadAudio drives the mouth live from the audio
          // played here, independently of this call.
          headRef.current?.speakAudio({ audio: audioBuffer });
        },
        queueBuffer(audioBuffer: AudioBuffer) {
          const head = headRef.current;
          if (!head) return;
          const audioCtx = head.audioCtx;
          if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
          const sources = queuedSourcesRef.current;
          if (sources.size === 0) {
            // What startSpeaking() does at the start of an utterance.
            head.lookAtCamera(500);
            head.speakWithHands();
          }
          head.isSpeaking = true;
          head.stateName = "speaking";
          const source = audioCtx.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(head.audioAnalyzerNode);
          const startAt = Math.max(audioCtx.currentTime, queueEndRef.current);
          source.start(startAt);
          queueEndRef.current = startAt + audioBuffer.duration;
          sources.add(source);
          source.onended = () => {
            source.disconnect();
            sources.delete(source);
            if (sources.size > 0) return;
            queueEndRef.current = 0;
            head.isSpeaking = false;
            head.stateName = "idle";
          };
        },
        stopSpeaking() {
          stopQueuedSources();
          headRef.current?.stopSpeaking();
        },
        async waitUntilIdle(signal?: AbortSignal) {
          const head = headRef.current;
          if (!head) return;
          const POLL_INTERVAL_MS = 50;
          while ((head.isSpeaking || queuedSourcesRef.current.size > 0) && !signal?.aborted) {
            await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
          }
        },
        async speakFromUrl(url: string) {
          const head = headRef.current;
          if (!head) return false;
          const response = await fetch(url);
          // Without this, a stale URL (DB says the audio exists, the file was since removed) hits
          // decodeAudioData with an error page's body instead of audio — it still throws, just
          // with a cryptic browser-internal message instead of one that says what actually failed.
          if (!response.ok) {
            throw new Error(`speakFromUrl: ${url} responded with ${response.status}`);
          }
          const arrayBuffer = await response.arrayBuffer();
          const audioBuffer = await head.audioCtx.decodeAudioData(arrayBuffer);
          // Resume BEFORE scheduling playback, not after: a suspended AudioContext still lets a
          // source start without error, it just stays silent — so scheduling first and checking
          // second used to leave that silent source sitting in the graph whenever the resume below
          // failed (e.g. this call's own autoplay attempt, before any user gesture happened). A
          // later, real click would then schedule a SECOND source on top of the still-pending first
          // one, and once that click's gesture finally unblocked the context, both played at once —
          // audibly doubling the greeting. Checking/resuming first means a blocked attempt never
          // schedules anything to begin with. Only a call stack that actually originates from a user
          // gesture (e.g. a click handler) can successfully resume it; an unprompted autoplay
          // attempt's resume() call here simply no-ops and leaves the context suspended, which is
          // exactly the signal the caller needs.
          if (head.audioCtx.state === "suspended") {
            try {
              await head.audioCtx.resume();
            } catch {
              // Browser refused — treated the same as staying suspended, see the check below.
            }
          }
          if (head.audioCtx.state !== "running") return false;
          // A second concurrent call can still reach this point — the autoplay attempt on load
          // racing a real click on the overlay play button before the first call's own fetch/
          // decode/resume round trip has resolved (see playGreeting in pages/PublicChat/index.tsx,
          // which no longer guards against this itself). The moment either call's speakBuffer()
          // below runs, TalkingHead's isSpeaking flips true synchronously (see startSpeaking() in
          // the vendored talkinghead.mjs) — so whichever call gets here second sees it and skips,
          // instead of scheduling the same greeting audio a second time and audibly doubling it.
          if (!head.isSpeaking) {
            this.speakBuffer(audioBuffer);
          }
          return true;
        },
        unlockAudio() {
          const audioCtx = headRef.current?.audioCtx;
          if (audioCtx?.state === "suspended") {
            // A refusal just leaves it suspended — speakFromUrl reports that as "not audible".
            audioCtx.resume().catch(() => {});
          }
        },
        startListening(stream: MediaStream) {
          const head = headRef.current;
          if (!head) return;
          // Same source as the MediaRecorder for the recording — no second getUserMedia call, and
          // the same AudioContext TalkingHead already holds for speech output.
          const source = head.audioCtx.createMediaStreamSource(stream);
          const analyzer = head.audioCtx.createAnalyser();
          source.connect(analyzer);
          head.startListening(analyzer);
        },
        stopListening() {
          headRef.current?.stopListening();
        },
        startThinking() {
          // x/y = null: the gaze goes to the camera's eye point instead of fixed screen
          // coordinates — needs no viewport calculation and matches the library's already
          // stronger eye contact during isSpeaking/isListening.
          headRef.current?.lookAt(null, null, THINKING_LOOK_MS);
          const engine = motionEngineRef.current;
          if (!engine || !thinkingMotionsEnabledRef.current) return;
          const run = ++thinkingRunRef.current;
          void (async () => {
            let last: string | null = null;
            while (thinkingRunRef.current === run) {
              const choices = THINKING_MOTIONS.filter((name) => name !== last);
              last = choices[Math.floor(Math.random() * choices.length)];
              thinkingGestureActiveRef.current = true;
              try {
                await engine.play(last);
              } catch (error) {
                // A gesture that fails to play must never break the reply it waits for.
                console.error("Avatar thinking gesture could not be played.", error);
                return;
              } finally {
                if (thinkingRunRef.current === run) thinkingGestureActiveRef.current = false;
              }
              const pause = THINKING_PAUSE_MIN_MS + Math.random() * (THINKING_PAUSE_MAX_MS - THINKING_PAUSE_MIN_MS);
              await new Promise((resolve) => setTimeout(resolve, pause));
            }
          })();
        },
        stopThinking() {
          thinkingRunRef.current += 1;
          if (thinkingGestureActiveRef.current) {
            thinkingGestureActiveRef.current = false;
            motionEngineRef.current?.stop();
          }
        },
        playMotions(names: string[]) {
          if (names.length === 0) return;
          // A gesture that fails to play must never break the reply it belongs to.
          motionEngineRef.current?.playSequence(names).catch((error) => {
            console.error("Avatar gesture could not be played.", error);
          });
        },
        stopMotion() {
          thinkingRunRef.current += 1;
          thinkingGestureActiveRef.current = false;
          motionEngineRef.current?.stop();
        },
        startFpsTracking() {
          const now = performance.now();
          fpsTrackerRef.current = { running: true, startedAt: now, lastTickAt: now, sampleCount: 0, droppedFrames: 0 };
        },
        stopFpsTracking() {
          const tracker = fpsTrackerRef.current;
          fpsTrackerRef.current = null;
          if (!tracker || tracker.sampleCount === 0) return null;
          const elapsedMs = tracker.lastTickAt - tracker.startedAt;
          return {
            avgFps: elapsedMs > 0 ? (tracker.sampleCount / elapsedMs) * 1000 : null,
            droppedFrames: tracker.droppedFrames,
            sampleCount: tracker.sampleCount,
          };
        },
      }),
      [],
    );

    useEffect(() => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        setStatus("skipped");
        return;
      }

      let cancelled = false;
      let head: TalkingHead | undefined;

      async function load() {
        try {
          const { TalkingHead: TalkingHeadClass } = await import("@met4citizen/talkinghead");
          if (cancelled || !containerRef.current) return;

          head = new TalkingHeadClass(containerRef.current, {
            cameraView: "upper",
            cameraRotateEnable: false,
            cameraPanEnable: false,
            cameraZoomEnable: false,
            // "upper" targets 2/3 of the body height (chest) by default — in a rather wide/short
            // frame (like this one) that leaves visible empty space at the top and bottom. cameraY
            // moves the camera's target point down (0 = default, larger = lower, more upper body
            // and less head/background space visible at the top) — it steers the actual 3D
            // camera, not just a crop applied afterwards (that was the earlier, failed CSS
            // transform approach on .canvasHost — it couldn't change the actual aspect ratio, see
            // TalkingHeadAvatar.module.css).
            cameraY: 0,
            // Lipsync either doesn't happen at all (landing page, idle) or comes from HeadAudio
            // directly from the audio signal (see speechEnabled below) — TalkingHead's own
            // text-based lipsync path (lipsyncModules) is never needed, since speakText() is never
            // called.
            lipsyncModules: [],
            modelFPS: MODEL_FPS,
            // A factor on top of devicePixelRatio, see MAX_PIXEL_RATIO.
            modelPixelRatio: Math.min(1, MAX_PIXEL_RATIO / window.devicePixelRatio),
          });
          await head.showAvatar({ url: avatarUrl, body: "F" });
          if (cancelled) return;
          headRef.current = head;
          if (speechEnabled) {
            try {
              await attachHeadAudio(head);
            } catch (error) {
              console.error("HeadAudio lipsync unavailable — the avatar runs without lip movement.", error);
            }
            // Composes over (never replaces) the lip-sync update attachHeadAudio just installed —
            // lip-sync must keep running every tick regardless of our own FPS bookkeeping. Uses
            // only our own performance.now() deltas between calls, not the library's `dt`
            // argument, whose exact semantics aren't part of its documented/stable contract.
            // Runs even if attachHeadAudio above failed: head.opt.update is then simply undefined
            // (hence the optional call below), and the FPS measurement keeps working either way.
            const lipSyncUpdate = head.opt.update;
            head.opt.update = (dt: number) => {
              lipSyncUpdate?.(dt);
              const tracker = fpsTrackerRef.current;
              if (!tracker?.running) return;
              const now = performance.now();
              const delta = now - tracker.lastTickAt;
              tracker.lastTickAt = now;
              tracker.sampleCount += 1;
              tracker.droppedFrames += Math.max(0, Math.round(delta / TARGET_FRAME_INTERVAL_MS) - 1);
            };
          }
          if (motionEnabled) {
            try {
              // Loaded on demand like TalkingHead itself, so projects without motion (and the
              // landing page) never download it.
              const [{ MotionEngine: MotionEngineClass }, { default: motions }] = await Promise.all([
                import("@/vendor/motion-engine/MotionEngine.js"),
                import("@/vendor/motion-engine/motions.json"),
              ]);
              if (cancelled) return;
              const engine = new MotionEngineClass(head);
              engine.registerMotions(motions);
              // Composes over whatever is already installed (lip-sync, FPS tracking) instead of
              // replacing it; the engine needs every frame for its bone overlays (e.g. a wave).
              const previousUpdate = head.opt.update;
              head.opt.update = (dt: number) => {
                previousUpdate?.(dt);
                engine.update(dt);
              };
              motionEngineRef.current = engine;
            } catch (error) {
              console.error("MotionEngine not available — the avatar runs without gestures.", error);
            }
          }
          if (!cancelled) {
            setStatus("ready");
            const avatarKey = `${avatarUrl}|${speechEnabled}`;
            if (readyReportedForRef.current !== avatarKey) {
              readyReportedForRef.current = avatarKey;
              onReady?.();
            }
          }
        } catch (error) {
          console.error("TalkingHead avatar could not be loaded.", error);
          if (!cancelled) {
            setStatus("error");
          }
        }
      }

      load();

      // Captured on the container because the canvas only exists once TalkingHead has built it,
      // and these events don't bubble.
      const container = containerRef.current;
      let rebuildTimer: ReturnType<typeof setTimeout> | undefined;
      const rebuildWhenIdle = () => {
        if (cancelled) return;
        if (headRef.current?.isSpeaking) {
          rebuildTimer = setTimeout(rebuildWhenIdle, 500);
          return;
        }
        if (rebuildCount >= MAX_CONTEXT_REBUILDS) {
          console.error("Avatar keeps losing its WebGL context — showing the fallback image instead.");
          setStatus("error");
          return;
        }
        setRebuildCount((count) => count + 1);
      };
      const onContextLost = () => {
        // dispose() in the cleanup below loses the context on purpose.
        if (cancelled) return;
        console.warn("Avatar lost its WebGL context (GPU under memory pressure?).");
        // The fallback covers the black canvas until the avatar is back.
        setStatus("loading");
        clearTimeout(rebuildTimer);
        rebuildTimer = setTimeout(rebuildWhenIdle, CONTEXT_RESTORE_WAIT_MS);
      };
      const onContextRestored = () => {
        if (cancelled) return;
        clearTimeout(rebuildTimer);
        if (headRef.current) setStatus("ready");
      };
      container?.addEventListener("webglcontextlost", onContextLost, true);
      container?.addEventListener("webglcontextrestored", onContextRestored, true);

      return () => {
        cancelled = true;
        clearTimeout(rebuildTimer);
        container?.removeEventListener("webglcontextlost", onContextLost, true);
        container?.removeEventListener("webglcontextrestored", onContextRestored, true);
        headRef.current = null;
        // A disposed head's sources would never report `ended`, and waitUntilIdle would wait forever.
        stopQueuedSources();
        // Clears the engine's pending gesture timers, which would otherwise fire on a disposed head.
        motionEngineRef.current?.stop();
        motionEngineRef.current = null;
        // stop() alone only pauses the render loop and suspends audioCtx — it leaves the WebGL
        // context and Three.js renderer alive. This component remounts a fresh TalkingHead on every
        // visit to any of its four call sites (Landing, Configurator Step1/Step4Preview,
        // PublicChat), so within one SPA session (no full page reload between them) those contexts
        // pile up — and browsers cap how many WebGL contexts a page may hold at once, Safari/iOS
        // notably tighter than desktop. Past that cap, a later new TalkingHead's own context
        // creation silently fails, which looks exactly like "the avatar doesn't load" — until a
        // hard refresh releases every context at once and the cap resets. dispose() actually
        // releases the WebGL context (WEBGL_lose_context) instead of just leaving it idle.
        head?.dispose();
      };
      // onReady deliberately not in the deps: a new inline function on every parent render must
      // not trigger an avatar reload (see avatarUrl/speechEnabled above, the actual triggers).
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [avatarUrl, speechEnabled, motionEnabled, rebuildCount]);

    return (
      <div className={styles.stage}>
        <div
          className={styles.backdrop}
          style={backgroundImageUrl ? { backgroundImage: `url(${backgroundImageUrl})` } : undefined}
        />
        <div ref={containerRef} className={styles.canvasHost} />
        <div
          className={`${styles.fallback} ${status === "ready" && revealed ? styles.fallbackHidden : ""}`}
          aria-hidden={status === "ready" && revealed}
        >
          {status === "loading" ? <Loader2 size={32} className={styles.spinIcon} /> : fallback}
        </div>
      </div>
    );
  },
);
