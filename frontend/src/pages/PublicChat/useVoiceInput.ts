// Voice input for the public chat: records the student and turns it into text, on the device
// (Parakeet, lib/parakeetStt.ts) when its model is ready, otherwise through the server in
// pause-cut segments. The live transcript goes into the composer; the final text is handed back
// through onFinalText. Also loads the on-device model and reports its progress, since the chat
// waits behind a loading screen until it has settled.

import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import { publicChatApi } from "@/api/publicChat";
import type { TalkingHeadAvatarHandle } from "@/components/TalkingHeadAvatar";
import { FALLBACK_SILENCE_RMS_THRESHOLD, calibrateSilenceThreshold, rms } from "@/lib/audioLevel";
import { ParakeetSttEngine, type LoadProgress, type ParakeetStatus, type StreamingSession } from "@/lib/parakeetStt";

// Pause-triggered incremental transcription for the server fallback (see backend
// features/ai/stt) — used when on-device recognition (lib/parakeetStt.ts) isn't available.
// Hardcoded constants, not project settings — same posture as the TTS chunker's thresholds
// (features/chat/streaming.py).
const RECORDING_SEGMENT_MIN_MS = 2000; // shorter than this, a pause doesn't cut a segment yet
const SILENCE_PAUSE_MS = 700; // how long a pause must last before it counts as a cut point
// How long watchForSpeechPauses spends measuring this recording's own ambient noise floor before
// deriving a real silence threshold from it (see lib/audioLevel.ts) — long enough to average out
// one stray sound (a cough, a chair scraping), short enough not to meaningfully delay pause
// detection. Harmless that RECORDING_SEGMENT_MIN_MS-vs-real-cuts overlaps with this window: no
// segment can be cut before RECORDING_SEGMENT_MIN_MS (2000ms) anyway, well past calibration.
const CALIBRATION_MS = 400;
// Mirrors the backend's own truncation (_MAX_INITIAL_PROMPT_CHARS in app/features/chat/public_router.py) —
// trimming here too avoids uploading an ever-growing prompt on every segment of a long recording,
// most of which the backend would immediately discard anyway.
const MAX_INITIAL_PROMPT_CHARS = 500;

// Calls `onPause` every time the stream has been quiet (RMS below the calibrated threshold) for
// SILENCE_PAUSE_MS. Returns a cleanup function that stops watching and releases the AudioContext.
// Reuses the MediaStream the caller already holds (from getUserMedia) instead of requesting a new
// one — an AnalyserNode can tap the same stream any number of times.
//
// Spends its first CALIBRATION_MS measuring THIS recording's own ambient noise floor instead of
// judging silence against one hardcoded RMS number — mic sensitivity and room noise vary too much
// across real devices for a fixed constant to work everywhere (see lib/audioLevel.ts). Runs
// concurrently with recording, not before it, so calibration adds no delay before the mic button
// visibly starts recording and no audio is lost — pause detection just uses
// FALLBACK_SILENCE_RMS_THRESHOLD for that first stretch, which is safe because no cut can happen
// before RECORDING_SEGMENT_MIN_MS (2000ms) regardless, well past calibration finishing.
function watchForSpeechPauses(stream: MediaStream, onPause: () => void): () => void {
  const audioCtx = new AudioContext();
  const source = audioCtx.createMediaStreamSource(stream);
  const analyser = audioCtx.createAnalyser();
  source.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  const calibrationStartedAt = performance.now();
  const calibrationSamples: number[] = [];
  let threshold = FALLBACK_SILENCE_RMS_THRESHOLD;
  let calibrated = false;
  let silenceSince: number | null = null;
  let frameId: number;

  function tick() {
    analyser.getFloatTimeDomainData(samples);
    const level = rms(samples);
    const now = performance.now();

    if (!calibrated) {
      calibrationSamples.push(level);
      if (now - calibrationStartedAt >= CALIBRATION_MS) {
        calibrated = true;
        threshold = calibrateSilenceThreshold(calibrationSamples);
      }
    }

    if (level < threshold) {
      if (silenceSince === null) silenceSince = now;
      else if (now - silenceSince >= SILENCE_PAUSE_MS) {
        silenceSince = now; // avoid firing again every frame while the pause continues
        onPause();
      }
    } else {
      silenceSince = null;
    }
    frameId = requestAnimationFrame(tick);
  }
  frameId = requestAnimationFrame(tick);

  return () => {
    cancelAnimationFrame(frameId);
    source.disconnect();
    void audioCtx.close();
  };
}

