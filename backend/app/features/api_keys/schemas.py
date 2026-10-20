"""
API Key Request/Response Shapes

The request/response shapes for app/features/api_keys/router.py, plus the shared validation that checks a
submitted key against the provider registry (app/core/providers.py) before it's created or
updated.

How to use:
    from app.features.api_keys.schemas import ApiKeyCreate
"""

import ipaddress
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator

from app.core.error_codes import ErrorCode
from app.core.providers import KEY_TYPE_LLM, KEY_TYPE_STT, KEY_TYPE_TTS, ProviderSpec, get_provider
from app.core.schema import CamelModel

# Hostnames/IPs no legitimate LLM/TTS server would ever be reachable at, so blocking them can't
# break a real deployment — unlike private IP ranges in general, which providers like Ollama and
# "OpenAI-compatible" (see core/providers.py) legitimately point at (a school's own LAN/GPU box).
_BLOCKED_API_BASE_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    # The deployment's own services (docker/docker-compose.yml, kept in sync by
    # tests/test_api_key_schemas.py): reachable by name from the backend, never a teacher's
    # provider. Loopback stays allowed — Deploy A runs a teacher's Ollama on localhost.
    "backend",
    "web",
    "rag",
    "rag-eval",
    "tts-local",
    "docling",
    "stt-model",
}
# AWS IMDSv2's IPv6 alias lives in fd00::/8 (unique-local), which ipaddress's `is_link_local`
# doesn't cover, so it's blocked by exact match alongside the well-known v4 metadata IP.
_BLOCKED_API_BASE_LITERAL_HOSTS = {"169.254.169.254", "fd00:ec2::254"}


def _validate_api_base(v: str | None) -> str | None:
    """Reject api_base values that would turn this field into an SSRF (server-side request
    forgery) vector against cloud-metadata endpoints and this deployment's own services — the
    request is made by the backend itself (see features/ai/llm, features/ai/tts), whenever a
    project using this key is used or "tested". Domain names aren't resolved here (that happens at request time, and
    DNS can change), so this only catches literal IPs/hostnames, not rebinding.
    """
    if not v:
        return v
    parsed = urlsplit(v)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(ErrorCode.PROVIDER_BASE_URL_NOT_ALLOWED)
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_API_BASE_HOSTNAMES or host in _BLOCKED_API_BASE_LITERAL_HOSTS:
        raise ValueError(ErrorCode.PROVIDER_BASE_URL_NOT_ALLOWED)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return v  # not an IP literal — a domain name, left to resolve normally at request time
    if ip.is_link_local:  # covers 169.254.0.0/16 (includes the metadata IP) and fe80::/10
        raise ValueError(ErrorCode.PROVIDER_BASE_URL_NOT_ALLOWED)
    return v


def _validate_against_registry(
    provider: str, key_type: str, api_base: str | None, model_id: str | None, arcana_id: str | None
) -> ProviderSpec:
    """Shared check used for both creating and editing a key.

    This used to be hardcoded special cases per provider; the requirements (is a key needed? a
    URL? which types?) now come from the registry, and an unknown provider is rejected outright —
    previously `provider` was arbitrary free text.
    """
    spec = get_provider(provider)
    if spec is None:
        raise ValueError(ErrorCode.UNKNOWN_PROVIDER)
    if key_type not in spec.supported_types:
        raise ValueError(ErrorCode.PROVIDER_UNSUPPORTED_KEY_TYPE)
    if spec.api_base_required and not api_base:
        raise ValueError(ErrorCode.PROVIDER_NEEDS_BASE_URL)
    # TTS keys for providers with a fixed, provider-side speech model (spec.tts_model, e.g.
    # OpenAI/Gemini) don't need a model choice — features/ai/tts always uses spec.tts_model for
    # them, never model_id. Same idea for STT keys with spec.stt_model (currently GWDG SAIA).
    model_required = not (
        (key_type == KEY_TYPE_TTS and spec.tts_model) or (key_type == KEY_TYPE_STT and spec.stt_model)
    )
    if model_required and not model_id:
        raise ValueError(ErrorCode.MODEL_REQUIRED)
    if spec.requires_arcana_id and not arcana_id:
        raise ValueError(ErrorCode.PROVIDER_NEEDS_ARCANA_ID)
    return spec


