"""
Public Chat Routes

The unauthenticated endpoints a published project's visitors actually use: load the project's
public info, send a chat message, and transcribe a voice message. There's no login here —
visitors are tracked by an anonymous cookie (visitor_id) instead — and every route is
rate-limited (see app/core/rate_limit.py) since anyone can call them.

The chat itself (LLM → save → TTS) lives in pipeline.py; these routes do the visitor checks and
turn failures into deliberately generic errors (no provider/model detail for anonymous visitors).
"""

import logging
import time
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.cookies import get_or_set_visitor_id
from app.core.deps import get_published_project, get_session
from app.core.error_codes import ErrorCode
from app.core.rate_limit import (
    enforce_chat_unlock_rate_limit,
    enforce_public_chat_rate_limit,
    enforce_public_transcribe_rate_limit,
)
from app.features.ai.stt import transcribe_audio
from app.features.api_keys.resolve import browser_stt_model_url_for, browser_tts_model_url_for, resolve_stt_key
from app.features.chat.audio_upload import read_audio_upload
from app.features.chat.checklist import checklist_for, replay
from app.features.chat.pipeline import ChatContext, ChatTurn, LLMFailed, prepare_chat, reply_turn, stream_turn
from app.features.chat.schemas import (
    ChatMessageIn,
    ChatMessageOut,
    ChatUnlockOut,
    ChatUnlockRequest,
    PublicChecklistItemOut,
    PublicChecklistOut,
    PublicProjectOut,
    TranscriptionOut,
)
from app.features.chat.streaming import sse_event
from app.features.chat.unlock import assert_unlocked, is_unlocked, issue_unlock_token, verify_chat_password
from app.features.chat.visitor_log import log_access
from app.features.chat.visitor_name import assert_visitor_name_provided, clean_visitor_name
from app.features.projects.models import Project
from app.features.users.models import User

router = APIRouter(prefix="/public", tags=["public-chat"])

logger = logging.getLogger(__name__)

# faster-whisper's initial_prompt only needs to carry recent context across a pause-segmented
# recording (see features/ai/stt) — capped to the trailing text, both because that's what
# actually helps decoding and to bound how much a client can make one transcription request process.
_MAX_INITIAL_PROMPT_CHARS = 500


@router.get("/{slug}", response_model=PublicProjectOut)
def load_tutor(
    request: Request,
    response: Response,
    project: Project = Depends(get_published_project),
    session: Session = Depends(get_session),
    x_chat_unlock_token: str | None = Header(default=None),
):
    """Load a published project's public info (for the public chat page) and record the visit."""
    visitor_id = get_or_set_visitor_id(request, response)
    log_access(session, project.id, visitor_id)

    unlocked = is_unlocked(project, visitor_id, x_chat_unlock_token)
    if project.password_protected and not unlocked:
        # Nothing persona/prompt-related leaks before the password is entered — just enough to
        # show a non-blank lock screen (title, who set it up).
        user = session.get(User, project.user_id)
        return PublicProjectOut(
            title=project.title,
            teacher_name=user.name if user else "",
            password_protected=True,
            unlocked=False,
            require_visitor_name=project.require_visitor_name,
        )

    user = session.get(User, project.user_id)
    teacher_name = user.name if user else ""

    return PublicProjectOut(
        title=project.title,
        teacher_name=teacher_name,
        start_prompt=project.start_prompt,
        start_audio_url=project.start_audio_url,
        avatar_model_url=project.avatar_model_url,
        avatar_background_url=project.avatar_background_url,
        spoken_language=project.spoken_language,
        tts_enabled=project.tts_enabled,
        stt_enabled=project.stt_enabled,
        browser_stt_model_url=browser_stt_model_url_for(project),
        browser_tts_model_url=browser_tts_model_url_for(project),
        browser_tts_voice=project.tts_voice if browser_tts_model_url_for(project) else None,
        streaming_enabled=project.streaming_enabled,
        chat_layout=project.chat_layout,
        motion_enabled=project.motion_enabled,
        # Combined here like the other flags: without gestures there is nothing to play.
        thinking_motions_enabled=project.thinking_motions_enabled and project.motion_enabled,
        checklist=_public_checklist(project),
        # The checkbox and URL are combined here, before anything goes out to the anonymous page —
        # both "not enabled" and "enabled but URL empty" end up as None.
        survey_before_url=project.survey_before_url if project.survey_before_enabled and project.survey_before_url else None,
        survey_after_url=project.survey_after_url if project.survey_after_enabled and project.survey_after_url else None,
        password_protected=project.password_protected,
        unlocked=unlocked,
        require_visitor_name=project.require_visitor_name,
        save_conversations=project.save_conversations,
        llm_model=project.llm_model,
    )


