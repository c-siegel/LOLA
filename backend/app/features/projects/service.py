"""
Project CRUD

The logic behind the project routes (features/projects/router.py): creating, listing, updating
and deleting a user's projects, checking that a project only references the user's own API keys
and library assets, and deriving the denormalized litellm model string from the chosen LLM key.

How to use:
    from app.features.projects.service import list_projects, update_project

    projects = list_projects(session, user_id)
"""

import json

from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.core.ownership import find_owned
from app.core.providers import KEY_TYPE_LLM, KEY_TYPE_STT, KEY_TYPE_TTS, build_model_string
from app.core.security import hash_password
from app.features.api_keys.models import UserApiKey
from app.features.api_keys.resolve import get_owned_key_of_type
from app.features.chat.models import Conversation, ProjectAccess
from app.features.evaluation import cleanup as evaluation_cleanup
from app.features.knowledge.models import KnowledgeBase
from app.features.media.models import AvatarModel, BackgroundImage
from app.features.projects.models import Project
from app.features.projects.schemas import ProjectUpdate
from app.storage.files import unlink_quietly

# Distinguishes "chat_password wasn't in the request at all" (no change) from "it was sent as
# null" (clear/disable) in create_project/apply_update below — both look like a missing key otherwise.
_NO_CHAT_PASSWORD_SENT = object()


class UnknownApiKey(DomainError):
    status_code = 400
    detail = ErrorCode.UNKNOWN_API_KEY


class UnknownAvatar(DomainError):
    status_code = 400
    detail = ErrorCode.AVATAR_NOT_FOUND


class UnknownBackground(DomainError):
    status_code = 400
    detail = ErrorCode.BACKGROUND_NOT_FOUND


class UnknownKnowledgeBase(DomainError):
    status_code = 400
    detail = ErrorCode.KNOWLEDGE_BASE_NOT_FOUND


def list_projects(session: Session, user_id: str) -> list[Project]:
    """List all of a user's projects."""
    return list(session.exec(select(Project).where(Project.user_id == user_id)))


def sync_llm_model(session: Session, project: Project) -> None:
    """Derive llm_model from the project's referenced API key.

    The project stores its model choice as a key reference; the litellm model string is
    additionally kept denormalized because analytics (features/analytics/service.py) and the project
    cards can then avoid a join. Done centrally here so the two never drift apart.
    """
    key = find_owned(session, UserApiKey, project.llm_api_key_id, project.user_id) if project.llm_api_key_id else None
    if key is None or not key.model_id:
        project.llm_model = None
        return
    project.llm_model = build_model_string(key.provider, key.model_id)


# Fields where an explicitly sent null is a deliberate clear (e.g. deselecting a model or
# removing an avatar). For every other field, null is ignored: ProjectUpdate declares every
# field as optional, but a null on title/temperature/... would be a malformed request and would
# fail the DB's NOT NULL constraint anyway.
_CLEARABLE_FIELDS = {
    "motion_prompt",
    "llm_api_key_id",
    "avatar_model_id",
    "builtin_avatar",
    "avatar_background_id",
    "grade_level",
    "tts_voice",
    "tts_voice_clip_id",
    "tts_api_key_id",
    "stt_api_key_id",
    "stt_server_engine",
}

# Changing any of these makes a previously generated start-prompt audio file (see
# features/projects/start_audio.py) no longer match what it should say/sound like — see update_project below.
_START_AUDIO_INVALIDATING_FIELDS = {"start_prompt", "spoken_language", "tts_voice", "tts_voice_clip_id", "tts_api_key_id"}


def delete_project(session: Session, project: Project) -> None:
    """Permanently delete a project together with its saved conversations, access logs, and its
    generated start-prompt audio file.

    The dependent rows have to go explicitly: SQLite runs with foreign-key enforcement off (see
    features/users/account.py), so deleting only the project row would silently orphan every
    student conversation and page view belonging to it. avatar_model_id/avatar_background_id
    aren't touched here — unlike start_audio_path, those point at reusable library assets
    (AvatarModel/BackgroundImage) other projects may still reference, so only their own
    library-delete endpoints (with their own reference checks) may remove those files.
    """
    unlink_quietly(project.start_audio_path)
    session.execute(delete(Conversation).where(Conversation.project_id == project.id))
    session.execute(delete(ProjectAccess).where(ProjectAccess.project_id == project.id))
    evaluation_cleanup.delete_for_project(session, project.id)
    session.delete(project)
    session.commit()


def set_or_clear_chat_password(project: Project, password: str | None) -> None:
    """Set/change (non-empty string) or remove (None) the project's public chat password."""
    project.chat_password_hash = hash_password(password) if password else None


