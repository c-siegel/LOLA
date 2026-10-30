"""
Latency Test Routes

Back the dashboard's latency test page (frontend pages/Dashboard/LatencyLab): the project's own
chat pipeline, with the LLM key, TTS path, streaming, and server STT engine switchable per
request, so a teacher can compare configurations on real devices without editing the project.
Every reply comes back as server-sent events with per-module timings. Like the preview chat
(preview_router.py), it runs only for the project's owner and never saves anything — test
messages must not show up as student conversations in analytics or exports.
"""

import dataclasses
import time
from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.config import settings
from app.core.deps import get_owned_project, get_session
from app.core.error_codes import ErrorCode
from app.core.providers import KEY_TYPE_LLM
from app.features.ai.stt import transcribe_audio
from app.features.api_keys.crypto import scrub_key_from_text
from app.features.api_keys.resolve import get_owned_key_of_type, resolve_stt_key
from app.features.chat.audio_upload import read_audio_upload
from app.features.chat.pipeline import ChatContext, ChatTurn, LLMFailed, prepare_chat, reply_turn, stream_turn
from app.features.chat.schemas import LatencyTestMessageIn, LatencyTranscriptionOut
from app.features.chat.streaming import sse_stream
from app.features.media.service import voice_reference_for_project
from app.features.projects.models import Project

router = APIRouter(prefix="/projects", tags=["projects"])


def _test_context(session: Session, project: Project, data: LatencyTestMessageIn) -> ChatContext:
    llm_key = None
    if data.llm_api_key_id:
        llm_key = get_owned_key_of_type(session, project.user_id, data.llm_api_key_id, KEY_TYPE_LLM)
        if llm_key is None:
            raise HTTPException(status_code=404, detail=ErrorCode.API_KEY_NOT_FOUND)
    context = prepare_chat(session, project, llm_key)
    if context is None:
        raise HTTPException(status_code=400, detail=ErrorCode.NO_LLM_MODEL_SELECTED)

    if data.tts_mode == "none":
        context = dataclasses.replace(context, tts_enabled=False, tts_key=None, voice_clip=None)
    elif data.tts_mode == "local":
        if not settings.local_tts_enabled:
            raise HTTPException(status_code=400, detail=ErrorCode.TTS_NOT_CONFIGURED)
        context = dataclasses.replace(
            context, tts_enabled=True, tts_key=None, voice_clip=voice_reference_for_project(session, project)
        )
    return dataclasses.replace(context, save_conversations=False, diagnostics=True)


def _plain_turn_events(context: ChatContext, turn: ChatTurn) -> Iterator[tuple[str, dict]]:
    """The non-streamed reply in the streamed reply's event shape: one chunk with all of it."""
    start = time.perf_counter()
    try:
        reply = reply_turn(context, turn, save=False)
    except LLMFailed as exc:
        message = scrub_key_from_text(str(exc.__cause__), context.llm_key.encrypted_api_key)
        yield "error", {"detail": ErrorCode.LLM_REQUEST_FAILED, "message": message}
        return
    total_ms = (time.perf_counter() - start) * 1000
    yield "chunk", {
        "index": 0,
        "text": reply.text,
        "audioBase64": reply.audio_base64,
        "contentType": reply.content_type,
        "textReadyMs": reply.llm_ms,
        "ttsMs": reply.tts_ms,
        "sentMs": total_ms,
    }
    yield "done", {
        "reply": reply.text,
        "llmMs": reply.llm_ms,
        "llmFirstTokenMs": None,
        "firstChunkMs": total_ms,
        "firstChunkTextReadyMs": reply.llm_ms,
        "ttsMs": reply.tts_ms,
        "retrievalMs": reply.retrieval_ms,
        "usage": reply.usage,
        "route": reply.route,
    }


@router.post("/{project_id}/latency-test/messages")
def latency_test_message(
    data: LatencyTestMessageIn,
    project: Project = Depends(get_owned_project),
    session: Session = Depends(get_session),
):
    """Answer one test message as server-sent events ("chunk"…, then "done" or "error"), with
    the server-side timings of every step — see pipeline.py::stream_turn for the event fields."""
    context = _test_context(session, project, data)
    session.close()
    turn = ChatTurn(
        data.message,
        [{"role": h.role, "content": h.content} for h in data.history],
        force_route=None if data.route == "auto" else data.route,
    )
    events = stream_turn(context, turn) if data.streaming else _plain_turn_events(context, turn)
    return StreamingResponse(
        sse_stream(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{project_id}/latency-test/transcriptions", response_model=LatencyTranscriptionOut)
def latency_test_transcription(
    audio: UploadFile,
    engine: Literal["project", "whisper", "parakeet"] = Form(default="project"),
    project: Project = Depends(get_owned_project),
    session: Session = Depends(get_session),
):
    """Transcribe a test recording on the server and report how long that took.

    "project" transcribes exactly like the public chat would (the project's STT key, else its
    local engine); "whisper"/"parakeet" force that local engine. Plain `def` for the same reason
    as public_router.py::transcribe: CPU-bound work must stay off the event loop.
    """
    content = read_audio_upload(audio)
    stt_key = resolve_stt_key(session, project) if engine == "project" else None
    local_engine = project.stt_server_engine if engine == "project" else engine
    used = stt_key.provider if stt_key else (local_engine or settings.stt_engine)
    start = time.perf_counter()
    try:
        text = transcribe_audio(content, project.spoken_language, api_key_record=stt_key, engine=local_engine)
    except Exception as exc:
        message = scrub_key_from_text(str(exc), stt_key.encrypted_api_key) if stt_key else str(exc)
        raise HTTPException(
            status_code=502, detail={"code": ErrorCode.STT_REQUEST_FAILED, "message": message}
        ) from exc
    return LatencyTranscriptionOut(text=text, stt_ms=(time.perf_counter() - start) * 1000, engine=used)
