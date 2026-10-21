"""
Chat Pipeline

The one place that combines the AI building blocks (app/features/ai) into a chat turn: resolve
the project's keys, ask the LLM, save the exchange, and synthesize speech — either as one reply
(reply_turn) or as sentence-sized chunks streamed while the LLM is still writing (stream_turn).
Used by both the public chat (public_router.py) and the configurator's preview chat
(preview_router.py); each maps failures to its own HTTP responses.

How to use:
    context = prepare_chat(session, project)  # None if no LLM key is configured
    session.close()                            # optional: don't hold a DB connection during the LLM call
    reply = reply_turn(context, ChatTurn(message, history))
"""

import base64
import logging
import time
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from normt2s import Piece, RuleSet
from sqlmodel import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.features.ai import llm
from app.features.ai.tts import VoiceReference, synthesize_speech
from app.features.ai.tts.normalizer import normalize_for_speech, speech_stream
from app.features.api_keys.models import UserApiKey
from app.features.api_keys.resolve import resolve_llm_key, resolve_tts_key
from app.features.chat.conversation_store import save_turn
from app.features.chat.checklist import Check, Checklist, checklist_for, checklist_prompt
from app.features.chat.motion import MotionMark, compose_system_prompt, extract_markers
from app.features.knowledge.prompt import reference_block
from app.features.knowledge.retrieval import KnowledgeContext, Passage, prepare_knowledge, retrieve, sources_for_transcript
from app.features.media.service import voice_reference_for_project
from app.features.projects.models import Project
from app.features.pronunciation.service import rules_for

# Visitors deliberately only get generic error messages (no technical detail) — so the actual
# error is still visible *somewhere* instead of being swallowed entirely, it goes into the
# server log here.
logger = logging.getLogger(__name__)

# Shared across every streamed reply instead of one ThreadPoolExecutor(max_workers=1) per
# request: chunk *yield* order is already enforced per-request by the `futures` deque in
# stream_turn() below (it only pops a chunk once the one before it is done), so which worker
# thread actually runs a given chunk's synthesis doesn't affect ordering — sharing one bounded
# pool just replaces "one thread per concurrent streaming reply, unbounded" with a fixed ceiling.
_tts_executor = ThreadPoolExecutor(max_workers=settings.tts_stream_worker_pool_size)


@dataclass(frozen=True)
class ChatContext:
    """Everything a chat turn needs from the project and its keys, read up front.

    Plain values and detached key records only, so a turn can run for seconds (or, streamed, after
    the route has returned) without touching — or holding open — the request's DB session.
    """

    project_id: str
    llm_key: UserApiKey
    tts_enabled: bool
    # May be None even with tts_enabled: no cloud key configured, so synthesize_speech falls back
    # to the local-TTS sidecar (or fails like any other TTS error, see features/ai/tts).
    tts_key: UserApiKey | None
    preprompt: str
    temperature: float | None
    top_p: float | None
    thinking: bool | None
    start_prompt: str | None
    tts_voice: str | None
    spoken_language: str
    save_conversations: bool
    # The teacher's voice clip local TTS clones the voice from (None: default voice). Resolved
    # here, while the DB session is still at hand — the reply itself is synthesized without one.
    voice_clip: VoiceReference | None = None
    # The project's knowledge bases (see features/knowledge/retrieval.py), None if it uses none.
    knowledge: KnowledgeContext | None = None
    # The teacher's own pronunciation word list for spoken_language (None: empty or TTS off).
    # Loaded once here, so a streamed reply doesn't re-read it for every chunk.
    pronunciation: RuleSet | None = None
    # Whether the LLM may mark gestures for the avatar (see features/chat/motion.py) — already
    # False when the project's layout shows no avatar.
    motion_enabled: bool = False
    # The project's own gesture instructions, None = built-in default (see chat/motion.py).
    motion_prompt: str | None = None
    # The conversation plan (see features/chat/checklist.py), None when off. Unlike gestures it
    # also applies to the chat-only layout: it structures the conversation, not the avatar.
    checklist: Checklist | None = None
    # The visitor's device speaks this reply (see Project.tts_browser_enabled): no synthesis here,
    # the reply's spoken form goes out instead — normalized and with the teacher's pronunciation
    # list applied, exactly what the server's TTS would have read.
    speak_on_device: bool = False


