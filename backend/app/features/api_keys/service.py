"""
Stored API Keys

The logic behind the API-key routes (features/api_keys/router.py): a user's own provider keys
for the "bring your own key" (BYOK) feature — creating, editing, deleting (and clearing every
project reference to the deleted key), counting which projects use each key, and the "Test"
button's real call against the provider. Keys are encrypted at rest (see crypto.py).
"""

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.error_codes import ErrorCode
from app.core.providers import KEY_TYPE_EMBEDDING, KEY_TYPE_STT, KEY_TYPE_TTS
from app.features.ai.llm import get_llm_client
from app.features.ai.stt import get_stt_client
from app.features.ai.tts import VoiceRequiredError, synthesize_speech
from app.features.api_keys.crypto import mask_key, scrub_key_from_text, store_api_key
from app.features.api_keys.models import UserApiKey
from app.features.api_keys.schemas import ApiKeyCreate, ApiKeyUpdate
from app.features.evaluation.cleanup import detach_judge_key
from app.features.knowledge.models import KnowledgeBase
from app.features.knowledge.service import detach_embedding_key, test_embedding_key
from app.features.projects.models import Project
from app.features.projects.service import sync_llm_model

# Max length of the error message sent back to the frontend — provider errors from litellm can
# contain entire request dumps, which would be unreadable in the UI callout.
_MAX_ERROR_LENGTH = 300


def list_keys_with_usage(session: Session, user_id: str) -> list[tuple[UserApiKey, int]]:
    """The user's stored keys (oldest first), each with how many projects use it."""
    keys = session.exec(select(UserApiKey).where(UserApiKey.user_id == user_id).order_by(UserApiKey.added_at)).all()
    # How often each key is used in projects (as the LLM, TTS or STT source), one query per usage
    # type instead of one per row, then merged into a single total per key.
    usage: dict[str, int] = {}
    for column in (Project.llm_api_key_id, Project.tts_api_key_id, Project.stt_api_key_id):
        counts = session.exec(
            select(column, func.count(Project.id))
            .where(Project.user_id == user_id, column.is_not(None))
            .group_by(column)
        ).all()
        for key_id, count in counts:
            usage[key_id] = usage.get(key_id, 0) + count
    # An embedding key is "used" by the knowledge bases built with it.
    kb_counts = session.exec(
        select(KnowledgeBase.embedding_api_key_id, func.count(KnowledgeBase.id))
        .where(KnowledgeBase.user_id == user_id, KnowledgeBase.embedding_api_key_id.is_not(None))
        .group_by(KnowledgeBase.embedding_api_key_id)
    ).all()
    for key_id, count in kb_counts:
        usage[key_id] = usage.get(key_id, 0) + count
    return [(key, usage.get(key.id, 0)) for key in keys]


def create_key(session: Session, user_id: str, data: ApiKeyCreate) -> UserApiKey:
    """Store a new key."""
    # A plain insert (previously an upsert per provider): multiple keys for the same provider
    # are explicitly allowed, e.g. an LLM and a TTS key at OpenAI.
    key = UserApiKey(
        user_id=user_id,
        provider=data.provider,
        key_type=data.key_type,
        label=(data.label or "").strip() or None,
        encrypted_api_key=store_api_key(data.api_key),
        masked_key=mask_key(data.api_key),
        api_base=data.api_base,
        model_id=data.model_id,
        arcana_id=data.arcana_id,
    )
    session.add(key)
    session.commit()
    session.refresh(key)
    return key


def update_key(session: Session, key: UserApiKey, data: ApiKeyUpdate) -> UserApiKey:
    """Update a stored key; re-syncs any projects that use it as their LLM source."""
    key.provider = data.provider
    key.key_type = data.key_type
    key.label = (data.label or "").strip() or None
    key.api_base = data.api_base
    key.model_id = data.model_id
    key.arcana_id = data.arcana_id
    # An empty key field means "leave unchanged" — the plaintext is never retrievable server-side,
    # so the user shouldn't have to retype it just to rename the key or change its model.
    if data.api_key:
        key.encrypted_api_key = store_api_key(data.api_key)
        key.masked_key = mask_key(data.api_key)
    # Any change to endpoint/model/key resets the entry back to "unverified".
    key.status = "unverified"
    session.add(key)

    # Projects keep a denormalized copy of the litellm model string — a model change on the key
    # must be propagated there too, or the analytics would filter on a model that no longer runs.
    session.flush()
    for project in session.exec(select(Project).where(Project.llm_api_key_id == key.id)).all():
        sync_llm_model(session, project)
        session.add(project)

    session.commit()
    session.refresh(key)
    return key


def delete_key(session: Session, key: UserApiKey) -> None:
    """Delete a stored key; projects using it fall back to "no key configured"."""
    # Projects using this key as their model source lose that choice — they'll show the "no LLM
    # configured yet" hint in the configurator again, instead of pointing at a dead foreign key.
    for project in session.exec(select(Project).where(Project.llm_api_key_id == key.id)).all():
        project.llm_api_key_id = None
        project.llm_model = None
        session.add(project)

    # Same for TTS — no denormalized model string to clear there, just the reference.
    for project in session.exec(select(Project).where(Project.tts_api_key_id == key.id)).all():
        project.tts_api_key_id = None
        session.add(project)

    # Same for STT — falls back to the instance-wide local Whisper engine, not a broken state.
    for project in session.exec(select(Project).where(Project.stt_api_key_id == key.id)).all():
        project.stt_api_key_id = None
        session.add(project)

    # Knowledge bases embedded with this key keep their documents but can't be searched until
    # they're re-created with another model (see features/knowledge/service.py).
    detach_embedding_key(session, key.id)
    # Evaluation runs judged with it stay readable (the judge model is in their snapshot).
    detach_judge_key(session, key.id)

    session.delete(key)
    session.commit()


def run_key_test(session: Session, key: UserApiKey) -> str | None:
    """Try the stored key against its provider and record the outcome in key.status.

    Returns the message to show the user (an error code or a scrubbed provider error), or None.
    """
    message: str | None = None
    try:
        # Test TTS/STT keys with a real synthesis/transcription call, not a chat call — otherwise
        # "Test" would always incorrectly fail for a correctly configured TTS/STT key.
        if key.key_type == KEY_TYPE_TTS:
            synthesize_speech("Test", None, key)
        elif key.key_type == KEY_TYPE_STT:
            get_stt_client(key).test()
        elif key.key_type == KEY_TYPE_EMBEDDING:
            test_embedding_key(key)
        else:
            get_llm_client(key).test()
        key.status = "active"
    except VoiceRequiredError:
        # Some providers (Cartesia; also "openai_compatible" via litellm) require a voice for
        # EVERY speech-synthesis call — but the voice only comes from the project and is unknown
        # during a plain key test. That's not a sign of an invalid key, hence "unverified" and
        # not "error".
        key.status = "unverified"
        message = ErrorCode.TTS_KEY_TEST_NEEDS_VOICE
    except Exception as exc:  # noqa: BLE001 — any provider/network failure counts as a test failure here
        key.status = "error"
        raw_message = str(exc)[:_MAX_ERROR_LENGTH] or exc.__class__.__name__
        message = scrub_key_from_text(raw_message, key.encrypted_api_key)

    session.add(key)
    session.commit()
    return message
