"""
Voice Clip Routes

Lets a teacher keep a private library of voice clips — uploaded files or recordings made in the
browser — that local TTS clones a project's voice from (see Project.tts_voice_clip_id), and hear
a sample sentence in a cloned voice before using it.

What is voice cloning?
The local TTS model (see local-tts/) doesn't have fixed voices: given a few seconds of someone
speaking, it speaks any text in a voice that sounds like that person. That's why every upload
requires an explicit confirmation that the uploader may use this person's voice.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlmodel import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_session
from app.core.error_codes import ErrorCode
from app.core.urls import voice_clip_file_url
from app.features.ai.tts import VoiceReference, synthesize_speech
from app.features.media import service as media
from app.features.media.models import VoiceClip
from app.features.media.schemas import VoiceClipOut, VoicePreviewIn
from app.features.media.voice_audio import normalize_voice_clip
from app.features.pronunciation.service import rules_for
from app.features.users.models import User
from app.storage.files import immutable_file_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-clips", tags=["voice-library"])

# Generous for a ≤30 s clip even as uncompressed 48 kHz stereo WAV (~11 MB is the worst case).
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024
_MAX_NAME_LENGTH = 100


def _to_out(clip: VoiceClip) -> VoiceClipOut:
    return VoiceClipOut(
        id=clip.id,
        name=clip.name,
        file_url=voice_clip_file_url(clip.id),
        duration_seconds=clip.duration_seconds,
        consent_confirmed_at=clip.consent_confirmed_at,
        created_at=clip.created_at,
    )


def _owned_or_404(session: Session, user: User, clip_id: str) -> VoiceClip:
    # 404 rather than 403 for someone else's clip, so clip IDs can't be probed (IDOR defense).
    clip = media.get_owned_voice_clip(session, user.id, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail=ErrorCode.VOICE_CLIP_NOT_FOUND)
    return clip


@router.get("", response_model=list[VoiceClipOut])
def list_voice_clips(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """List the current user's voice clips."""
    return [_to_out(c) for c in media.list_voice_clips(session, current_user.id)]


@router.post("", response_model=VoiceClipOut)
def upload_voice_clip(
    file: UploadFile,
    name: str = Form(...),
    consent: bool = Form(False),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Add a voice clip (an uploaded file or a browser recording); it's stored as normalized WAV.

    A plain `def`: decoding and resampling the audio is CPU work, which FastAPI then runs in its
    thread pool instead of on the event loop.
    """
    if not consent:
        raise HTTPException(status_code=400, detail=ErrorCode.VOICE_CLIP_CONSENT_REQUIRED)
    name = name.strip()[:_MAX_NAME_LENGTH]
    if not name:
        raise HTTPException(status_code=400, detail=ErrorCode.VOICE_CLIP_NAME_REQUIRED)
    content = file.file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=ErrorCode.VOICE_CLIP_TOO_LARGE)
    wav, duration = normalize_voice_clip(content)
    return _to_out(media.create_voice_clip(session, current_user.id, name, wav, duration))


@router.get("/{clip_id}/file")
def get_voice_clip_file(
    clip_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    """Serve a clip's WAV file to its owner (never publicly — students only hear the cloned voice)."""
    clip = _owned_or_404(session, current_user, clip_id)
    return immutable_file_response(clip.file_path, "audio/wav", filename=f"{clip.name}.wav")


@router.post("/{clip_id}/preview")
def preview_voice_clip(
    clip_id: str,
    body: VoicePreviewIn,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Speak `body.text` in the clip's cloned voice (WAV), via the local-TTS sidecar."""
    clip = _owned_or_404(session, current_user, clip_id)
    if not settings.local_tts_enabled:
        raise HTTPException(status_code=400, detail=ErrorCode.TTS_NOT_CONFIGURED)
    try:
        audio, content_type = synthesize_speech(
            body.text,
            None,
            None,
            body.language,
            voice_clip=VoiceReference(sha256=clip.sha256, path=clip.file_path),
            local_timeout_seconds=settings.voice_preview_timeout_seconds,
            # The teacher should hear the clip exactly as their students would, word list included.
            pronunciation=rules_for(session, current_user.id, body.language),
        )
    except Exception as exc:
        logger.exception("Voice preview failed (clip_id=%s)", clip.id)
        raise HTTPException(status_code=502, detail=ErrorCode.VOICE_PREVIEW_FAILED) from exc
    return Response(content=audio, media_type=content_type)


@router.delete("/{clip_id}", status_code=204)
def delete_voice_clip(
    clip_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    """Delete a clip; projects that used it go back to the default voice."""
    media.delete_voice_clip(session, _owned_or_404(session, current_user, clip_id))