@dataclass
class ChatTurn:
    """One visitor message, plus who sent it and when (for the saved conversation)."""

    message: str
    history: list[dict]
    visitor_id: str | None = None
    visitor_name: str | None = None
    # Captured before any LLM/TTS work — the closest we get to "when the visitor actually sent
    # this", used for the saved message's per-message timestamp.
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # The plan steps done so far, replayed from the visitor's history (see checklist.py::replay).
    checklist_done: frozenset[str] = frozenset()


@dataclass
class ChatReply:
    text: str
    audio_base64: str | None
    content_type: str | None
    llm_ms: float
    # None (not ~0ms) when TTS didn't actually run (disabled, or enabled with nothing configured to
    # synthesize with).
    tts_ms: float | None
    # None when the project uses no knowledge base.
    retrieval_ms: float | None = None
    # Gestures for the avatar, in reply order (see features/chat/motion.py).
    motions: list[str] = field(default_factory=list)
    # Plan steps ticked off or taken back in this reply (see features/chat/checklist.py).
    checks: list[Check] = field(default_factory=list)
    # What the visitor's device should say — only with ChatContext.speak_on_device.
    spoken: str | None = None


class LLMFailed(Exception):
    """The LLM call itself failed; `__cause__` is the provider's original exception."""


def prepare_chat(session: Session, project: Project, llm_key: UserApiKey | None = None) -> ChatContext | None:
    """Resolve the project's LLM (and, if enabled, TTS) key and snapshot what a turn needs.

    `llm_key` replaces the project's own LLM key (the latency test compares models with it; the
    caller has checked it's the owner's). None if there's no usable LLM key. The key records are expunged from `session` so
    their already-loaded columns can never be invalidated by a later commit expiring them — the
    caller may close or keep using the session independently of the returned context.
    """
    api_key = llm_key or resolve_llm_key(session, project)
    if api_key is None:
        return None
    tts_api_key = resolve_tts_key(session, project) if project.tts_enabled else None
    session.expunge(api_key)
    if tts_api_key is not None:
        session.expunge(tts_api_key)
    return ChatContext(
        project_id=project.id,
        llm_key=api_key,
        tts_enabled=project.tts_enabled,
        tts_key=tts_api_key,
        preprompt=project.preprompt or "",
        temperature=project.temperature,
        top_p=project.top_p,
        thinking={"on": True, "off": False}.get(project.thinking_mode),
        start_prompt=project.start_prompt,
        tts_voice=project.tts_voice,
        spoken_language=project.spoken_language,
        save_conversations=project.save_conversations,
        voice_clip=voice_reference_for_project(session, project) if tts_api_key is None else None,
        knowledge=prepare_knowledge(session, project, api_key.provider),
        pronunciation=rules_for(session, project.user_id, project.spoken_language) if project.tts_enabled else None,
        motion_enabled=project.motion_enabled and project.chat_layout != "chat_only",
        motion_prompt=project.motion_prompt,
        checklist=checklist_for(project),
    )


def _retrieve(context: ChatContext, turn: ChatTurn) -> tuple[list[Passage], float | None]:
    """The knowledge-base passages for this turn and how long finding them took (None: no KB).
    Never raises — see features/knowledge/retrieval.py::retrieve."""
    if context.knowledge is None:
        return [], None
    start = time.perf_counter()
    passages = retrieve(context.knowledge, turn.message, turn.history)
    return passages, (time.perf_counter() - start) * 1000


def _chat_request(context: ChatContext, turn: ChatTurn, passages: list[Passage], *, plain: bool = False) -> llm.ChatRequest:
    """The LLM request for `turn`; `plain` leaves out the gesture instructions and the plan."""
    reference = reference_block(context.knowledge.mode, passages) if context.knowledge else None
    plan = None if plain or context.checklist is None else checklist_prompt(context.checklist, set(turn.checklist_done))
    preprompt = compose_system_prompt(context.preprompt, context.motion_prompt, not plain and context.motion_enabled, plan)
    return llm.ChatRequest(
        preprompt,
        turn.message,
        context.temperature,
        context.top_p,
        context.start_prompt,
        turn.history,
        reference_material=reference,
        thinking=context.thinking,
    )