class ApiKeyCreate(CamelModel):
    provider: str
    key_type: str = KEY_TYPE_LLM
    label: str | None = None
    # Empty for providers with key_required == False (e.g. a school-internal Ollama server).
    api_key: str = ""
    api_base: str | None = None
    model_id: str | None = None
    arcana_id: str | None = None

    @field_validator("api_base")
    @classmethod
    def _check_api_base(cls, v: str | None) -> str | None:
        return _validate_api_base(v)

    @model_validator(mode="after")
    def validate_against_registry(self):
        spec = _validate_against_registry(self.provider, self.key_type, self.api_base, self.model_id, self.arcana_id)
        if spec.key_required and not self.api_key:
            raise ValueError(ErrorCode.PROVIDER_NEEDS_API_KEY)
        return self


class ApiKeyUpdate(CamelModel):
    provider: str
    key_type: str = KEY_TYPE_LLM
    label: str | None = None
    # Empty here means "leave unchanged" — the stored plaintext is never retrievable, so the user
    # shouldn't have to retype it just to rename the key or change its model.
    api_key: str = ""
    api_base: str | None = None
    model_id: str | None = None
    arcana_id: str | None = None

    @field_validator("api_base")
    @classmethod
    def _check_api_base(cls, v: str | None) -> str | None:
        return _validate_api_base(v)

    @model_validator(mode="after")
    def validate_against_registry(self):
        _validate_against_registry(self.provider, self.key_type, self.api_base, self.model_id, self.arcana_id)
        return self


class ApiKeyOut(CamelModel):
    id: str
    provider: str
    key_type: str
    label: str | None = None
    masked_key: str
    status: str
    added_at: datetime
    api_base: str | None = None
    model_id: str | None = None
    arcana_id: str | None = None
    # Number of projects using this key as a model source — lets the frontend warn before
    # deleting a key that's still referenced by projects.
    used_by_projects: int = 0


class ApiKeyTestResult(CamelModel):
    status: str
    # Plaintext error cause (truncated). Previously the exception was swallowed entirely, so the
    # UI could only show "Error" without saying why.
    message: str | None = None


class LocalTtsVoiceOut(CamelModel):
    id: str
    label: str
    # "preset": always available; "donation": a donated CC0 voice, only with cloning.
    kind: Literal["preset", "donation"]


class LocalTtsStatusOut(CamelModel):
    # Whether the local-TTS sidecar (see local-tts/) is enabled for this deployment — lets the
    # frontend treat "no TTS key configured" the same forgiving way it already treats local STT,
    # instead of always showing the "add a key" warning (see Step2Technical.tsx).
    available: bool
    # Whether the sidecar answered just now; the fields below are empty when it didn't.
    reachable: bool = False
    # Whether it clones voices (teachers' clips, donated voices) — needs its TTS_HF_TOKEN.
    cloning: bool = False
    languages: list[str] = []
    voices: list[LocalTtsVoiceOut] = []


class BrowserSttStatusOut(CamelModel):
    # Whether this deployment allows browser-side (WebGPU) transcription at all (see
    # Settings.browser_stt_enabled) — gates whether the Configurator even shows its per-project
    # "on-device transcription" checkbox (see Step2Technical.tsx).
    available: bool


class ServerSttStatusOut(CamelModel):
    # The deployment's default local server engine (Settings.stt_engine), which a project without
    # its own stt_server_engine uses — and whether the Parakeet model files are present, so the
    # Configurator only offers Parakeet when it would actually run (see Step2Technical.tsx).
    default_engine: Literal["whisper", "parakeet"]
    parakeet_available: bool


class ProviderModelOut(CamelModel):
    value: str
    label: str


class ProviderSpecOut(CamelModel):
    value: str
    label: str
    key_placeholder: str
    default_api_base: str | None
    api_base_required: bool
    key_required: bool
    supported_types: list[str]
    models: list[ProviderModelOut]
    hint: str | None = None
    # This provider's speech model is fixed (e.g. OpenAI/Gemini) — the frontend hides the model
    # choice for TTS keys in that case, instead of forcing a selection that would be ignored anyway.
    tts_model_fixed: bool = False
    # Same idea for STT keys (currently only GWDG SAIA, which has exactly one Whisper model).
    stt_model_fixed: bool = False
    # Extra required "Arcana ID" field in the key form (currently only GWDG Arcana).
    requires_arcana_id: bool = False
    # Curated models for embedding keys (knowledge bases); empty means free text only.
    embedding_models: list[ProviderModelOut] = []
