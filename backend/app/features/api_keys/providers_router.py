"""
Provider Registry Route

Exposes the provider registry (app/core/providers.py) so the frontend can build its API-key form
— provider dropdown, endpoint defaults, curated models — without duplicating that data. Also
reports the deployment-wide speech options that need no key at all: the local-TTS sidecar,
browser-side (WebGPU) transcription, and the local server STT engines.
"""

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.providers import KEY_TYPE_EMBEDDING, PROVIDERS
from app.features.ai.stt import parakeet_local
from app.features.ai.tts.local import sidecar_voices
from app.features.api_keys.schemas import (
    BrowserSttStatusOut,
    BrowserTtsStatusOut,
    LocalTtsStatusOut,
    LocalTtsVoiceOut,
    ProviderModelOut,
    ProviderSpecOut,
    ServerSttStatusOut,
)
from app.features.users.models import User

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=list[ProviderSpecOut])
def list_providers(_: User = Depends(get_current_user)):
    """List all supported providers and their config, for building the API-key form."""
    # Providers, endpoint defaults, and curated models all come from a single registry
    # (app/core/providers.py), so the frontend and backend can never drift apart.
    return [
        ProviderSpecOut(
            value=spec.value,
            label=spec.label,
            key_placeholder=spec.key_placeholder,
            default_api_base=spec.default_api_base,
            api_base_required=spec.api_base_required,
            key_required=spec.key_required,
            # Embedding keys only exist for knowledge bases — hidden where that feature is off.
            supported_types=[t for t in spec.supported_types if t != KEY_TYPE_EMBEDDING or settings.rag_enabled],
            models=[ProviderModelOut(value=value, label=label) for value, label in spec.models],
            embedding_models=[ProviderModelOut(value=value, label=label) for value, label in spec.embedding_models],
            hint=spec.hint,
            tts_model_fixed=spec.tts_model is not None,
            stt_model_fixed=spec.stt_model is not None,
            requires_arcana_id=spec.requires_arcana_id,
        )
        for spec in PROVIDERS
    ]


@router.get("/local-tts-status", response_model=LocalTtsStatusOut)
def local_tts_status(_: User = Depends(get_current_user)):
    """Whether the local-TTS sidecar is enabled for this deployment (see Settings.local_tts_enabled),
    and if it answers, what it can speak: languages, voice cloning and its voices."""
    sidecar = sidecar_voices()
    if sidecar is None:
        return LocalTtsStatusOut(available=settings.local_tts_enabled)
    return LocalTtsStatusOut(
        available=True,
        reachable=True,
        cloning=sidecar.cloning,
        languages=sidecar.languages,
        voices=[
            LocalTtsVoiceOut(id=voice["id"], label=voice.get("label") or voice["id"], kind=voice["kind"])
            for voice in sidecar.voices
            # A newer sidecar may know kinds this backend doesn't; leave those out.
            if voice.get("kind") in ("preset", "donation") and voice.get("id")
        ],
    )


@router.get("/browser-stt-status", response_model=BrowserSttStatusOut)
def browser_stt_status(_: User = Depends(get_current_user)):
    """Whether browser-side (WebGPU) transcription is enabled for this deployment (see
    Settings.browser_stt_enabled)."""
    return BrowserSttStatusOut(available=settings.browser_stt_enabled)


@router.get("/browser-tts-status", response_model=BrowserTtsStatusOut)
def browser_tts_status(_: User = Depends(get_current_user)):
    """Whether this deployment offers on-device speech output (see Settings.browser_tts_enabled),
    and the one language its model speaks."""
    return BrowserTtsStatusOut(available=settings.browser_tts_enabled, language=settings.browser_tts_language)


@router.get("/server-stt-status", response_model=ServerSttStatusOut)
def server_stt_status(_: User = Depends(get_current_user)):
    """The deployment's default local server STT engine (Settings.stt_engine) and whether a
    project may pick Parakeet instead (its model files are present)."""
    return ServerSttStatusOut(
        default_engine=settings.stt_engine,
        parakeet_available=parakeet_local.model_available(),
    )