def _save(
    context: ChatContext,
    turn: ChatTurn,
    reply: str,
    reply_ready_at: datetime,
    passages: list[Passage],
    motions: list[MotionMark] | None = None,
    checks: list[Check] | None = None,
) -> None:
    save_turn(
        context.project_id,
        turn.visitor_id,
        turn.message,
        reply,
        turn.received_at,
        reply_ready_at,
        turn.visitor_name,
        sources=sources_for_transcript(passages) if context.knowledge else None,
        # Kept so a teacher can see which gestures the LLM chose and where (see analytics).
        motions=[m.to_json() for m in motions] if motions else None,
        # Kept so a teacher can see how far each student got through the plan (see analytics).
        checks=[c.to_json() for c in checks] if checks else None,
    )


def _split_markers(context: ChatContext, text: str) -> tuple[str, list[MotionMark], list[Check]]:
    """`text` without markers, the gestures and the plan ticks — none of a kind the project has
    off, whose markers are still cut out: the model may write one anyway, and students never see
    one. Ticks of a step number the plan doesn't have are dropped."""
    clean, marks, ticks = extract_markers(text)
    # Only a step's last tick in the reply counts — it decides the step's state. That also keeps
    # the list at one entry per step, within what ChatHistoryEntry.checks accepts back: a model
    # stuck in a loop would otherwise return more ticks than the next request may carry.
    last: dict[str, Check] = {}
    if context.checklist is not None:
        for tick in ticks:
            item_id = context.checklist.id_for(tick.number)
            if item_id is not None:
                last.pop(item_id, None)
                last[item_id] = Check(item_id, tick.done, tick.offset)
    return clean, marks if context.motion_enabled else [], list(last.values())


def _synthesize(context: ChatContext, text: str, normalized: bool = False) -> tuple[str | None, str | None, float]:
    """Speech for `text` as (audioBase64, contentType, ms); never raises — speech output is an
    addition to the text reply, so a TTS failure is logged and the text still goes out.
    `normalized`: `text` already is the spoken form (see synthesize_speech)."""
    # A chunk can consist of nothing but a gesture marker.
    if not context.tts_enabled or context.speak_on_device or not text:
        return None, None, 0.0
    synth_start = time.perf_counter()
    try:
        audio_bytes, content_type = synthesize_speech(
            text,
            context.tts_voice,
            context.tts_key,
            context.spoken_language,
            voice_clip=context.voice_clip,
            pronunciation=context.pronunciation,
            normalized=normalized,
        )
    except Exception:
        logger.exception("TTS failed (project_id=%s)", context.project_id)
        return None, None, (time.perf_counter() - synth_start) * 1000
    return base64.b64encode(audio_bytes).decode(), content_type, (time.perf_counter() - synth_start) * 1000


def reply_turn(context: ChatContext, turn: ChatTurn, *, save: bool = True) -> ChatReply:
    """Ask the LLM, save the exchange (if the project saves conversations and `save`), then
    synthesize the whole reply. Raises LLMFailed if the LLM call fails."""
    # Timed for the client-side latency-test log (see pages/PublicChat/index.tsx) — not used by
    # the default UI, just extra fields riding along in the response.
    passages, retrieval_ms = _retrieve(context, turn)
    llm_start = time.perf_counter()
    try:
        reply = llm.complete(context.llm_key, _chat_request(context, turn, passages))
    except Exception as exc:
        raise LLMFailed() from exc
    llm_ms = (time.perf_counter() - llm_start) * 1000
    reply_ready_at = datetime.now(timezone.utc)
    reply, motions, checks = _split_markers(context, reply)

    if save and context.save_conversations:
        try:
            _save(context, turn, reply, reply_ready_at, passages, motions, checks)
        except Exception:
            # Same as in stream_turn: the reply exists (and was paid for), so a failed save, e.g.
            # "database is locked" under a class-sized burst, mustn't cost the visitor their answer.
            logger.exception("Saving the conversation failed (project_id=%s)", context.project_id)

    tts_start = time.perf_counter()
    audio_base64, content_type, _ = _synthesize(context, reply)
    tts_ms = (time.perf_counter() - tts_start) * 1000 if audio_base64 is not None else None
    spoken = normalize_for_speech(reply, context.spoken_language, context.pronunciation) if context.speak_on_device else None
    return ChatReply(
        reply, audio_base64, content_type, llm_ms, tts_ms, retrieval_ms, [m.name for m in motions], checks, spoken
    )


