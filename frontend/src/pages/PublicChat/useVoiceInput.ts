// Voice input for the public chat: records the student and turns it into text, on the device
// (Parakeet, lib/parakeetStt.ts) when its model is ready, otherwise through the server in
// segments cut at speech pauses (Silero VAD, lib/vad.ts). The live transcript goes into the
// composer; the final text is handed back through onFinalText. Also loads the on-device model and
// reports its progress, since the chat waits behind a loading screen until it has settled.
//
// Two modes, per project (Project.voice_input_mode): push-to-talk, where the mic button starts and
// stops one recording, and hands-free, where it switches listening on and off and each turn ends by
// itself (Smart Turn in workers/vadWorker.ts). In hands-free mode listening pauses while the reply
// is generated and spoken, so the avatar's voice and the student can't talk over each other.

import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import { publicChatApi } from "@/api/publicChat";
import type { TalkingHeadAvatarHandle } from "@/components/TalkingHeadAvatar";
import { ParakeetSttEngine, type LoadProgress, type ParakeetStatus, type StreamingSession } from "@/lib/parakeetStt";
import { HANDS_FREE_PAUSE_MS, HANDS_FREE_TURN, SileroVad, type VadSession } from "@/lib/vad";

// Pause-triggered incremental transcription for the server fallback (see backend
// features/ai/stt) — used when on-device recognition (lib/parakeetStt.ts) isn't available.
// Hardcoded constants, not project settings — same posture as the TTS chunker's thresholds
// (features/chat/streaming.py).
const RECORDING_SEGMENT_MIN_MS = 2000; // shorter than this, a pause doesn't cut a segment yet
const SILENCE_PAUSE_MS = 700; // how long a pause must last before it counts as a cut point
// Mirrors the backend's own truncation (_MAX_INITIAL_PROMPT_CHARS in app/features/chat/public_router.py) —
// trimming here too avoids uploading an ever-growing prompt on every segment of a long recording,
// most of which the backend would immediately discard anyway.
const MAX_INITIAL_PROMPT_CHARS = 500;

// Hands-free: if the page doesn't start a reply within this long after a turn was handed over
// (e.g. the message was refused), listening resumes anyway instead of waiting forever.
const REPLY_START_TIMEOUT_MS = 1500;

/** The open microphone of a hands-free session, kept across turns. */
interface HandsFreeSession {
  stream: MediaStream;
  // Created in the click that switched hands-free on and kept open: iPadOS only starts audio from a
  // click, and later turns start without one.
  audioContext: AudioContext;
  vad: VadSession | null;
}

/** One MediaRecorder segment of a server-transcribed recording. */
interface Segment {
  /** Whether the VAD heard speech in it; segments without any aren't uploaded. Starts true while
   * the VAD isn't listening (yet, or at all), so nothing is dropped unchecked. */
  hadSpeech: boolean;
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
  /** Hands-free voice input (see the file comment) instead of push-to-talk. */
  handsFree: boolean;
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
  // Speech detection for the server fallback: loaded on the first such recording (a ~2 MB
  // download only for visitors who actually speak through the server), then kept for the visit.
  const vadRef = useRef<SileroVad | null>(null);
  const vadSessionRef = useRef<VadSession | null>(null);
  const currentSegmentRef = useRef<Segment | null>(null);
  // Bumped when a server recording starts and when it stops, so a VAD that finishes loading after
  // its recording already ended doesn't start listening to a closed stream.
  const recordingIdRef = useRef(0);
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
  // Hands-free: switched on by the mic button (handsFreeOn), one turn recorded at a time
  // (turnActiveRef), then waiting for the reply to the last turn before listening again.
  const [handsFreeOn, setHandsFreeOn] = useState(false);
  const [waitingForReply, setWaitingForReply] = useState(false);
  const handsFreeRef = useRef<HandsFreeSession | null>(null);
  const turnActiveRef = useRef(false);
  const replySeenRef = useRef(false);

  function getVad(): SileroVad {
    // Smart Turn (~8 MB) is only loaded for hands-free projects.
    return (vadRef.current ??= new SileroVad(latest.current.handsFree));
  }

  function pauseDetectionActive(): boolean {
    return vadSessionRef.current !== null || Boolean(handsFreeRef.current?.vad);
  }

