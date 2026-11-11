"""
Speech-to-Text (STT) Transcription

Transcribes uploaded audio to text. By default this runs locally inside the backend process with
the Parakeet model (parakeet_local.py). A project can instead configure a "bring your own key"
(BYOK) STT provider (currently GWDG SAIA, see core/providers.py::GWDG_SAIA_PROVIDER); passing
that key's record dispatches to saia.py instead of the local model.

How to use:
    from app.features.ai.stt import transcribe_audio

    text = transcribe_audio(audio_bytes, project.spoken_language, api_key_record=stt_key)
"""

from app.core.providers import GWDG_SAIA_PROVIDER
from app.features.ai.stt.base import STTClient
from app.features.ai.stt.parakeet_local import LocalParakeetClient, SttModelMissing
from app.features.ai.stt.saia import SaiaClient
from app.features.api_keys.models import UserApiKey

__all__ = ["STTClient", "SttModelMissing", "get_stt_client", "transcribe_audio"]


def get_stt_client(api_key_record: UserApiKey | None) -> STTClient:
    """The client for `api_key_record`'s provider, or the local Parakeet model for None (no STT
    key configured — a normal, fully working state, unlike a missing LLM/TTS key)."""
    if api_key_record is not None and api_key_record.provider == GWDG_SAIA_PROVIDER:
        return SaiaClient(api_key_record)
    return LocalParakeetClient()


def transcribe_audio(
    audio_bytes: bytes,
    language: str,
    initial_prompt: str | None = None,
    api_key_record: UserApiKey | None = None,
) -> str:
    """Transcribe speech audio to text, decoded as the given language (project.spoken_language).

    `initial_prompt` carries the text already transcribed earlier in the same recording (see the
    public chat's pause-triggered segmentation, pages/PublicChat/useVoiceInput.ts). A cloud
    provider uses it as decoding context across the seam between two segments; the local model
    ignores it. None (the default) is the original, single-shot behavior.

    `api_key_record` is the project's resolved STT key (features/api_keys/resolve.py::
    resolve_stt_key), if any.

    Raises SttModelMissing if the local model is needed but its files aren't there.
    """
    return get_stt_client(api_key_record).transcribe(audio_bytes, language, initial_prompt)