def update_project(session: Session, project: Project, data: dict) -> Project:
    """Apply a partial update to a project (only the fields present in `data`)."""
    # A cached start-prompt audio file is only valid for the exact text/voice/key it was
    # generated with — once any of those changes, the file on disk would say the wrong thing (or
    # in the wrong voice), so drop it and let the "generated?" status in the Configurator go back
    # to "not generated" until the educator regenerates it.
    if project.start_audio_path and any(
        field in data and data[field] != getattr(project, field) for field in _START_AUDIO_INVALIDATING_FIELDS
    ):
        unlink_quietly(project.start_audio_path)
        project.start_audio_path = None
    if "knowledge_base_ids" in data:
        ids = data.pop("knowledge_base_ids")
        if ids is not None:
            # dict.fromkeys: de-duplicated, order kept.
            project.knowledge_base_ids_json = json.dumps(list(dict.fromkeys(ids)))
    if "checklist_items" in data:
        items = data.pop("checklist_items")
        if items is not None:
            project.checklist_json = json.dumps(items)
    for field, value in data.items():
        if value is not None or field in _CLEARABLE_FIELDS:
            setattr(project, field, value)
    if "llm_api_key_id" in data:
        sync_llm_model(session, project)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _require_owned_key_of_type(session: Session, user_id: str, key_id: str, key_type: str) -> None:
    """Raise UnknownApiKey (HTTP 400) unless `key_id` is one of `user_id`'s own API keys of the given type."""
    # Shared check for llm_api_key_id/tts_api_key_id/stt_api_key_id: the key must exist, belong
    # to the calling user, AND be of the matching type — otherwise a TTS key could e.g. be entered
    # as llm_api_key_id (all three fields point at the same table).
    if get_owned_key_of_type(session, user_id, key_id, key_type) is None:
        raise UnknownApiKey()


def _check_references(session: Session, user_id: str, data: ProjectUpdate) -> None:
    # A project may only point at one of the user's own keys — otherwise a user could enter
    # someone else's key ID (it could never actually be used, see resolve_llm_key, but the
    # reference wouldn't belong in the DB either way).
    if data.llm_api_key_id:
        _require_owned_key_of_type(session, user_id, data.llm_api_key_id, KEY_TYPE_LLM)
    if data.tts_api_key_id:
        _require_owned_key_of_type(session, user_id, data.tts_api_key_id, KEY_TYPE_TTS)
    if data.stt_api_key_id:
        _require_owned_key_of_type(session, user_id, data.stt_api_key_id, KEY_TYPE_STT)
    # Same for library assets — a foreign avatar/background would also be served to the
    # project's public chat visitors (see media/service.py::is_used_by_published_project).
    if data.avatar_model_id and find_owned(session, AvatarModel, data.avatar_model_id, user_id) is None:
        raise UnknownAvatar()
    if data.avatar_background_id and find_owned(session, BackgroundImage, data.avatar_background_id, user_id) is None:
        raise UnknownBackground()
    for kb_id in data.knowledge_base_ids or []:
        if find_owned(session, KnowledgeBase, kb_id, user_id) is None:
            raise UnknownKnowledgeBase()


def create_project(session: Session, user_id: str, data: ProjectUpdate) -> Project:
    """Create a new project for `user_id` from the fields actually sent."""
    # Uses ProjectUpdate instead of a separate ProjectCreate schema: every field is optional
    # anyway and the DB model has a default for everything except title/user_id (see
    # features/projects/models.py) — the frontend always sends a title on creation ("+ New project") anyway.
    _check_references(session, user_id, data)
    create_data = data.model_dump(exclude_unset=True)
    chat_password = create_data.pop("chat_password", _NO_CHAT_PASSWORD_SENT)
    knowledge_base_ids = create_data.pop("knowledge_base_ids", None)
    checklist_items = create_data.pop("checklist_items", None)
    project = Project(user_id=user_id, **create_data)
    if knowledge_base_ids:
        project.knowledge_base_ids_json = json.dumps(list(dict.fromkeys(knowledge_base_ids)))
    if checklist_items:
        project.checklist_json = json.dumps(checklist_items)
    if chat_password is not _NO_CHAT_PASSWORD_SENT:
        set_or_clear_chat_password(project, chat_password)
    sync_llm_model(session, project)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def apply_update(session: Session, project: Project, data: ProjectUpdate) -> Project:
    """Validate and apply a ProjectUpdate (only the fields actually sent), including the chat password."""
    _check_references(session, project.user_id, data)
    update_data = data.model_dump(exclude_unset=True)
    if "motion_prompt" in update_data and not (update_data["motion_prompt"] or "").strip():
        update_data["motion_prompt"] = None
    chat_password = update_data.pop("chat_password", _NO_CHAT_PASSWORD_SENT)
    if chat_password is not _NO_CHAT_PASSWORD_SENT:
        set_or_clear_chat_password(project, chat_password)
    return update_project(session, project, update_data)