def _public_checklist(project: Project) -> PublicChecklistOut | None:
    """The plan for the public page: only when students may see it, and without the notes,
    which may hold the answers the avatar is checking for."""
    checklist = checklist_for(project)
    if checklist is None or not project.checklist_visible:
        return None
    return PublicChecklistOut(
        ordered=checklist.ordered,
        items=[PublicChecklistItemOut(id=item.id, title=item.title) for item in checklist.items],
    )


@router.post("/{slug}/unlock", response_model=ChatUnlockOut)
def unlock(
    data: ChatUnlockRequest,
    request: Request,
    response: Response,
    project: Project = Depends(get_published_project),
):
    """Verify a visitor-entered chat password and issue an unlock token for this project+visitor."""
    visitor_id = get_or_set_visitor_id(request, response)
    enforce_chat_unlock_rate_limit(request, visitor_id)
    if not project.password_protected:
        raise HTTPException(status_code=400, detail=ErrorCode.PROJECT_NOT_PASSWORD_PROTECTED)
    if not verify_chat_password(project, data.password):
        raise HTTPException(status_code=401, detail=ErrorCode.CHAT_PASSWORD_INCORRECT)
    return ChatUnlockOut(unlock_token=issue_unlock_token(project, visitor_id))


def _start_turn(
    data: ChatMessageIn,
    request: Request,
    response: Response,
    project: Project,
    x_chat_unlock_token: str | None,
    x_visitor_name: str | None,
) -> ChatTurn:
    """The checks every chat message passes before any LLM work, in this order."""
    # Captured before any LLM/TTS work — the closest we get to "when the visitor actually sent
    # this", used for the saved message's per-message timestamp.
    received_at = datetime.now(timezone.utc)
    visitor_id = get_or_set_visitor_id(request, response)
    # Checked before the rate limit — an unauthenticated caller shouldn't be able to spend a
    # protected project's chat budget just by guessing at the endpoint.
    assert_unlocked(project, visitor_id, x_chat_unlock_token)
    visitor_name = clean_visitor_name(x_visitor_name)
    assert_visitor_name_provided(project, visitor_name)
    enforce_public_chat_rate_limit(request, visitor_id)
    history = [{"role": h.role, "content": h.content} for h in data.history]
    done = frozenset(replay(h.checks for h in data.history))
    return ChatTurn(data.message, history, visitor_id, visitor_name, received_at, done)


def _chat_context(session: Session, project: Project, data: ChatMessageIn) -> ChatContext:
    """The turn's context; 503 without a usable LLM key. The device only takes over speech output
    where the project offers it — a request can't switch off the server's TTS otherwise."""
    context = prepare_chat(session, project)
    if context is None:
        raise HTTPException(status_code=503, detail=ErrorCode.CHAT_UNAVAILABLE)
    if data.speak_on_device and browser_tts_model_url_for(project):
        context = replace(context, speak_on_device=True)
    return context


@router.post("/{slug}/messages", response_model=ChatMessageOut)
def send_message(
    data: ChatMessageIn,
    request: Request,
    response: Response,
    project: Project = Depends(get_published_project),
    session: Session = Depends(get_session),
    x_chat_unlock_token: str | None = Header(default=None),
    x_visitor_name: str | None = Header(default=None),
):
    """Send a visitor's chat message to the project's LLM and return the reply, saving history if enabled."""
    turn = _start_turn(data, request, response, project, x_chat_unlock_token, x_visitor_name)

    # No technical detail (provider/model) is passed to anonymous visitors — that's the project
    # owner's configuration problem, not something visitors are affected by or can fix.
    context = _chat_context(session, project, data)
    # The LLM and TTS calls take seconds, and holding a pooled DB connection for that whole time
    # is what let a class-sized burst of concurrent chats exhaust the connection pool.
    session.close()

    try:
        reply = reply_turn(context, turn)
    except LLMFailed as exc:
        logger.exception("LLM request failed (project_id=%s)", context.project_id)
        raise HTTPException(status_code=503, detail=ErrorCode.CHAT_UNAVAILABLE) from exc
    return ChatMessageOut(
        reply=reply.text,
        audio_base64=reply.audio_base64,
        content_type=reply.content_type,
        llm_ms=reply.llm_ms,
        tts_ms=reply.tts_ms,
        motions=reply.motions,
        checks=[c.to_client() for c in reply.checks],
        spoken=reply.spoken,
    )


