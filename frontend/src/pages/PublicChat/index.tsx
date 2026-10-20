import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2, Mic, MessageCircle, Play, Send, Square, LogOut, X } from "lucide-react";
import { Avatar } from "@/components/Avatar";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { ChatBubble, TypingBubble } from "@/components/ChatBubble";
import { ChecklistCard } from "@/components/ChecklistCard";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { SurveyEmbed } from "@/components/SurveyEmbed";
import { AudioOnlySpeaker, TalkingHeadAvatar, type TalkingHeadAvatarHandle } from "@/components/TalkingHeadAvatar";
import { PublicChatLayout } from "@/layouts/PublicChatLayout";
import { publicChatApi, type StreamChunkEvent, type StreamDoneEvent } from "@/api/publicChat";
import type { LoadProgress } from "@/lib/parakeetStt";
import { PocketBrowserTts } from "@/lib/pocketTts";
import { ApiError } from "@/api/client";
import { doneStepIds } from "@/lib/checklist";
import { getVisitorName } from "@/lib/visitorNameStorage";
import { useAutoResizeTextarea } from "@/hooks/useAutoResizeTextarea";
import type { ChatMessage } from "@/types/chat";
import type { ChatLayout } from "@/types/project";
import { NameGate, PasswordGate, gatePassed, isLocked } from "./Gate";
import { ModelInfoButton } from "./ModelInfoButton";
import { useVoiceInput } from "./useVoiceInput";
import styles from "./PublicChat.module.css";

type Stage = "locked" | "name-gate" | "before-survey" | "chat" | "after-survey" | "done";