  // Server fallback: transcribes one segment (see startPauseDetection) via the backend and
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
      turnFinalized(Boolean(fullText.trim()));
    }
  }

  // Starts one MediaRecorder segment on the given (already-open) stream. Cutting a segment means
  // stopping this recorder and immediately starting a new one on the same stream — MediaRecorder
  // has no "flush and keep recording" API, but a fresh instance yields an independently
  // decodable WebM blob per segment, which a mid-stream chunk from one long recording would not be.
  function startSegmentRecorder(stream: MediaStream) {
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    const segment: Segment = { hadSpeech: !pauseDetectionActive() };
    currentSegmentRef.current = segment;
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = () => {
      // Identity check against the specific recorder toggleRecording's stop click targeted (see
      // finalRecorderRef's own comment) — not a shared flag, so a pause-cut segment whose onstop
      // is still pending when the student clicks stop can't mistake itself for the final one.
      const isFinal = recorder === finalRecorderRef.current;
      // Hands-free keeps the microphone open for the next turn.
      if (isFinal && !handsFreeRef.current) stream.getTracks().forEach((track) => track.stop());
      // A segment without speech (e.g. the silence after the last word) isn't uploaded: it would
      // only use a server transcription slot, and models tend to invent words in silence.
      const blob = new Blob(segment.hadSpeech ? chunks : [], { type: "audio/webm" });
      // Chained, not fire-and-forget: keeps every segment's transcribeSegment() call — including
      // this one if it's final — from running before an earlier segment's is done reading/updating
      // transcriptSoFarRef (see transcriptionChainRef's own comment).
      transcriptionChainRef.current = transcriptionChainRef.current.then(() => transcribeSegment(blob, isFinal));
    };
    recorder.start();
    mediaRecorderRef.current = recorder;
    segmentStartedAtRef.current = performance.now();
  }

  // Called when the VAD reports the end of speech, once the current segment is long enough (see
  // RECORDING_SEGMENT_MIN_MS) — too-short segments are left alone and simply carried into the
  // next one, since speech recognition on very short clips is poor.
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
    let sent = false;
    try {
      const { text } = await session.stop();
      if (text.trim()) {
        latest.current.onFinalText(text);
        sent = true;
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
      // Hands-free keeps the microphone open for the next turn.
      if (!handsFreeRef.current) {
        recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
      }
      setIsFinalizingRecording(false);
    }
    turnFinalized(sent);
  }

  // Server fallback: cuts the recording into segments at speech pauses, once the VAD is loaded.
  // Without it (still loading, or failed), the recording just isn't cut and goes up as one piece.
  async function startPauseDetection(stream: MediaStream, audioContext: AudioContext) {
    const recordingId = ++recordingIdRef.current;
    const vad = getVad();
    await vad.ensureReady();
    if (vad.status !== "ready" || recordingId !== recordingIdRef.current) {
      void audioContext.close();
      return;
    }
    try {
      vadSessionRef.current = await vad.listen(
        stream,
        audioContext,
        {
          onSpeechStart: () => {
            if (currentSegmentRef.current) currentSegmentRef.current.hadSpeech = true;
          },
          onSpeechEnd: () => {
            if (performance.now() - segmentStartedAtRef.current >= RECORDING_SEGMENT_MIN_MS) cutSegment();
          },
        },
        SILENCE_PAUSE_MS,
      );
    } catch (error) {
      console.error("Pause detection unavailable for this recording.", error);
      void audioContext.close();
      return;
    }
    // Stopped while listen() was still setting up.
    if (recordingId !== recordingIdRef.current) {
      vadSessionRef.current.stop();
      vadSessionRef.current = null;
    }
  }

  // Server path: starts a fresh recording (segments, transcript) on an open stream.
  function beginServerRecording(stream: MediaStream) {
    recordingStreamRef.current = stream;
    transcriptSoFarRef.current = "";
    finalRecorderRef.current = null;
    transcriptionChainRef.current = Promise.resolve();
    recordingHadFailureRef.current = false;
    startSegmentRecorder(stream);
  }

  // Server path: stops the recording; its last segment's transcription then sends the text.
  function stopServerRecording() {
    // Captures the exact recorder instance being stopped — see finalRecorderRef's own comment
    // for why this can't be a plain boolean shared across every segment's onstop handler.
    finalRecorderRef.current = mediaRecorderRef.current;
    setIsFinalizingRecording(true);
    mediaRecorderRef.current?.stop();
    avatarRef.current?.stopListening();
    setIsRecording(false);
  }

  // ==================== Hands-free ====================

  // Switches hands-free listening on: opens the microphone once, starts the VAD with turn
  // detection and records the first turn.
  async function startHandsFree() {
    // Created inside the click, before any await — see HandsFreeSession.audioContext.
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
    const vad = getVad();
    await vad.ensureReady();
    if (vad.status !== "ready") {
      // Without speech detection nothing could end a turn; push-to-talk still works.
      stream.getTracks().forEach((track) => track.stop());
      void audioContext.close();
      latest.current.onNotice("publicChat.handsFreeUnavailable");
      return;
    }
    const session: HandsFreeSession = { stream, audioContext, vad: null };
    handsFreeRef.current = session;
    recordingStreamRef.current = stream;
    setHandsFreeOn(true);
    try {
      session.vad = await vad.listen(
        stream,
        audioContext,
        {
          onSpeechStart: () => {
            if (currentSegmentRef.current) currentSegmentRef.current.hadSpeech = true;
          },
          onSpeechEnd: () => {
            if (mediaRecorderRef.current?.state !== "recording") return;
            if (performance.now() - segmentStartedAtRef.current >= RECORDING_SEGMENT_MIN_MS) cutSegment();
          },
          onTurnEnd: () => endTurn(),
        },
        HANDS_FREE_PAUSE_MS,
        HANDS_FREE_TURN,
        false,
      );
    } catch (error) {
      console.error("Hands-free listening couldn't start.", error);
      stopHandsFree();
      latest.current.onNotice("publicChat.handsFreeUnavailable");
      return;
    }
    await startTurn();
  }

  // Records one turn on the open hands-free microphone, on the device or through the server.
  async function startTurn() {
    const session = handsFreeRef.current;
    if (!session || turnActiveRef.current) return;
    turnActiveRef.current = true;
    session.vad?.resume(); // fresh VAD and Smart Turn state for the new turn
    inputBeforeRecordingRef.current = latest.current.input.trim();
    const engine = sttEngineRef.current;
    if (engine?.status === "ready") {
      try {
        streamingSessionRef.current = await engine.startStreaming(
          session.stream,
          session.audioContext,
          showLiveTranscript,
          false,
        );
      } catch (error) {
        console.error("On-device audio capture failed, using server transcription instead.", error);
        engine.status = "error";
      }
    }
    // Switched off while the device model was starting up.
    if (handsFreeRef.current !== session) {
      void streamingSessionRef.current?.stop();
      streamingSessionRef.current = null;
      return;
    }
    if (!streamingSessionRef.current) beginServerRecording(session.stream);
    avatarRef.current?.startListening(session.stream);
    setIsRecording(true);
  }

  // The turn is over (Smart Turn or the maximum silence, or the student sent it): stop recording;
  // the final text is sent once it's transcribed (see turnFinalized).
  function endTurn() {
    if (!turnActiveRef.current) return;
    turnActiveRef.current = false;
    handsFreeRef.current?.vad?.pause();
    if (streamingSessionRef.current) {
      void stopStreamingRecording(streamingSessionRef.current);
    } else if (mediaRecorderRef.current?.state === "recording") {
      stopServerRecording();
    } else {
      // Nothing was recording yet (the turn ended while it was starting up): just listen on.
      void startTurn();
    }
  }

  // Called once a recording's final text has been handed over (or there was none). In hands-free
  // mode, listening resumes after the reply — or right away if nothing was sent.
  function turnFinalized(sent: boolean) {
    if (!handsFreeRef.current) return;
    if (sent) {
      replySeenRef.current = false;
      setWaitingForReply(true);
    } else {
      void startTurn();
    }
  }

  // Switches hands-free off. A turn in progress is finished and sent, like stopping push-to-talk.
  function stopHandsFree() {
    const session = handsFreeRef.current;
    if (!session) return;
    // Cleared first, so the finishing turn closes the microphone instead of keeping it open.
    handsFreeRef.current = null;
    setHandsFreeOn(false);
    setWaitingForReply(false);
    session.vad?.stop();
    const close = () => void session.audioContext.close();
    if (turnActiveRef.current) {
      turnActiveRef.current = false;
      if (streamingSessionRef.current) {
        void stopStreamingRecording(streamingSessionRef.current).finally(close);
        return;
      }
      if (mediaRecorderRef.current?.state === "recording") {
        stopServerRecording(); // its final segment's onstop stops the tracks
        close();
        return;
      }
    }
    avatarRef.current?.stopListening();
    setIsRecording(false);
    session.stream.getTracks().forEach((track) => track.stop());
    recordingStreamRef.current = null;
    close();
  }

  // The composer's send while voice input is running: sends what was said so far. Push-to-talk:
  // like pressing stop. Hands-free: ends this turn, but keeps listening afterwards.
  function finishRecording() {
    if (handsFreeRef.current) endTurn();
    else void toggleRecording();
  }

  // Voice recording in the browser
  async function toggleRecording() {
    // The spoken reply arrives long after this gesture, so audio is unlocked now (see
    // sendFromComposer in the page).
    avatarRef.current?.unlockAudio();
    if (latest.current.handsFree) {
      if (handsFreeRef.current) {
        stopHandsFree();
        return;
      }
      if (replyPending) return;
      setMicErrorKey(null);
      try {
        await startHandsFree();
      } catch (error) {
        console.error("Microphone access failed.", error);
        const denied = error instanceof DOMException && error.name === "NotAllowedError";
        setMicErrorKey(denied ? "publicChat.micPermissionDenied" : "publicChat.micUnavailable");
      }
      return;
    }
    // Already recording -> stop the recording
    if (isRecording) {
      if (streamingSessionRef.current) {
        void stopStreamingRecording(streamingSessionRef.current);
        return;
      }
      recordingIdRef.current++;
      vadSessionRef.current?.stop();
      vadSessionRef.current = null;
      stopServerRecording();
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
      // Created before awaiting the mic permission, while still inside the click — Safari starts an
      // AudioContext created later in the async chain as "suspended", so the VAD would hear nothing.
      const vadAudioContext = new AudioContext();
      // Request the microphone and wait for permission. echoCancellation/noiseSuppression requested
      // explicitly rather than left to the browser's own default — bare booleans (not wrapped in
      // `exact`) are "ideal" constraints per the Media Capture spec, so a device that can't honor
      // them still grants the stream instead of getUserMedia rejecting. Suppressing steady room
      // noise up front also helps the VAD tell speech from a noisy classroom.
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true },
        });
      } catch (error) {
        void vadAudioContext.close();
        throw error;
      }
      avatarRef.current?.startListening(stream);
      beginServerRecording(stream);
      setIsRecording(true);
      void startPauseDetection(stream, vadAudioContext);
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

  // Hands-free: listening resumes once the reply to the last turn has been generated and spoken
  // (replyPending went true and back to false) — or after REPLY_START_TIMEOUT_MS if no reply
  // started at all.
  useEffect(() => {
    if (!handsFreeOn || !waitingForReply) return;
    if (replyPending) {
      replySeenRef.current = true;
      return;
    }
    if (replySeenRef.current) {
      replySeenRef.current = false;
      setWaitingForReply(false);
      void startTurn();
      return;
    }
    const timer = setTimeout(() => {
      setWaitingForReply(false);
      void startTurn();
    }, REPLY_START_TIMEOUT_MS);
    return () => clearTimeout(timer);
    // startTurn only reads refs and `latest`, so a stale closure is harmless here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handsFreeOn, waitingForReply, replyPending]);

  // Terminates the speech recognition and VAD workers on unmount — leaving them running would keep
  // the loaded models in memory for a tab that's no longer showing this chat at all.
  useEffect(() => {
    return () => {
      stopHandsFree();
      sttEngineRef.current?.dispose();
      vadSessionRef.current?.stop();
      vadRef.current?.dispose();
    };
    // Unmount only; stopHandsFree reads refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    isRecording,
    isFinalizingRecording,
    handsFreeOn,
    micErrorKey,
    sttLoadProgress,
    toggleRecording,
    finishRecording,
  };
}