@dataclass
class EvaluationAnswer:
    text: str
    passages: list[Passage]
    retrieval_ms: float | None
    llm_ms: float


def answer_for_evaluation(context: ChatContext, question: str) -> EvaluationAnswer:
    """One test question through the same retrieval and prompt as a student's first message —
    without speech, gestures and saving (see features/evaluation/runner.py). Raises LLMFailed."""
    turn = ChatTurn(question, [])
    passages, retrieval_ms = _retrieve(context, turn)
    llm_start = time.perf_counter()
    try:
        # Without the gesture instructions: the evaluation judges the answer's content, and stray
        # markers would only add noise to it.
        text = llm.complete(context.llm_key, _chat_request(context, turn, passages, plain=True))
    except Exception as exc:
        raise LLMFailed() from exc
    return EvaluationAnswer(text, passages, retrieval_ms, (time.perf_counter() - llm_start) * 1000)


def stream_turn(context: ChatContext, turn: ChatTurn) -> Iterator[tuple[str, dict]]:
    """Yield (event, data) pairs for a streamed reply: one "chunk" per sentence-sized piece (see
    ai/tts/normalizer.py::speech_stream), each synthesized as soon as it's ready — so the avatar can start speaking well
    before the full reply exists — then "done" (or "error" if the LLM failed).

    Never raises for an LLM or save failure: by the time either happens, part of the reply may
    already have reached the client.
    """
    start_time = time.perf_counter()
    first_chunk_ms: float | None = None
    # Time until the first chunk is handed to TTS, distinct from first_chunk_ms (time until
    # that chunk's synthesis *finishes*) — isolates LLM/chunking speed from TTS speed. Captured
    # in submit() itself since that's the one place both the per-delta loop and the
    # flush_piece() tail path funnel through.
    first_chunk_ready_ms: float | None = None
    # Time until the LLM's first token — separates the provider's own response time from the
    # stream waiting for a whole sentence.
    first_token_ms: float | None = None
    total_tts_ms: float | None = 0.0 if context.tts_enabled else None
    full_text_parts: list[str] = []
    # Each piece carries the raw text (shown, and where the markers are) and its spoken form
    # (normalized, with the teacher's word list), so a chunk is normalized once, as a whole.
    stream = speech_stream(context.spoken_language, context.pronunciation)
    # Chunks are submitted to the shared _tts_executor (module-level, see its definition
    # above) in order, but output order is enforced independently by pop_ready() only ever
    # popping the deque's front once it's done — so it doesn't matter whether the shared
    # pool's worker threads finish chunk N+1 before chunk N, synthesis for chunk N still
    # overlaps with the LLM producing chunk N+1 rather than a fully sequential "wait for
    # TTS, then ask for more".
    futures: deque[tuple[int, "Future[tuple[str | None, str | None, float]]", str, str, list[str], list[dict], float]] = deque()
    next_index = 0

    def submit(piece: Piece) -> None:
        nonlocal next_index, first_chunk_ready_ms
        # Stripped like the whole reply: the browser joins chunks with a space.
        raw = piece.raw.strip()
        if not raw:
            return
        ready_ms = (time.perf_counter() - start_time) * 1000
        if first_chunk_ready_ms is None:
            first_chunk_ready_ms = ready_ms
        # The stream never cuts inside a marker, so each chunk's markers can be cut out on their
        # own; the browser plays them when that chunk's audio starts.
        text, marks, checks = _split_markers(context, raw)
        motions = [m.name for m in marks]
        ticks = [c.to_client() for c in checks]
        # The normalizer only silences markers that stand apart; motion.py decides what a marker
        # is, also one glued to a word ("Richtig::nod_yes::"), so the spoken side goes through it too.
        spoken = extract_markers(piece.spoken)[0].strip()
        futures.append(
            (next_index, _tts_executor.submit(_synthesize, context, spoken, True), text, spoken, motions, ticks, ready_ms)
        )
        next_index += 1

    def chunk_event(
        idx: int, future: Future, text: str, spoken: str, motions: list[str], checks: list[dict], ready_ms: float
    ) -> tuple[str, dict]:
        nonlocal first_chunk_ms, total_tts_ms
        audio_b64, content_type, synth_ms = future.result()
        if total_tts_ms is not None:
            total_tts_ms += synth_ms
        sent_ms = (time.perf_counter() - start_time) * 1000
        if first_chunk_ms is None:
            first_chunk_ms = sent_ms
        # The *Ms fields are per-chunk timings (since the request started) for the dashboard's
        # latency test; the public chat ignores them.
        event = {
            "index": idx,
            "text": text,
            "audioBase64": audio_b64,
            "contentType": content_type,
            "motions": motions,
            # Plan ticks in this chunk, so the browser ticks the step while it is being spoken.
            "checks": checks,
            "textReadyMs": ready_ms,
            "ttsMs": synth_ms if context.tts_enabled else None,
            "sentMs": sent_ms,
        }
        if context.speak_on_device:
            event["spoken"] = spoken
        return "chunk", event

    def pop_ready():
        while futures and futures[0][1].done():
            yield chunk_event(*futures.popleft())

    # Before the LLM call and inside the measured time: retrieval delays the first audio too.
    passages, retrieval_ms = _retrieve(context, turn)

    llm_error: Exception | None = None
    try:
        for delta in llm.stream(context.llm_key, _chat_request(context, turn, passages)):
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - start_time) * 1000
            full_text_parts.append(delta)
            for piece in stream.feed_pieces(delta):
                submit(piece)
            yield from pop_ready()
    except Exception as exc:  # LLM failure mid-stream — no retry, whatever was already sent stays.
        llm_error = exc
    llm_ms = (time.perf_counter() - start_time) * 1000
    reply_ready_at = datetime.now(timezone.utc)

    if llm_error is None:
        tail = stream.flush_piece()
        if tail is not None:
            submit(tail)

    # Drain whatever's left, waiting for each in turn — most of this already finished while
    # later LLM tokens were still streaming in.
    while futures:
        yield chunk_event(*futures.popleft())

    if llm_error is not None:
        logger.error("LLM streaming failed (project_id=%s): %s", context.project_id, llm_error)
        yield "error", {"detail": ErrorCode.CHAT_UNAVAILABLE}
        return

    # Belt and braces: if ArcanaReferenceGuard ever leaked, the saved transcript and the
    # final text sent to the client still come out clean.
    full_reply = llm.strip_arcana_references("".join(full_text_parts).strip())
    # Taken from the whole reply rather than collected per chunk, so the saved offsets refer to
    # exactly the text that is saved.
    full_reply, motion_marks, checks = _split_markers(context, full_reply)

    if context.save_conversations:
        try:
            _save(context, turn, full_reply, reply_ready_at, passages, motion_marks, checks)
        except Exception:
            # The reply itself already reached the visitor via the chunk events above —
            # only the save failed, so log it instead of turning it into an error event
            # this late (that would incorrectly tell the client the whole reply failed).
            logger.exception("Saving the conversation failed (project_id=%s)", context.project_id)

    yield "done", {
        "reply": full_reply,
        # All of the reply's plan ticks — what the browser stores on the message, see checklist.py.
        "checks": [c.to_client() for c in checks],
        "llmMs": llm_ms,
        "llmFirstTokenMs": first_token_ms,
        "firstChunkMs": first_chunk_ms,
        "firstChunkTextReadyMs": first_chunk_ready_ms,
        "ttsMs": total_tts_ms,
        "retrievalMs": retrieval_ms,
    }