// Same role as setTimeout, but resolves immediately once `signal` aborts instead of waiting out the
// full duration — used for the playback pacing waits below, so interruptResponse() doesn't have to
// wait for the current chunk/reply's audio to finish before unblocking the composer.
function delay(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

// Drops local-only UI notices (see the "system" role on ChatMessage) before a message list is sent
// to the backend — its ChatHistoryEntry only accepts "user"/"assistant" and 422s on anything else.
function toApiHistory(history: ChatMessage[]): ChatMessage[] {
  return history.filter((message) => message.role !== "system");
}

// Mirrors the backend's own cap (_MAX_CHAT_MESSAGE_CHARS in app/features/chat/schemas.py) — caps
// typing here too so a too-long message shows as "can't type more" instead of a round trip to
// the backend just to get a 422 back.
const MAX_CHAT_MESSAGE_CHARS = 8000;

// Safety net for the avatar-reveal gate below (see greetingGraceExpired): how long to wait for the
// autoplayed greeting to resolve as audible before showing the avatar anyway. Generous relative to
// the actual fetch+decode+resume-check round trip (typically well under a second even on a slow
// connection, see speakFromUrl), but short enough a student on a browser that blocks autoplay
// without a prior gesture — the norm on iOS/iPadOS Safari — isn't left thinking the avatar itself
// failed to load, with only a small, easy-to-miss play button as the way to find out otherwise.
const GREETING_REVEAL_GRACE_MS = 2500;

/** Loading screen covering the chat while the on-device speech recognition model loads (the
 * chat and avatar already load underneath it). With `onStart`, loading is done and it shows a
 * "Start chat" button instead of the progress bar — see chatStartPending in PublicChatPage. */
function SttLoadingScreen({ progress, onStart }: { progress: LoadProgress | null; onStart?: () => void }) {
  const { t } = useTranslation();
  const loadedBytes = progress?.loadedBytes ?? 0;
  const totalBytes = progress?.totalBytes ?? 0;
  const percent = totalBytes > 0 ? Math.floor((loadedBytes / totalBytes) * 100) : 0;
  // Once every byte is in, the model is still being set up on the GPU — that takes a few seconds,
  // and a bar stuck at 100% would look frozen.
  const preparing = totalBytes > 0 && loadedBytes >= totalBytes;
  return (
    <div className={`${styles.centered} ${styles.sttLoadingOverlay}`}>
      <div className={styles.sttLoadingScreen}>
        {onStart ? (
          <>
            <h2>{t("publicChat.sttLoading.readyTitle")}</h2>
            <p className={styles.sttLoadingText}>{t("publicChat.sttLoading.readyDescription")}</p>
            <Button type="button" variant="accent" onClick={onStart} autoFocus>
              {t("publicChat.sttLoading.start")}
            </Button>
          </>
        ) : (
          <>
            <Loader2 size={32} className={styles.spinIcon} />
            <h2>{t("publicChat.sttLoading.title")}</h2>
            <p className={styles.sttLoadingText}>{t("publicChat.sttLoading.description")}</p>
            <div
              className={styles.sttLoadingBar}
              role="progressbar"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span className={styles.sttLoadingLabel}>
                {preparing ? t("publicChat.sttLoading.preparing") : t("publicChat.sttLoading.progress", { percent })}
              </span>
              <div className={styles.sttLoadingTrack}>
                <div className={styles.sttLoadingFill} style={{ width: `${percent}%` }} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// Screen 1i — public student chat page (mobile-first, no login). Spoken messages are sent
// automatically after transcription (no manual "Send" click needed).
export function PublicChatPage() {
  const { t } = useTranslation();
  const { projectSlug } = useParams<{ projectSlug: string }>();
  const slug = projectSlug!;

  const tutorQuery = useQuery({
    queryKey: ["public-chat", slug],
    queryFn: () => publicChatApi.loadTutor(slug),
    retry: false,
  });

  // The password/name screens themselves live in Gate.tsx.
  const [visitorNameProvided, setVisitorNameProvided] = useState(() => Boolean(getVisitorName(slug)));

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // Replayed from the messages rather than kept separately, so an edited or regenerated message
  // takes its ticks with it — the same replay the backend does (see lib/checklist.ts).
  const doneIds = useMemo(() => doneStepIds(messages), [messages]);
  const [input, setInput] = useState("");
  const [rateLimited, setRateLimited] = useState(false);
  // null = no manual stage choice made yet; the actual initial stage depends on the loaded
  // project (pre-survey configured?), see `stage` further down.
  const [manualStage, setManualStage] = useState<Stage | null>(null);
  // null = not yet initialized from the loaded project (see useEffect below); after that only the
  // visitor's own click controls it, the project default only acts as the starting value.
  const [chatOpen, setChatOpen] = useState<boolean | null>(null);

  const avatarRef = useRef<TalkingHeadAvatarHandle>(null);
  const [avatarReady, setAvatarReady] = useState(false);
  // Ensures the spoken greeting is only ever autoplayed once per page load, not again on a later
  // re-render (e.g. after loadTutor refetches following an unlock).
  const autoplayedGreetingRef = useRef(false);
  // Until the greeting is actually audible, the avatar stays behind its static fallback (see
  // TalkingHeadAvatar's `revealed` prop) instead of idling in the background behind the play
  // button — like a paused video showing a poster frame instead of already-moving footage. Set
  // from playGreeting only once speakFromUrl confirms the audio is actually audible (an autoplay
  // attempt right on load can be silently suspended by the browser — decoding/scheduling still
  // "succeeds" in that case, so success alone isn't enough to reveal).
  const [greetingStarted, setGreetingStarted] = useState(false);
  // Forces `revealed` to true once GREETING_REVEAL_GRACE_MS has passed without greetingStarted —
  // a blocked autoplay attempt (no user gesture yet, essentially guaranteed on iOS/iPadOS Safari)
  // would otherwise leave a fully-loaded avatar hidden behind its static fallback indefinitely,
  // with no visible sign anything is wrong beyond a small play-button overlay. Started in
  // handleAvatarReady, right alongside the autoplay attempt itself.
  const [greetingGraceExpired, setGreetingGraceExpired] = useState(false);
  const greetingGraceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Hides the play button — either immediately on its own click (a real user gesture always
  // makes the audio audible), or via playGreeting's async result once a non-click autoplay
  // attempt is confirmed audible. Never on the mere attempt: a silently blocked autoplay must
  // leave this button up, since it's the only thing that can still make the greeting audible.
  const [playButtonDismissed, setPlayButtonDismissed] = useState(false);
  // True once the model has loaded and a spoken greeting is waiting for the visitor's "Start chat"
  // click. The download usually takes far longer than the browser treats the visitor's last click
  // (password/name/survey) as permission to play audio — so an autoplayed greeting right after the
  // loading screen stayed silent. The start button is a fresh user gesture that unlocks the audio
  // (see startChat).
  const [chatStartPending, setChatStartPending] = useState(false);
  // Mirrors "the loading screen still covers the chat" for handleAvatarReady, which TalkingHeadAvatar
  // calls through the closure of its first render (onReady isn't in its effect deps) — state would
  // be stale there. While set, the avatar's own ready-autoplay is skipped; startChat plays it.
  const greetingDeferredRef = useRef(false);
  // Whether the chat stage is showing — read when the model finishes loading: if that happened
  // during the pre-chat survey, its "continue" click is fresh enough to autoplay, no button needed.
  const inChatStageRef = useRef(false);
  const chatBodyRef = useRef<HTMLDivElement>(null);
  // On-device speech output (Pocket TTS), loaded in the background by the effect below when the
  // project offers it; until it's ready — or if it never gets there — the server speaks.
  const deviceTtsRef = useRef<PocketBrowserTts | null>(null);
  // performance.now() at which the audio queued on the device so far runs out — when the next
  // piece will start playing (see speakOnDevice).
  const deviceQueueEndRef = useRef(0);
  const threadRef = useRef<HTMLDivElement>(null);

  // One AbortController per in-flight turn, created in runSend() — read by mutationFn (passed into
  // the API calls) and by the playback code below (to cut a wait short) so interruptResponse() can
  // cancel both the network request and any audio still playing/queued with a single abort() call.
  const abortControllerRef = useRef<AbortController | null>(null);
  // Half of this becomes the composer/edit-textarea's max-height ("no higher than half the message
  // window") — measured off .chatColumn, not .thread, since .chatColumn's own height is fixed by the
  // page layout and doesn't shrink when the composer grows, unlike .thread's (which would feed back
  // into the very cap being computed).
  const [chatColumnHeight, setChatColumnHeight] = useState(0);
  const chatColumnObserverRef = useRef<ResizeObserver | null>(null);
  // A callback ref, not useRef+useEffect([]) — .chatColumn only mounts once the async tutor query
  // resolves into the "chat" stage, a commit later than an empty-deps effect already ran and found
  // nothing there. A callback ref fires exactly when the node itself mounts (and again on
  // collapse/expand of the chat column), so the observer never misses it.
  const chatColumnRef = useCallback((el: HTMLDivElement | null) => {
    chatColumnObserverRef.current?.disconnect();
    chatColumnObserverRef.current = null;
    if (!el) return;
    const observer = new ResizeObserver((entries) => setChatColumnHeight(entries[0].contentRect.height));
    observer.observe(el);
    chatColumnObserverRef.current = observer;
  }, []);
  const composerMaxHeight = chatColumnHeight > 0 ? chatColumnHeight / 2 : undefined;
  const composerTextareaRef = useRef<HTMLTextAreaElement>(null);
  useAutoResizeTextarea(composerTextareaRef, input, composerMaxHeight);

  // Keeps the thread pinned to its newest message whenever its own rendered size changes for any
  // reason — not just on a new message (see the messages/isPending effect below), but also when the
  // composer or an inline message-edit textarea grows/shrinks and eats into .thread's available height.
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => el.scrollTo({ top: el.scrollHeight }));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Serializes playback of streamed chunks: each chunk's decode+speak+wait is chained onto this
  // promise instead of firing independently, so chunk N+1 never starts before chunk N finishes —
  // chunks can arrive over the wire faster than they can be spoken.
  const playChainRef = useRef<Promise<void>>(Promise.resolve());

  function revealStreamedChunk(chunk: StreamChunkEvent) {
    avatarRef.current?.playMotions(chunk.motions);
    // Plan ticks land on the message as its sentence is spoken, so the step is ticked then.
    const checks = chunk.checks ?? [];
    setMessages((prev) => {
      if (chunk.index === 0) return [...prev, { role: "assistant", content: chunk.text, checks }];
      // A chunk that held nothing but a marker (see backend features/chat/motion.py).
      if (!chunk.text && !checks.length) return prev;
      const next = [...prev];
      const last = next[next.length - 1];
      // Chunks come pre-trimmed (see features/chat/streaming.py), so the original spacing/newlines
      // between them is already lost — a single space is a reasonable stand-in for the few
      // hundred ms until the "done" event replaces this with the exact original text below.
      const content = !chunk.text ? last.content : last.content ? `${last.content} ${chunk.text}` : chunk.text;
      // A step ticked again replaces its earlier tick, like the backend does within one reply —
      // so the list stays within what the history accepts back, even for an interrupted reply.
      const newIds = new Set(checks.map((check) => check.id));
      const kept = (last.checks ?? []).filter((check) => !newIds.has(check.id));
      next[next.length - 1] = { ...last, content, checks: [...kept, ...checks] };
      return next;
    });
  }

  /** Whether this message should be spoken on the device rather than by the server. */
  function speaksOnDevice(): boolean {
    return deviceTtsRef.current?.status === "ready";
  }

  /**
   * Speaks `text` on the device: every generated piece is queued right behind the audio already
   * queued (queueBuffer), so a reply plays without gaps while it's still being generated.
   * `onStart` runs when this text's first piece becomes audible — the moment the server path
   * reveals a chunk — or right away if nothing could be generated. Resolves once the text is
   * fully generated and `onStart` has run (not when it has finished playing), so the next chunk
   * is generated while this one plays.
   */
  async function speakOnDevice(text: string, onStart: () => void, signal: AbortSignal | undefined): Promise<void> {
    const engine = deviceTtsRef.current;
    let started: Promise<void> | null = null;
    if (engine && text.trim()) {
      try {
        await engine.synthesize(text, tutor.spokenLanguage, (samples, sampleRate) => {
          if (signal?.aborted) return;
          const buffer = new AudioBuffer({ length: samples.length, numberOfChannels: 1, sampleRate });
          buffer.copyToChannel(samples, 0);
          const now = performance.now();
          const startsAt = Math.max(now, deviceQueueEndRef.current);
          deviceQueueEndRef.current = startsAt + buffer.duration * 1000;
          avatarRef.current?.queueBuffer(buffer);
          started ??= delay(startsAt - now, signal).then(() => {
            if (!signal?.aborted) onStart();
          });
        }, signal);
      } catch (error) {
        // The text still goes out; only this part stays silent.
        console.error("On-device speech output failed.", error);
      }
    }
    if (started) await started;
    else if (!signal?.aborted) onStart();
  }

  async function playStreamedChunk(chunk: StreamChunkEvent): Promise<void> {
    // Already interrupted (see interruptResponse) — drop this chunk entirely: no text reveal, no
    // audio. Chunks already in flight when the student clicked stop still resolve and reach this
    // chained callback, so the check has to happen here, not just at the click itself.
    const signal = abortControllerRef.current?.signal;
    if (signal?.aborted) return;
    if (chunk.index === 0) avatarRef.current?.stopThinking();
    if (chunk.spoken !== undefined) {
      await speakOnDevice(chunk.spoken, () => revealStreamedChunk(chunk), signal);
      return;
    }
    if (!chunk.audioBase64) {
      revealStreamedChunk(chunk);
      return;
    }
    let audioBuffer: AudioBuffer | null = null;
    try {
      audioBuffer = (await avatarRef.current?.decodeAudio(chunk.audioBase64)) ?? null;
    } catch (error) {
      console.error("Audio chunk could not be decoded.", error);
    }
    // Revealed right as the audio is about to start (or right after we know it won't) — not
    // earlier — so the bubble text stays in step with what's actually audible.
    revealStreamedChunk(chunk);
    if (!audioBuffer) return;
    try {
      avatarRef.current?.speakBuffer(audioBuffer);
    } catch (error) {
      // Must not reject playChainRef: that would abort every remaining queued chunk, and make
      // mutationFn's `await playChainRef.current` below throw well after real audio has already
      // played — landing in the catch block that assumes nothing was spoken yet and falls back to
      // the plain endpoint, duplicating the spoken reply. Log and move on instead.
      console.error("Audio chunk could not be played.", error);
      return;
    }
    // A timer, not a real playback-ended signal — can drift by a fraction of a second on a long
    // reply. Accepted trade-off: wiring a real "ended" callback through TalkingHead/HeadAudio
    // would be a much bigger change for a rarely-noticeable amount of drift. Resolves early if
    // interrupted, via delay()'s own signal check, instead of waiting out the rest of this chunk.
    await delay(audioBuffer.duration * 1000, signal);
  }

  function handleStreamChunk(chunk: StreamChunkEvent) {
    playChainRef.current = playChainRef.current.then(() => playStreamedChunk(chunk));
  }

  function handleStreamDone(doneData: StreamDoneEvent) {
    // Queued onto the same chain so the exact final text only replaces the space-joined
    // approximation once every chunk has actually finished playing, not as soon as the SSE
    // stream itself ends (which happens as soon as the last chunk's TTS finishes, typically
    // before that last chunk has finished being spoken).
    playChainRef.current = playChainRef.current.then(() => {
      // In practice "done" never arrives after an abort (the stream is closed), but a stray race is
      // cheap to guard against: don't resurrect a message the student already interrupted.
      if (abortControllerRef.current?.signal.aborted) return;
      setMessages((prev) => {
        const next = [...prev];
        // The complete list of ticks replaces the per-chunk ones (identical unless a chunk was lost).
        next[next.length - 1] = { ...next[next.length - 1], content: doneData.reply, checks: doneData.checks };
        return next;
      });
    });
  }

  const sendMutation = useMutation({
    mutationFn: async ({ message, history }: { message: string; history: ChatMessage[] }) => {
      const signal = abortControllerRef.current!.signal;
      // Decided once per message: a model that finishes loading mid-reply waits for the next one.
      const onDevice = speaksOnDevice();
      deviceQueueEndRef.current = 0;
      if (tutor.streamingEnabled && tutor.ttsEnabled) {
        try {
          await publicChatApi.sendMessageStream(
            slug,
            message,
            history,
            { onChunk: handleStreamChunk, onDone: handleStreamDone },
            signal,
            onDevice,
          );
          await playChainRef.current;
          // playChainRef's own per-chunk delay() calls are a drifting estimate, not a real
          // "playback ended" signal (see playStreamedChunk) — wait for TalkingHead to actually
          // report idle before cutting it off, or a multi-chunk reply's tail can still be playing
          // here and get cut short. See stopSpeaking's own doc comment for why this call, not just
          // the interrupt path, is what actually puts the mouth back to idle once HeadAudio's
          // live-audio-driven lipsync goes quiet.
          await avatarRef.current?.waitUntilIdle(signal);
          avatarRef.current?.stopSpeaking();
          return { kind: "streamed" as const };
        } catch (error) {
          // Nothing was shown/spoken yet at this point (see sendMessageStream's contract) — safe
          // to retry via the always-available plain endpoint below instead of failing outright.
          console.error("Streaming chat failed, falling back to a plain request.", error);
        }
      }
      const res = await publicChatApi.sendMessage(slug, message, history, signal, onDevice);
      return { kind: "plain" as const, res };
    },
    onSuccess: async (result) => {
      avatarRef.current?.stopThinking();
      if (result.kind === "streamed") return;
      const { res } = result;
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply, checks: res.checks }]);
      // The whole reply arrives at once here, so its gestures play from the start rather than
      // at their sentence like in the streamed path.
      if (!abortControllerRef.current?.signal.aborted) avatarRef.current?.playMotions(res.motions);
      const replySignal = abortControllerRef.current?.signal;
      if (res.spoken != null && !replySignal?.aborted) {
        await speakOnDevice(res.spoken, () => undefined, replySignal);
        // Generated doesn't mean played: wait until the queued audio has run out (see
        // waitUntilIdle), then put the mouth back to idle like the server path below.
        await avatarRef.current?.waitUntilIdle(replySignal);
        avatarRef.current?.stopSpeaking();
      } else if (res.audioBase64 && !abortControllerRef.current?.signal.aborted) {
        // Deliberately awaited (not fire-and-forget): isPending then stays true for the whole
        // spoken reply, not just until the HTTP response arrives (@tanstack/query-core only
        // dispatches "success" once onSuccess resolves) — the same busy state as the streamed
        // path, which already awaits full playback in mutationFn.
        try {
          const audioBuffer = await avatarRef.current?.decodeAudio(res.audioBase64);
          if (audioBuffer) {
            avatarRef.current?.speakBuffer(audioBuffer);
            await delay(audioBuffer.duration * 1000, abortControllerRef.current?.signal);
            // See waitUntilIdle's own doc comment — the duration-based delay() above is still just
            // an estimate of when TalkingHead's own playback actually ends.
            await avatarRef.current?.waitUntilIdle(abortControllerRef.current?.signal);
            // See stopSpeaking's own doc comment — nothing else puts the mouth back to idle once
            // this reply's audio goes quiet.
            avatarRef.current?.stopSpeaking();
          }
        } catch (error) {
          console.error("Audio could not be played.", error);
        }
      }
    },
    onError: (err) => {
      avatarRef.current?.stopThinking();
      // A user-initiated stop (see interruptResponse) — it already appended the notice and stopped
      // the avatar, nothing more to do here. Only the plain path can land here for an abort: the
      // streamed path's sendMessageStream swallows its own AbortError and resolves normally instead.
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (err instanceof ApiError && err.status === 429) {
        setRateLimited(true);
        return;
      }
      // Every other failure (LLM provider timeout/error, network drop, ...) used to end here
      // silently — the user's own message is already shown (added optimistically in sendMessage,
      // before this request even resolves), so with no notice it just sits there forever with no
      // reply and no explanation. More likely to actually happen with two people chatting on the
      // same project at once: a shared LLM key's own rate limit or a transient provider error
      // under doubled load isn't retried indefinitely (see features/ai/llm's retry budget).
      setMessages((prev) => [...prev, { role: "system", content: t("publicChat.messageFailed") }]);
    },
  });

  const tutorForVoice = tutorQuery.data;
  const { isRecording, isFinalizingRecording, micErrorKey, sttLoadProgress, toggleRecording } = useVoiceInput({
    slug,
    modelUrl:
      tutorForVoice?.sttEnabled && tutorForVoice.browserSttModelUrl && gatePassed(tutorForVoice, visitorNameProvided)
        ? tutorForVoice.browserSttModelUrl
        : null,
    avatarRef,
    input,
    setInput,
    replyPending: sendMutation.isPending,
    onFinalText: sendMessage,
    onNotice: (key) => setMessages((prev) => [...prev, { role: "system", content: t(key) }]),
    // While the model loads, the avatar's own ready-autoplay of the greeting waits (see
    // greetingDeferredRef).
    onModelLoading: () => {
      greetingDeferredRef.current = true;
    },
    onModelSettled: (status) => {
      const tutor = tutorQuery.data;
      // A greeting to speak and a real wait behind us (no WebGPU at all settles at once, still
      // within the last click's autoplay permission): ask for a click first, see chatStartPending.
      if (inChatStageRef.current && tutor?.ttsEnabled && tutor.startAudioUrl && status !== "unsupported") {
        setChatStartPending(true);
      } else {
        greetingDeferredRef.current = false;
      }
      // No WebGPU, or the model failed to load: voice input still works, through the server.
      if (status !== "ready") {
        setMessages((prev) => [...prev, { role: "system", content: t("publicChat.sttFallbackNotice") }]);
      }
    },
  });

  // Shared core of sendMessage/editMessage/regenerateFrom: creates the AbortController this turn's
  // interrupt button will act on, and kicks off the mutation against an explicit history (rather
  // than reading the `messages` closure) — editMessage/regenerateFrom need a *truncated* history,
  // not "everything so far".
  function runSend(text: string, history: ChatMessage[]) {
    setRateLimited(false);
    avatarRef.current?.startThinking();
    abortControllerRef.current = new AbortController();
    sendMutation.mutate({ message: text, history: toApiHistory(history) });
  }

  function sendMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sendMutation.isPending) return;
    // history = the conversation so far BEFORE this new message (messages is still the old state
    // value here; the setMessages below only takes effect on the next render).
    runSend(trimmed, messages);
    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setInput("");
  }

  // Edits an earlier user message in place: drops it and everything after it (including its old
  // reply), then resends the edited text as a fresh turn — only reachable while nothing is in
  // flight (see ChatBubble's `disabled` prop), so there's no running response to interrupt first.
  function editMessage(index: number, newText: string) {
    const trimmed = newText.trim();
    if (!trimmed) return;
    const before = messages.slice(0, index);
    runSend(trimmed, before);
    setMessages([...before, { role: "user", content: trimmed }]);
  }

  // Resends an earlier user message unchanged — same truncation as editMessage, just without a text
  // change. Useful when a past reply was unsatisfying and the student wants another attempt at the
  // same question.
  function regenerateFrom(index: number) {
    const original = messages[index];
    if (!original || original.role !== "user") return;
    const before = messages.slice(0, index);
    runSend(original.content, before);
    setMessages([...before, original]);
  }

  // Stops the current reply: cancels the in-flight request (or, if it already arrived, whatever
  // audio is still playing/queued), and leaves a visible trace of the interruption in the thread.
  function interruptResponse() {
    abortControllerRef.current?.abort();
    avatarRef.current?.stopSpeaking();
    deviceQueueEndRef.current = 0;
    avatarRef.current?.stopMotion();
    setMessages((prev) => [...prev, { role: "system", content: t("publicChat.responseInterrupted") }]);
  }

  // Shared by the send button and the Enter key below. While a recording is still capturing audio,
  // `input` only holds the segments transcribed so far — sending it straight away would ship a
  // truncated message, drop the segment still being recorded, and leave the mic running in the
  // background (nothing would ever call stop() on it). Routing through toggleRecording() instead
  // makes a send-while-recording behave exactly like pressing "stop recording": it stops the mic,
  // transcribes the last segment, and sends the complete accumulated text once that's done (see
  // transcribeSegment's isFinal branch in useVoiceInput.ts). While a stop is already in flight
  // (isFinalizingRecording), that pending finalization will send on its own — sending here too
  // would double-send.
  function sendFromComposer() {
    // The reply is played long after this click, outside any gesture. Without a gesture-time
    // resume, a still-suspended AudioContext (autoplay policy, no greeting played yet) would
    // swallow the reply's audio silently.
    avatarRef.current?.unlockAudio();
    if (isRecording) {
      toggleRecording();
      return;
    }
    if (isFinalizingRecording) return;
    sendMessage(input);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    sendFromComposer();
  }

  // Enter sends (matching the composer's <form> submit); Shift+Enter inserts a newline instead.
  // isComposing guards against submitting mid-IME-composition (e.g. typing Japanese/Chinese), where
  // the Enter that confirms a candidate word must not also send the message.
  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      sendFromComposer();
    }
  }

  // After every new message (and when the typing bubble appears/disappears) scroll to the end of
  // the thread automatically instead of making the user scroll down themselves.
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sendMutation.isPending]);

  // One-time adoption of the initial state set in the configurator once the project has loaded —
  // after that only setChatOpen (a click on the handle) determines the state.
  // Not while password-locked: that response carries no layout yet (it'd always read as the default).
  useEffect(() => {
    const tutor = tutorQuery.data;
    if (tutor && chatOpen === null && !isLocked(tutor)) {
      setChatOpen(tutor.chatLayout !== "avatar_chat_collapsed");
    }
  }, [tutorQuery.data, chatOpen]);

  // Only cleans up on unmount — greetingGraceTimerRef itself is set from handleAvatarReady, not
  // from an effect (it needs to start at the exact moment the autoplay attempt does).
  useEffect(() => {
    return () => {
      if (greetingGraceTimerRef.current) clearTimeout(greetingGraceTimerRef.current);
    };
  }, []);

  // Loads the on-device speech output model once the visitor has passed any password/name gate
  // (same reason as above). Unlike speech recognition it doesn't hold the chat back: replies are
  // spoken by the server until the model is ready, and messages go out with speakOnDevice after.
  useEffect(() => {
    const tutor = tutorQuery.data;
    if (!tutor?.ttsEnabled || !tutor.browserTtsModelUrl) return;
    if (!gatePassed(tutor, visitorNameProvided)) return;
    if (deviceTtsRef.current) return;
    const engine = new PocketBrowserTts();
    deviceTtsRef.current = engine;
    engine.load(tutor.browserTtsModelUrl, () => {}, tutor.browserTtsVoice).catch((error) => {
      console.error("On-device speech output unavailable, the server speaks instead.", error);
    });
  }, [tutorQuery.data, visitorNameProvided]);

  // Ends the speech output worker with the page, like the speech recognition one below.
  useEffect(() => {
    return () => deviceTtsRef.current?.dispose();
  }, []);

  // Keeps keyboard focus and screen readers out of the chat while the loading screen covers it —
  // it's already rendered underneath so the avatar can load in parallel with the model. No deps:
  // the chat body can also mount later (leaving the survey) while it's already covered.
  const chatCovered = sttLoadProgress !== null || chatStartPending;
  useEffect(() => {
    if (chatBodyRef.current) chatBodyRef.current.inert = chatCovered;
  });

  if (tutorQuery.isLoading) {
    return (
      <PublicChatLayout>
        <div className={styles.centered}>{t("common.loading")}</div>
      </PublicChatLayout>
    );
  }

  if (tutorQuery.isError || !tutorQuery.data) {
    return (
      <PublicChatLayout>
        <div className={styles.centered}>{t("publicChat.unavailable")}</div>
      </PublicChatLayout>
    );
  }

  const tutor = tutorQuery.data;

  // Locked takes priority over every other stage, and the name gate over everything past it — a
  // manual survey/chat choice made before either was satisfied can't have happened yet anyway
  // (see below: the header's End-chat button and everything past these gates only render later).
  // First stage depends on project: with survey it starts there, else withthe chat
  const stage: Stage = isLocked(tutor)
    ? "locked"
    : tutor.requireVisitorName && !visitorNameProvided
      ? "name-gate"
      : (manualStage ?? (tutor.surveyBeforeUrl ? "before-survey" : "chat"));
  inChatStageRef.current = stage === "chat";
  // "Avatar only" needs voice both ways — without either there'd be no way to ask or no way to hear
  // the answer, so such a project falls back to avatar + chat (the Configurator warns about it).
  const chatLayout: ChatLayout =
    tutor.chatLayout === "avatar_only" && !(tutor.ttsEnabled && tutor.sttEnabled) ? "avatar_chat" : tutor.chatLayout;
  const showAvatar = chatLayout !== "chat_only";
  const hasChat = chatLayout !== "avatar_only";
  // Fallback only for the very first render, before the initialization effect above has run.
  const isChatOpen = hasChat && (!showAvatar || (chatOpen ?? chatLayout !== "avatar_chat_collapsed"));

  function endChat() {
    setManualStage(tutor.surveyAfterUrl ? "after-survey" : "done");
  }

  // Plays the project's pre-generated spoken greeting (see api/projects.ts::generateStartAudio) —
  // used both for the autoplay attempt below and the overlay play button. startAudioUrl is
  // router-relative (see features/projects/start_audio_router.py), so it needs the API origin
  // prefixed before fetch() can reach it — same as avatarModelUrl/avatarBackgroundUrl below.
  function playGreeting() {
    const url = tutor.startAudioUrl;
    if (!url) return;
    // Only reveal the avatar and hide the play button once speakFromUrl confirms the audio is
    // actually audible (not just successfully decoded/scheduled) — an autoplay attempt right as
    // the avatar loads can get silently suspended by the browser (no user gesture yet), in which
    // case both must stay as they are so the button remains the student's way to actually hear
    // it. A real click (see the button below) reliably resolves true, since clicking IS the
    // gesture that unblocks it.
    avatarRef.current
      ?.speakFromUrl(url)
      .then((audible) => {
        if (!audible) return;
        setGreetingStarted(true);
        setPlayButtonDismissed(true);
      })
      .catch((error) => {
        console.error("Greeting could not be played.", error);
        // The button's own onClick already dismissed itself synchronously (clicking IS the
        // gesture that unblocks playback) — bring it back so a failed attempt (stale audio URL,
        // transient network blip) doesn't remove the student's only way to hear the greeting at
        // all, with no way to retry. A no-op when this was the autoplay attempt instead, which
        // never dismissed the button to begin with.
        setPlayButtonDismissed(false);
      });
  }

  function handleAvatarReady() {
    setAvatarReady(true);
    // Loaded behind the speech recognition loading screen — startChat plays the greeting instead.
    if (greetingDeferredRef.current) return;
    autoplayGreeting();
  }

  // Clicked on the loading screen once the model is ready (see chatStartPending). Unlocks the
  // avatar's audio synchronously, inside this click — Safari only allows that during the gesture
  // itself, and speakFromUrl only gets to resume() after fetching the audio.
  function startChat() {
    setChatStartPending(false);
    greetingDeferredRef.current = false;
    avatarRef.current?.unlockAudio();
    // Avatar still loading: handleAvatarReady starts the greeting once it's there.
    if (avatarReady) autoplayGreeting();
  }

  function autoplayGreeting() {
    // Autoplay is frequently blocked by the browser without a prior user gesture — that's expected
    // and silently falls through to the overlay play button below, not an error state.
    if (!autoplayedGreetingRef.current && tutor.ttsEnabled && tutor.startAudioUrl) {
      autoplayedGreetingRef.current = true;
      playGreeting();
      // See greetingGraceExpired's own comment — a blocked attempt must not hide the avatar
      // forever just because nobody happens to tap the play button.
      greetingGraceTimerRef.current = setTimeout(() => setGreetingGraceExpired(true), GREETING_REVEAL_GRACE_MS);
    }
  }

  // Says what actually happens to the conversation, straight from the project's setting — the
  // page used to claim "chat isn't saved" unconditionally, which was untrue whenever the teacher
  // had recording switched on. A separate wording when a name/ID was also collected: "saved
  // anonymously" would be inaccurate once a name is attached to it.
  const privacyNotice = tutor.saveConversations
    ? tutor.requireVisitorName
      ? t("publicChat.privacySavedWithName")
      : t("publicChat.privacySaved")
    : t("publicChat.privacyNotSaved");

  // On the avatar stage normally, in the composer on a chat-only page.
  const micButton = tutor.sttEnabled && (
    <button
      type="button"
      className={`${styles.roundButton} ${isRecording ? styles.recording : ""}`}
      onClick={toggleRecording}
      disabled={isFinalizingRecording || (!isRecording && sendMutation.isPending)}
      aria-label={
        isFinalizingRecording
          ? t("publicChat.transcribing")
          : isRecording
            ? t("publicChat.stopRecording")
            : t("publicChat.voiceInput")
      }
      aria-pressed={isRecording}
    >
      {isFinalizingRecording ? (
        <Loader2 size={22} className={styles.spinIcon} />
      ) : isRecording ? (
        <Square size={20} fill="currentColor" />
      ) : (
        <Mic size={22} />
      )}
    </button>
  );
  const greetingPlayable = avatarReady && tutor.ttsEnabled && tutor.startAudioUrl && !playButtonDismissed;
  // Without a chat column, notices that would otherwise land in the thread (a failed recording, the
  // on-device fallback, ...) show over the avatar instead — only the latest, if it's the last thing
  // that happened.
  const lastMessage = messages[messages.length - 1];
  const stageNotice = !hasChat
    ? micErrorKey
      ? t(micErrorKey)
      : rateLimited
        ? t("errors.RATE_LIMIT_CHAT")
        : lastMessage?.role === "system"
          ? lastMessage.content
          : null
    : null;

  return (
    <PublicChatLayout showLanguageSwitcher={false}>
      <header className={styles.header}>
        <Avatar name={tutor.title} size="md" />
        <div className={styles.headerInfo}>
          <h1>{tutor.title}</h1>
        </div>
        <div className={styles.headerActions}>
          <LanguageSwitcher compact />
          {stage === "chat" && (
            <button
              type="button"
              className={styles.infoButton}
              onClick={endChat}
              disabled={sendMutation.isPending}
              aria-label={t("publicChat.endChat")}
              title={t("publicChat.endChat")}
            >
              <LogOut size={24} />
            </button>
          )}
          <ModelInfoButton llmModel={tutor.llmModel} />
        </div>
      </header>

      {stage === "locked" && <PasswordGate slug={slug} />}

      {stage === "name-gate" && <NameGate slug={slug} onProvided={() => setVisitorNameProvided(true)} />}

      {stage === "before-survey" && tutor.surveyBeforeUrl && (
        <SurveyEmbed
          url={tutor.surveyBeforeUrl}
          title={t("publicChat.surveyBeforeTitle")}
          continueLabel={t("publicChat.continueToChat")}
          onContinue={() => setManualStage("chat")}
          onSkip={() => setManualStage("chat")}
        />
      )}

      {stage === "chat" && (
        <div className={styles.chatArea}>
          {chatCovered && (
            <SttLoadingScreen progress={sttLoadProgress} onStart={chatStartPending ? startChat : undefined} />
          )}
          <div className={styles.body} ref={chatBodyRef}>
            {showAvatar ? (
              <div className={`${styles.avatarStage} ${!isChatOpen ? styles.avatarStageFull : ""}`}>
                <TalkingHeadAvatar
                  avatarUrl={tutor.avatarModelUrl ?? undefined}
                  backgroundImageUrl={tutor.avatarBackgroundUrl ?? undefined}
                  speechEnabled={tutor.ttsEnabled}
                  motionEnabled={tutor.motionEnabled}
                  thinkingMotionsEnabled={tutor.thinkingMotionsEnabled}
                  fallback={<Avatar name={tutor.title} size="lg" />}
                  onReady={handleAvatarReady}
                  // Stays behind the static fallback until the greeting has actually started, instead
                  // of idling in the background behind the play button — nothing to wait for (no
                  // greeting configured) reveals it immediately, as before. greetingGraceExpired is the
                  // escape hatch once autoplay is confirmed blocked instead of just still pending (see
                  // its own comment) — without it, a browser that never allows autoplay (essentially
                  // every iOS/iPadOS Safari visit) would hide a fully-loaded avatar indefinitely.
                  revealed={!(tutor.ttsEnabled && tutor.startAudioUrl) || greetingStarted || greetingGraceExpired}
                  ref={avatarRef}
                />
                {/* Dismissed two ways: immediately (synchronously) on its own click — since clicking
                    IS the gesture that guarantees audio actually plays — or by playGreeting's async
                    result, once an autoplay attempt confirms the audio was actually audible (not
                    silently suspended). Never tied to the mere ATTEMPT, or a blocked autoplay would
                    hide the only control that could still make it audible. */}
                {greetingPlayable && (
                  <button
                    type="button"
                    className={styles.playGreetingButton}
                    onClick={() => {
                      setPlayButtonDismissed(true);
                      playGreeting();
                    }}
                    aria-label={t("publicChat.playGreeting")}
                    title={t("publicChat.playGreeting")}
                  >
                    <Play size={24} fill="currentColor" />
                  </button>
                )}
                {/* Avatar only: the privacy notice and any notices normally sit in the chat column. */}
                {!hasChat && (
                  <div className={styles.stageTop}>
                    <p className={styles.stageNote}>{privacyNotice}</p>
                    {stageNotice && (
                      <p className={`${styles.stageNote} ${styles.stageNotice}`} role="status">
                        {stageNotice}
                      </p>
                    )}
                    {/* In the same column as the notices, so it can't cover them. */}
                    {tutor.checklist && (
                      <ChecklistCard
                        items={tutor.checklist.items}
                        ordered={tutor.checklist.ordered}
                        doneIds={doneIds}
                        className={styles.stageChecklist}
                      />
                    )}
                  </div>
                )}
                {hasChat && tutor.checklist && (
                  <ChecklistCard items={tutor.checklist.items} ordered={tutor.checklist.ordered} doneIds={doneIds} />
                )}
                {/* No composer on an avatar-only page — what's being recognized shows here instead. */}
                {!hasChat && (isRecording || isFinalizingRecording) && input && (
                  <p className={styles.liveCaption} aria-live="polite">
                    {input}
                  </p>
                )}
                {/* Mic (input) + stop (interrupting the reply) deliberately sit here, not in the
                    chat column's composer — like the control bar under the video in a video call,
                    they stay reachable regardless of whether the chat is expanded or collapsed. */}
                {(tutor.sttEnabled || sendMutation.isPending) && (
                  <div className={styles.stageControls}>
                    {micButton}
                    {/* Not gated on tutor.ttsEnabled: even a text-only (no TTS) reply can be slow enough
                        to want stopping, since sendMutation.isPending already spans "waiting for the
                        LLM" too, not just "the avatar is speaking". */}
                    {sendMutation.isPending && (
                      <button
                        type="button"
                        className={styles.roundButton}
                        onClick={interruptResponse}
                        aria-label={t("publicChat.stopResponse")}
                        title={t("publicChat.stopResponse")}
                      >
                        <Square size={20} fill="currentColor" />
                      </button>
                    )}
                  </div>
                )}
              </div>
            ) : (
              // Chat only: no 3D model at all — replies and the greeting are still spoken, through
              // the same handle (see AudioOnlySpeaker).
              tutor.ttsEnabled && <AudioOnlySpeaker onReady={handleAvatarReady} ref={avatarRef} />
            )}

            {/* Pull-out handle at the edge of the chat column — horizontal below the avatar on
                mobile, vertical next to the column on desktop (see the CSS media query). Sits as
                a normal flex sibling exactly at the seam between avatar and chat column, in both
                layouts, without its own positioning logic per breakpoint. Only when there's both
                an avatar and a chat to trade space between. */}
            {showAvatar && hasChat && (
              <button
                type="button"
                className={styles.chatToggle}
                onClick={() => setChatOpen(!isChatOpen)}
                aria-expanded={isChatOpen}
                aria-label={isChatOpen ? t("publicChat.collapseChat") : t("publicChat.expandChat")}
                title={isChatOpen ? t("publicChat.collapseChat") : t("publicChat.expandChat")}
              >
                {isChatOpen ? <X size={16} /> : <MessageCircle size={18} />}
              </button>
            )}

            {isChatOpen && (
              <div className={`${styles.chatColumn} ${!showAvatar ? styles.chatColumnSolo : ""}`} ref={chatColumnRef}>
                <div className={styles.chatColumnHeader}>
                  <h2>{t("publicChat.chatTitle")}</h2>
                  <p className={styles.privacyNote}>{privacyNotice}</p>
                </div>

                {/* Same sentence as the desktop subtitle above — only one of the two is ever visible
                    (see the media query in PublicChat.module.css), so students get it either way. */}
                <p className={`${styles.privacyNote} ${styles.privacyNoteMobile}`}>{privacyNotice}</p>

                {/* Chat only: no stage to float over, so the plan sits above the thread. */}
                {!showAvatar && tutor.checklist && (
                  <ChecklistCard
                    items={tutor.checklist.items}
                    ordered={tutor.checklist.ordered}
                    doneIds={doneIds}
                    variant="inline"
                    className={styles.chatChecklist}
                  />
                )}

                <div className={styles.thread} ref={threadRef}>
                  <ChatBubble
                    role="assistant"
                    content={tutor.startPrompt || t("configurator.step4.defaultGreeting", { name: tutor.title })}
                  />
                  {/* Chat only: the play button that normally sits on the avatar goes under the
                      greeting it plays instead. */}
                  {!showAvatar && greetingPlayable && (
                    <button
                      type="button"
                      className={styles.inlineGreetingButton}
                      onClick={() => {
                        setPlayButtonDismissed(true);
                        playGreeting();
                      }}
                    >
                      <Play size={14} fill="currentColor" /> {t("publicChat.playGreeting")}
                    </button>
                  )}
                  {messages.map((message, index) =>
                    message.role === "system" ? (
                      <p key={index} className={styles.systemNotice}>
                        {message.content}
                      </p>
                    ) : (
                      <ChatBubble
                        key={index}
                        role={message.role}
                        content={message.content}
                        editable={message.role === "user"}
                        disabled={sendMutation.isPending}
                        onEdit={(text) => editMessage(index, text)}
                        onRegenerate={() => regenerateFrom(index)}
                        maxHeightPx={composerMaxHeight}
                        maxLengthChars={MAX_CHAT_MESSAGE_CHARS}
                      />
                    ),
                  )}
                  {sendMutation.isPending && <TypingBubble />}
                </div>

                {rateLimited && (
                  <div className={styles.notice}>
                    <Callout variant="warning">{t("errors.RATE_LIMIT_CHAT")}</Callout>
                  </div>
                )}

                {micErrorKey && (
                  <div className={styles.notice}>
                    <Callout variant="warning">{t(micErrorKey)}</Callout>
                  </div>
                )}

                <form className={styles.composer} onSubmit={handleSubmit}>
                  {/* Chat only: no avatar stage to hold the mic (the send button doubles as stop). */}
                  {!showAvatar && micButton}
                  <textarea
                    ref={composerTextareaRef}
                    className={styles.textInput}
                    rows={1}
                    placeholder={t("publicChat.messagePlaceholder")}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleComposerKeyDown}
                    aria-label={t("publicChat.messageAriaLabel")}
                    maxLength={MAX_CHAT_MESSAGE_CHARS}
                  />
                  <button
                    type={sendMutation.isPending ? "button" : "submit"}
                    className={`${styles.roundButton} ${styles.sendButton}`}
                    onClick={sendMutation.isPending ? interruptResponse : undefined}
                    disabled={!sendMutation.isPending && isFinalizingRecording}
                    aria-label={sendMutation.isPending ? t("publicChat.stopResponse") : t("publicChat.send")}
                  >
                    {sendMutation.isPending ? <Square size={20} fill="currentColor" /> : <Send size={22} />}
                  </button>
                </form>
              </div>
            )}
          </div>
        </div>
      )}

      {stage === "after-survey" && tutor.surveyAfterUrl && (
        <SurveyEmbed
          url={tutor.surveyAfterUrl}
          title={t("publicChat.surveyAfterTitle")}
          continueLabel={t("publicChat.done")}
          onContinue={() => setManualStage("done")}
          onSkip={() => setManualStage("done")}
        />
      )}

      {stage === "done" && <div className={styles.centered}>{t("publicChat.chatEnded")}</div>}
    </PublicChatLayout>
  );
}