@router.post("/{slug}/messages/stream")
def send_message_stream(
    data: ChatMessageIn,
    request: Request,
    response: Response,
    project: Project = Depends(get_published_project),
    session: Session = Depends(get_session),
    x_chat_unlock_token: str | None = Header(default=None),
    x_visitor_name: str | None = Header(default=None),
):
    """Streamed variant of send_message: the LLM reply is split into sentence-sized chunks, each
    synthesized and sent to the client as soon as it's ready (see pipeline.py::stream_turn). Falls
    back to the plain /messages endpoint on the frontend if this fails; see pages/PublicChat/index.tsx.
    """
    turn = _start_turn(data, request, response, project, x_chat_unlock_token, x_visitor_name)

    context = _chat_context(session, project, data)
    # Same reason as in send_message, only more so: a streamed reply runs for the LLM call plus
    # every sentence's TTS. Everything the stream needs was read into `context` up front, and
    # saving opens its own session, so nothing touches `session` after this.
    session.close()

    streaming = StreamingResponse(
        (sse_event(event, payload) for event, payload in stream_turn(context, turn)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    # FastAPI only merges the injected `response`'s headers into responses it builds itself, so
    # the visitor cookie _start_turn may have set there would be dropped — copy it over.
    streaming.raw_headers.extend((name, value) for name, value in response.raw_headers if name == b"set-cookie")
    return streaming


@router.post("/{slug}/transcriptions", response_model=TranscriptionOut)
def transcribe(
    audio: UploadFile,
    request: Request,
    response: Response,
    project: Project = Depends(get_published_project),
    session: Session = Depends(get_session),
    x_chat_unlock_token: str | None = Header(default=None),
    x_visitor_name: str | None = Header(default=None),
    # Text already transcribed earlier in the same recording (see the frontend's pause-triggered
    # segmentation) — optional and unused by a plain single-shot recording, which sends nothing.
    initial_prompt: str | None = Form(default=None),
):
    """Transcribe a visitor's voice message for the public chat.

    Deliberately a plain `def`, not `async def`: transcription is synchronous and CPU-bound
    (see features/ai/stt), and FastAPI runs a sync `def` route in its worker thread pool
    instead of on the event loop. An `async def` here would block every other request in the
    process — page loads, other visitors' chats, SSE streams — for the whole transcription.
    """
    visitor_id = get_or_set_visitor_id(request, response)
    assert_unlocked(project, visitor_id, x_chat_unlock_token)
    assert_visitor_name_provided(project, clean_visitor_name(x_visitor_name))
    enforce_public_transcribe_rate_limit(request, visitor_id)

    if not project.stt_enabled:
        raise HTTPException(status_code=503, detail=ErrorCode.VOICE_INPUT_UNAVAILABLE)
    content = read_audio_upload(audio)
    if initial_prompt:
        initial_prompt = initial_prompt[-_MAX_INITIAL_PROMPT_CHARS:]

    stt_key = resolve_stt_key(session, project)
    stt_start = time.perf_counter()
    try:
        text = transcribe_audio(
            content,
            project.spoken_language,
            initial_prompt,
            api_key_record=stt_key,
            engine=project.stt_server_engine,
        )
    except Exception as exc:
        # Generic message for visitors (no technical detail), consistent with send_message.
        logger.exception("Transcription failed (project_id=%s)", project.id)
        raise HTTPException(status_code=503, detail=ErrorCode.VOICE_INPUT_UNAVAILABLE) from exc
    stt_ms = (time.perf_counter() - stt_start) * 1000
    return TranscriptionOut(text=text, stt_ms=stt_ms)
