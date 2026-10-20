"""
Text-to-Speech (TTS) Synthesis

Turns text into spoken audio using the API key configured for a project. Most providers go
through litellm (litellm_provider.py); Cartesia and Google Cloud TTS have their own direct HTTP
integrations because litellm doesn't support them. With no key configured, synthesis falls back
to the optional local-TTS sidecar (local.py) — the TTS counterpart to the local Whisper fallback
for STT.

How to use:
    from app.features.ai.tts import synthesize_speech

    audio_bytes, content_type = synthesize_speech(text, project.tts_voice, api_key, project.spoken_language)
    # With a teacher's own voice clip (local TTS only, see local.py):
    audio_bytes, content_type = synthesize_speech(text, None, None, "de", voice_clip=reference)
"""

from normt2s import RuleSet

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.providers import CARTESIA_PROVIDER, GOOGLE_CLOUD_TTS_PROVIDER
from app.features.ai.tts.base import TTSClient, VoiceRequiredError
from app.features.ai.tts.cartesia import CartesiaClient
from app.features.ai.tts.google import GoogleCloudTTSClient
from app.features.ai.tts.litellm_provider import LiteLLMSpeechClient
from app.features.ai.tts.local import LocalTTSClient, VoiceReference
from app.features.ai.tts.normalizer import normalize_for_speech
from app.features.api_keys.models import UserApiKey

__all__ = ["TTSClient", "VoiceReference", "VoiceRequiredError", "get_tts_client", "synthesize_speech"]


def get_tts_client(
    api_key_record: UserApiKey | None,
    voice_clip: VoiceReference | None = None,
    local_timeout_seconds: float | None = None,
) -> TTSClient:
    """The client for the provider of `api_key_record`, or the local-TTS sidecar for None.

    `voice_clip` and `local_timeout_seconds` only apply to the sidecar — a cloud provider speaks
    with its own voices. Raises ValueError(TTS_NOT_CONFIGURED) for None when the sidecar isn't
    enabled either (settings.local_tts_enabled) — callers treat that like any other TTS failure.
    """
    if api_key_record is None:
        if not settings.local_tts_enabled:
            raise ValueError(ErrorCode.TTS_NOT_CONFIGURED)
        return LocalTTSClient(voice_clip, local_timeout_seconds)
    if api_key_record.provider == CARTESIA_PROVIDER:
        return CartesiaClient(api_key_record)
    if api_key_record.provider == GOOGLE_CLOUD_TTS_PROVIDER:
        return GoogleCloudTTSClient(api_key_record)
    return LiteLLMSpeechClient(api_key_record)


def synthesize_speech(
    text: str,
    tts_voice: str | None,
    api_key_record: UserApiKey | None,
    language: str = "de",
    voice_clip: VoiceReference | None = None,
    local_timeout_seconds: float | None = None,
    pronunciation: RuleSet | None = None,
    normalized: bool = False,
) -> tuple[bytes, str]:
    """Generate speech for `text`, dispatching to the right provider integration for the given key.

    `text` is normalized for speech first (see normalizer.py) — numbers, units and math read
    correctly, but this only affects what's spoken, never what's displayed. `pronunciation` is
    the teacher's own word list for `language` (see features/pronunciation/service.py::rules_for).
    `normalized=True` skips that step for text that already is the spoken form (a streamed reply's
    chunks come out of normalizer.py::speech_stream normalized), so it isn't rewritten twice.

    `api_key_record` is None when the caller has TTS enabled but no cloud key configured — same
    convention as features/ai/stt/__init__.py::transcribe_audio's `api_key_record` (see get_tts_client).
    """
    if not normalized:
        text = normalize_for_speech(text, language, pronunciation)
    client = get_tts_client(api_key_record, voice_clip, local_timeout_seconds)
    return client.synthesize(text, tts_voice, language)
