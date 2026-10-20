"""
Pronunciation Test Sentence

Backs the test box on the "Pronunciation" page: shows what the TTS engine is given for a sample
sentence with the teacher's word list applied, and optionally lets them hear it with a TTS key,
voice or voice clip of their choice — so they can tune an entry without building a project
around it.

How to use:
    from app.features.pronunciation.preview import preview

    result = preview(session, current_user.id, PronunciationPreviewIn(text="pH = 7", language="de"))
"""

import base64
import logging

from normt2s import Rule
from sqlmodel import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.core.providers import KEY_TYPE_TTS
from app.core.rate_limit import enforce_tts_preview_rate_limit
from app.features.ai.tts import VoiceReference, synthesize_speech
from app.features.ai.tts.normalizer import normalize_for_speech
from app.features.api_keys.crypto import scrub_key_from_text
from app.features.api_keys.resolve import get_owned_key_of_type
from app.features.media.service import get_owned_voice_clip
from app.features.pronunciation.schemas import PronunciationPreviewIn, PronunciationPreviewOut
from app.features.pronunciation.service import rules_for

logger = logging.getLogger(__name__)


class TtsKeyNotFound(DomainError):
    status_code = 404
    detail = ErrorCode.API_KEY_NOT_FOUND


class VoiceClipNotFound(DomainError):
    status_code = 404
    detail = ErrorCode.VOICE_CLIP_NOT_FOUND


class TtsNotConfigured(DomainError):
    status_code = 400
    detail = ErrorCode.TTS_NOT_CONFIGURED


def preview(session: Session, user_id: str, data: PronunciationPreviewIn) -> PronunciationPreviewOut:
    """The spoken form of `data.text` (and, with data.synthesize, its audio)."""
    rules = rules_for(session, user_id, data.language)
    applied: list[Rule] = []
    spoken_text = normalize_for_speech(data.text, data.language, rules, applied)
    result = PronunciationPreviewOut(spoken_text=spoken_text, applied_terms=[rule.term for rule in applied])
    if not data.synthesize:
        return result

    enforce_tts_preview_rate_limit(user_id)
    # Only the teacher's own keys and clips — the same ownership checks a project gets when it
    # references them (see features/api_keys/resolve.py).
    api_key = None
    if data.tts_api_key_id:
        api_key = get_owned_key_of_type(session, user_id, data.tts_api_key_id, KEY_TYPE_TTS)
        if api_key is None:
            raise TtsKeyNotFound()
    elif not settings.local_tts_enabled:
        raise TtsNotConfigured()
    voice_clip = None
    if api_key is None and data.voice_clip_id:
        clip = get_owned_voice_clip(session, user_id, data.voice_clip_id)
        if clip is None:
            raise VoiceClipNotFound()
        voice_clip = VoiceReference(sha256=clip.sha256, path=clip.file_path)

    try:
        audio, content_type = synthesize_speech(
            data.text,
            (data.tts_voice or None) if api_key is not None else None,
            api_key,
            data.language,
            voice_clip=voice_clip,
            local_timeout_seconds=settings.voice_preview_timeout_seconds,
            pronunciation=rules,
        )
    except Exception as exc:
        logger.warning("Pronunciation preview TTS failed (user_id=%s): %s", user_id, type(exc).__name__)
        # The teacher's own key and their own debugging context — the concrete (key-scrubbed)
        # provider message helps them, same as the start-audio generation.
        message = scrub_key_from_text(str(exc), api_key.encrypted_api_key) if api_key else str(exc)
        raise DomainError(
            status_code=502, detail={"code": ErrorCode.PRONUNCIATION_PREVIEW_FAILED, "message": message}
        ) from exc
    result.audio_base64 = base64.b64encode(audio).decode()
    result.content_type = content_type
    return result