interface VoiceInputOptions {
  slug: string;
  /** The on-device model to load; null while it shouldn't (server transcription, or the visitor
   * isn't through the password/name gate yet — no ~170 MB download for someone who leaves there). */
  modelUrl: string | null;
  avatarRef: RefObject<TalkingHeadAvatarHandle>;
  /** The composer text; the live transcript is shown after what was typed before recording. */
  input: string;
  setInput: Dispatch<SetStateAction<string>>;
  /** A reply is still in flight or being spoken, so a new recording couldn't be sent. */
  replyPending: boolean;
  onFinalText: (text: string) => void;
  /** A notice for the thread, as a translation key. */
  onNotice: (key: string) => void;
  /** The model download starts — called in the same effect, before the avatar can be ready. */
  onModelLoading: () => void;
  /** The model is ready, or turned out unsupported/failed (voice input then uses the server). */
  onModelSettled: (status: ParakeetStatus) => void;
}

export function useVoiceInput(options: VoiceInputOptions) {
  const { slug, modelUrl, avatarRef, input, setInput, replyPending } = options;
  // The recorder and model callbacks run long after the render that set them up; through this
  // they always reach the page's current handlers (e.g. sendMessage with the current history).
  const latest = useRef(options);
  latest.current = options;

  // Translation key for the last getUserMedia failure (permission denied, no device, ...), or
  // null if the mic hasn't failed (yet) — see toggleRecording. Previously only console.error'd,
  // so a student who denied the permission prompt had no idea why voice input silently did
  // nothing.
  const [micErrorKey, setMicErrorKey] = useState<string | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  // True from the moment the mic button is released until the last segment's transcription (and
  // the resulting sendMessage) has been kicked off — drives the mic button's spinner/disabled state,
  // the same role transcribeMutation.isPending used to play before segmentation replaced it.
  const [isFinalizingRecording, setIsFinalizingRecording] = useState(false);
  // Non-null while the on-device speech recognition model is still loading — the chat stays
  // behind a full-page loading screen until then (see the warm-up effect below). null once it's
  // not relevant (project transcribes on the server) or loading has finished or failed.
  const [sttLoadProgress, setSttLoadProgress] = useState<LoadProgress | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);
  const stopWatchingPausesRef = useRef<(() => void) | null>(null);
  // The specific MediaRecorder instance toggleRecording's stop click targeted — read inside each
  // recorder's own onstop handler (see startSegmentRecorder) via identity comparison, not a plain
  // boolean flag. A pause-triggered cut (cutSegment) starts a new recorder immediately and lets
  // the old one's onstop fire whenever the browser gets to it — if the student pauses and clicks
  // "stop recording" in quick succession, that earlier onstop can still be pending when the click
  // flips a *shared* boolean, making it ALSO see itself as "the final segment". Comparing against
  // the exact recorder instance instead means only the one actually stopped by the click ever is.
  const finalRecorderRef = useRef<MediaRecorder | null>(null);
  const segmentStartedAtRef = useRef(0);
  // Accumulates across every segment of the CURRENT recording — sent back as initial_prompt (see
  // features/ai/stt) so each new segment gets cross-segment context, and used as the final message
  // text once recording stops.
  const transcriptSoFarRef = useRef("");
  // Chains every segment's transcribeSegment() call onto the one before it (same pattern as
  // playChainRef in the page), so segments are always folded into transcriptSoFarRef in the order they
  // were spoken. Without this, each pause-triggered segment's /transcriptions request races the
  // others — usually resolving in order on a quiet server, but not guaranteed, and far more likely
  // to reorder once the shared, single-slot STT queue (see features/ai/stt) is also busy with a
  // second visitor's recording. An out-of-order final segment would otherwise read/reset
  // transcriptSoFarRef before an earlier segment's words had been folded in, silently dropping
  // them from the sent message (or leaking them into the next recording instead).
  const transcriptionChainRef = useRef<Promise<void>>(Promise.resolve());
  // True if ANY segment of the CURRENT recording failed to transcribe (network error, or the
  // shared STT slot timing out under contention — see features/ai/stt); reset at the start of each
  // new recording. Read once the final segment finishes, to decide whether to warn the student
  // their message may be missing words (or didn't send at all) — see transcribeSegment.
  const recordingHadFailureRef = useRef(false);
  // Built once per page visit by the warm-up effect below and kept across recordings — loading
  // the model is the expensive part.
  const sttEngineRef = useRef<ParakeetSttEngine | null>(null);
  // The on-device recording in progress, if any (the server fallback uses mediaRecorderRef).
  const streamingSessionRef = useRef<StreamingSession | null>(null);
  // Whatever the student had already typed when the recording started — the live transcript is
  // shown after it in the composer.
  const inputBeforeRecordingRef = useRef("");

  // Server fallback: transcribes one segment (see watchForSpeechPauses) via the backend and
  // appends the result to the visible input box; on the final segment (mic button released),
  // sends the full accumulated text instead — no manual send click needed. Always called chained
  // onto transcriptionChainRef (see startSegmentRecorder) so segments never read/reset
  // transcriptSoFarRef out of order relative to each other.
  async function transcribeSegment(blob: Blob, isFinal: boolean) {
    if (blob.size > 0) {
      try {
        const initialPrompt = transcriptSoFarRef.current.slice(-MAX_INITIAL_PROMPT_CHARS) || undefined;
        const res = await publicChatApi.transcribe(slug, blob, initialPrompt);
        if (res.text) {
          transcriptSoFarRef.current = `${transcriptSoFarRef.current} ${res.text}`.trim();
          setInput((prev) => `${prev} ${res.text}`.trim());
        }
      } catch (error) {
        // Swallowed here rather than re-thrown: this call is chained onto transcriptionChainRef,
        // and a rejection would break that chain for every later segment too (including the real
        // final one). Recorded on recordingHadFailureRef instead — this segment's words are
        // missing from what gets sent, surfaced as a system-message notice once the recording ends
        // (see the isFinal block below).
        console.error("Segment transcription failed.", error);
        recordingHadFailureRef.current = true;
      }
    }
    if (isFinal) {
      setIsFinalizingRecording(false);
      const fullText = transcriptSoFarRef.current;
      transcriptSoFarRef.current = "";
      // Surfaced the same way interruptResponse() reports an interruption — inline in the thread,
      // right where the student is looking, instead of a generic banner elsewhere on the page.
      // Fires whenever ANY segment of this recording failed, even one that wasn't the final one,
      // since the sent (or unsent) text may be missing words the student actually said.
      if (recordingHadFailureRef.current) {
        latest.current.onNotice("publicChat.recordingFailed");
      }
      if (fullText.trim()) {
        latest.current.onFinalText(fullText);
      }
    }
  }

  // Starts one MediaRecorder segment on the given (already-open) stream. Cutting a segment means
  // stopping this recorder and immediately starting a new one on the same stream — MediaRecorder
  // has no "flush and keep recording" API, but a fresh instance yields an independently
  // decodable WebM blob per segment, which a mid-stream chunk from one long recording would not be.
  function startSegmentRecorder(stream: MediaStream) {
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = () => {
      // Identity check against the specific recorder toggleRecording's stop click targeted (see
      // finalRecorderRef's own comment) — not a shared flag, so a pause-cut segment whose onstop
      // is still pending when the student clicks stop can't mistake itself for the final one.
      const isFinal = recorder === finalRecorderRef.current;
      if (isFinal) stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(chunks, { type: "audio/webm" });
      // Chained, not fire-and-forget: keeps every segment's transcribeSegment() call — including
      // this one if it's final — from running before an earlier segment's is done reading/updating
      // transcriptSoFarRef (see transcriptionChainRef's own comment).
      transcriptionChainRef.current = transcriptionChainRef.current.then(() => transcribeSegment(blob, isFinal));
    };
    recorder.start();
    mediaRecorderRef.current = recorder;
    segmentStartedAtRef.current = performance.now();
  }

  // Called on a detected speech pause once the current segment is long enough (see
  // RECORDING_SEGMENT_MIN_MS) — too-short segments are left alone and simply carried into the
  // next one, since faster-whisper's accuracy on very short clips is poor.
  function cutSegment() {
    const recorder = mediaRecorderRef.current;
    const stream = recordingStreamRef.current;
    if (!recorder || !stream || recorder.state !== "recording") return;
    recorder.stop(); // triggers onstop above -> transcribes this segment
    startSegmentRecorder(stream); // continue recording the next one right away
  }

  // Shows the live transcript after whatever was already typed before the recording started.
  function showLiveTranscript(text: string) {
    setInput(`${inputBeforeRecordingRef.current} ${text}`.trim());
  }

  // On-device path of toggleRecording: the transcript grows in the composer while the student
  // speaks, and the final text is sent as soon as the model has decoded the last bit of audio.
  // Returns false if audio capture couldn't be set up, so the caller can use the server instead;
  // a microphone error (permission denied, ...) is thrown like in the server path.
  async function startStreamingRecording(engine: ParakeetSttEngine): Promise<boolean> {
    // Created before awaiting the mic permission, while still inside the click — Safari starts an
    // AudioContext created later in the async chain as "suspended", so it would capture nothing.
    const audioContext = new AudioContext();
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (error) {
      void audioContext.close();
      throw error;
    }
    try {
      inputBeforeRecordingRef.current = input.trim();
      streamingSessionRef.current = await engine.startStreaming(stream, audioContext, showLiveTranscript);
    } catch (error) {
      console.error("On-device audio capture failed, using server transcription instead.", error);
      engine.status = "error";
      stream.getTracks().forEach((track) => track.stop());
      void audioContext.close();
      return false;
    }
    recordingStreamRef.current = stream;
    avatarRef.current?.startListening(stream);
    setIsRecording(true);
    return true;
  }

  async function stopStreamingRecording(session: StreamingSession) {
    streamingSessionRef.current = null;
    setIsFinalizingRecording(true);
    avatarRef.current?.stopListening();
    setIsRecording(false);
    try {
      const { text } = await session.stop();
      if (text.trim()) {
        latest.current.onFinalText(text);
      } else {
        setInput(inputBeforeRecordingRef.current);
      }
    } catch (error) {
      // The model failed mid-recording; the engine has switched itself off, so the next
      // recording goes through the server instead.
      console.error("On-device transcription failed.", error);
      setInput(inputBeforeRecordingRef.current);
      latest.current.onNotice("publicChat.onDeviceSttFailed");
    } finally {
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
      recordingStreamRef.current = null;
      setIsFinalizingRecording(false);
    }
  }

  // Voice recording in the browser
  async function toggleRecording() {
    // The spoken reply arrives long after this gesture, so audio is unlocked now (see
    // sendFromComposer in the page).
    avatarRef.current?.unlockAudio();
    // Already recording -> stop the recording
    if (isRecording) {
      if (streamingSessionRef.current) {
        void stopStreamingRecording(streamingSessionRef.current);
        return;
      }
      // Captures the exact recorder instance being stopped — see finalRecorderRef's own comment
      // for why this can't be a plain boolean shared across every segment's onstop handler.
      finalRecorderRef.current = mediaRecorderRef.current;
      setIsFinalizingRecording(true);
      stopWatchingPausesRef.current?.();
      stopWatchingPausesRef.current = null;
      mediaRecorderRef.current?.stop();
      avatarRef.current?.stopListening();
      setIsRecording(false);
      return;
    }
    // A reply from a previous message may still be in flight (or, for a streamed reply, still
    // being spoken) — starting a new recording now would let its final text be dropped by the
    // page's "a reply is pending" guard on sending, with no feedback.
    if (replyPending) return;
    // Not recording yet: get the device permission, record and transcribe
    setMicErrorKey(null);
    try {
      const engine = sttEngineRef.current;
      if (engine?.status === "ready" && (await startStreamingRecording(engine))) return;
      // Request the microphone and wait for permission. echoCancellation/noiseSuppression requested
      // explicitly rather than left to the browser's own default — bare booleans (not wrapped in
      // `exact`) are "ideal" constraints per the Media Capture spec, so a device that can't honor
      // them still grants the stream instead of getUserMedia rejecting. Suppressing steady room
      // noise up front also makes watchForSpeechPauses's calibrated threshold below meaningful:
      // without it, a noisy classroom's noise floor could sit high enough that real speech and
      // silence become hard to tell apart no matter how the threshold is calibrated.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      recordingStreamRef.current = stream;
      avatarRef.current?.startListening(stream);
      transcriptSoFarRef.current = "";
      finalRecorderRef.current = null;
      transcriptionChainRef.current = Promise.resolve();
      recordingHadFailureRef.current = false;
      startSegmentRecorder(stream);
      stopWatchingPausesRef.current = watchForSpeechPauses(stream, () => {
        if (performance.now() - segmentStartedAtRef.current >= RECORDING_SEGMENT_MIN_MS) {
          cutSegment();
        }
      });
      setIsRecording(true);
    } catch (error) {
      console.error("Microphone access failed.", error);
      // NotAllowedError: permission denied (or blocked by browser/site policy). Anything else
      // (NotFoundError, NotReadableError, SecurityError on an insecure origin, ...) means voice
      // input just isn't usable right now for a reason the student can't fix by granting access.
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      setMicErrorKey(denied ? "publicChat.micPermissionDenied" : "publicChat.micUnavailable");
    }
  }

  // Starts loading the on-device speech recognition model once the page asks for it (see
  // modelUrl): before the chat itself, so with a pre-chat survey the download runs while the
  // visitor fills it out. The chat waits behind a loading screen until this settles.
  useEffect(() => {
    if (!modelUrl) return;
    if (sttEngineRef.current?.modelUrl === modelUrl) return;
    sttEngineRef.current?.dispose();
    const engine = new ParakeetSttEngine(modelUrl);
    sttEngineRef.current = engine;
    latest.current.onModelLoading();
    setSttLoadProgress({ loadedBytes: 0, totalBytes: 0 });
    void engine
      .ensureReady((progress) => setSttLoadProgress(progress))
      .then(() => {
        setSttLoadProgress(null);
        latest.current.onModelSettled(engine.status);
      });
  }, [modelUrl]);

  // Terminates the speech recognition worker on unmount — leaving it running would keep the
  // loaded model in memory for a tab that's no longer showing this chat at all.
  useEffect(() => {
    return () => sttEngineRef.current?.dispose();
  }, []);

  return { isRecording, isFinalizingRecording, micErrorKey, sttLoadProgress, toggleRecording };
}
