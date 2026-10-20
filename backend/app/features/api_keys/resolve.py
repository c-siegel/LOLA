"""
Resolving a Project's API Keys

Looks up which of a user's stored API keys a project should actually use for its LLM/TTS calls,
and works out the endpoint override (if any) to pass to litellm. Also decides whether a project
transcribes on the visitor's device — the STT counterpart to a resolved key, just picked from
Settings instead of the database. Kept central so the chat pipeline
(app/features/chat/pipeline.py), the transcribe routes, and start-audio generation never
duplicate this logic.

How to use:
    from app.features.api_keys.resolve import resolve_llm_key

    api_key = resolve_llm_key(session, project)
"""

from sqlmodel import Session, select

from app.core.config import settings
from app.core.ownership import find_owned
from app.core.providers import KEY_TYPE_LLM, KEY_TYPE_STT, KEY_TYPE_TTS, get_provider
from app.core.urls import site_url
from app.features.api_keys.models import UserApiKey
from app.features.projects.models import Project


def get_user_api_key(
    session: Session, user_id: str, provider: str, key_type: str | None = None
) -> UserApiKey | None:
    """Find a user's key for `provider` (optionally of a specific type); the oldest match wins."""
    # Since the unique constraint was dropped, a user can have several keys per provider — the
    # oldest one wins deterministically, so the result doesn't change between calls.
    query = select(UserApiKey).where(UserApiKey.user_id == user_id).where(UserApiKey.provider == provider)
    if key_type is not None:
        query = query.where(UserApiKey.key_type == key_type)
    return session.exec(query.order_by(UserApiKey.added_at)).first()


def get_owned_key_of_type(session: Session, user_id: str, key_id: str, key_type: str) -> UserApiKey | None:
    """Load a key, but only if it belongs to the user AND is of the expected type.

    Without the key_type check, an LLM key could e.g. be entered as tts_api_key_id (or the other
    way round) — both fields point at the same table, and the foreign-key constraint alone
    doesn't distinguish what the key is meant for.
    """
    key = find_owned(session, UserApiKey, key_id, user_id)
    if key is not None and key.key_type == key_type:
        return key
    return None


def provider_from_model(llm_model: str) -> str:
    """Extract the provider name from a litellm model string like "anthropic/claude-...''."""
    # litellm model strings have the shape "<provider>/<model>" (e.g. "anthropic/claude-sonnet-4-5").
    # Ollama has two litellm prefixes for the same server — "ollama/" and the chat-recommended
    # "ollama_chat/" — both should match the same stored "ollama" key, so a trailing "_chat" on
    # the provider part is ignored.
    # This is now only a fallback for projects created before Project.llm_api_key_id existed; the
    # normal path is resolve_llm_key().
    prefix = llm_model.split("/")[0] if "/" in llm_model else llm_model
    return prefix.removesuffix("_chat")


def resolve_llm_key(session: Session, project: Project) -> UserApiKey | None:
    """Find the LLM key configured for this project."""
    if project.llm_api_key_id:
        # If the key now belongs to someone else, was deleted, or was retyped (e.g. to "tts"),
        # the project counts as unconfigured — a clean failure beats using someone else's or a
        # mistyped key.
        key = get_owned_key_of_type(session, project.user_id, project.llm_api_key_id, KEY_TYPE_LLM)
        if key is not None:
            return key
    if project.llm_model:
        return get_user_api_key(session, project.user_id, provider_from_model(project.llm_model), KEY_TYPE_LLM)
    return None


def resolve_tts_key(session: Session, project: Project) -> UserApiKey | None:
    """Find the TTS key configured for this project (the TTS counterpart to resolve_llm_key).

    No legacy string fallback needed — the "tts" key type never existed without this field.
    """
    if project.tts_api_key_id:
        return get_owned_key_of_type(session, project.user_id, project.tts_api_key_id, KEY_TYPE_TTS)
    return None


def resolve_stt_key(session: Session, project: Project) -> UserApiKey | None:
    """Find the STT key configured for this project (the STT counterpart to resolve_tts_key).

    None (no key configured, or a stale/foreign one) means the caller should fall back to the
    instance-wide local Whisper engine (see features/ai/stt) — unlike LLM/TTS, having no
    STT key is a normal, fully working state, not a missing configuration.
    """
    if project.stt_api_key_id:
        return get_owned_key_of_type(session, project.user_id, project.stt_api_key_id, KEY_TYPE_STT)
    return None


def browser_stt_model_url_for(project: Project) -> str | None:
    """Base URL of the on-device (WebGPU) transcription model the visitor's browser should load,
    or None if this project transcribes on the server only.

    None if the deployment turned it off (Settings.browser_stt_enabled), the project opted out
    (project.stt_browser_enabled, on by default), or voice input is off for the project. The
    model is multilingual, so unlike server Whisper it doesn't depend on spoken_language.
    """
    if not (settings.browser_stt_enabled and project.stt_enabled and project.stt_browser_enabled):
        return None
    return site_url(settings.browser_stt_model_url)


def browser_tts_model_url_for(project: Project) -> str | None:
    """Base URL of the on-device speech output model the visitor's browser should load, or None
    if this project speaks through the server only.

    None if the deployment doesn't offer it (Settings.browser_tts_enabled), the project hasn't opted
    in (project.tts_browser_enabled), speech output is off, or the project speaks another language
    than the model (Settings.browser_tts_language).
    """
    if not (settings.browser_tts_enabled and project.tts_enabled and project.tts_browser_enabled):
        return None
    if project.spoken_language != settings.browser_tts_language:
        return None
    return site_url(settings.browser_tts_model_url)


def effective_api_base(key: UserApiKey) -> str | None:
    """The endpoint address to pass to litellm, or None to use litellm's own default.

    The address of the fixed SaaS providers is only shown in the form as a pre-filled default.
    Passing that unchanged default through to litellm would change the previously working call
    behavior (litellm already knows the official endpoints, including path quirks). Only a
    deliberately different address (a proxy, a custom endpoint, an Ollama server) is passed on.
    """
    if not key.api_base:
        return None
    spec = get_provider(key.provider)
    if spec is not None and spec.default_api_base and key.api_base.rstrip("/") == spec.default_api_base.rstrip("/"):
        return None
    return key.api_base
