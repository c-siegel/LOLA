"""
Project Request/Response Shapes

Project CRUD, the preview chat, and the portable export/import subset of a project
(ProjectExportData) — everything an educator would want to carry from one project to another,
or share with a colleague. The export is deliberately narrower than the full Project table: API
key references, the publish state/share link, the chat password hash, and the cached
start-prompt audio path are all either secret, instance-specific, or derived, so none of them
round-trip.
"""

import re
import secrets
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.error_codes import ErrorCode
from app.core.schema import CamelModel
from app.features.auth.schemas import MAX_PASSWORD_BYTES
from app.features.chat.schemas import CHECKLIST_ID_PATTERN, ChatHistoryEntry, ChecklistCheck

# A shared classroom PIN, not an account password — short and memorable is fine, unlike the
# stricter 10-char+digit rule enforced on User passwords (features/auth/schemas.py).
MIN_CHAT_PASSWORD_LENGTH = 4

# The ranges OpenAI-compatible providers accept for the two sampling parameters. Checked here so
# an out-of-range value is a clean 422 on save, instead of a provider error the educator only
# discovers later in a live chat.
MAX_TEMPERATURE = 2.0
MAX_TOP_P = 1.0

# The local server STT engines a project may pick (see features/ai/stt/__init__.py::get_stt_client) —
# None instead follows the deployment's Settings.stt_engine.
SttServerEngine = Literal["whisper", "parakeet"]

# What the public chat page shows, see features/projects/models.py::Project.chat_layout.
# "avatar_only" needs voice in and out — the public page falls back to "avatar_chat" while TTS or
# STT is off (see frontend pages/PublicChat/index.tsx).
ChatLayout = Literal["avatar_chat", "avatar_chat_collapsed", "avatar_only", "chat_only"]

# How a project uses its knowledge bases (see features/knowledge/prompt.py): not at all, as
# supplementary material, or as the only source it may answer from.
KnowledgeMode = Literal["off", "supplement", "strict"]
MAX_KNOWLEDGE_TOP_K = 8
MAX_KNOWLEDGE_BASES_PER_PROJECT = 10

# A bundled default avatar is addressed by its file name in frontend/public/avatars/ (e.g.
# "julia" -> /avatars/julia.glb) — restricted to a plain slug so it can never point anywhere else.
_BUILTIN_AVATAR_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def _check_builtin_avatar(value: str | None) -> str | None:
    if value is not None and not _BUILTIN_AVATAR_RE.match(value):
        raise ValueError(ErrorCode.INVALID_BUILTIN_AVATAR)
    return value


# Generous: the default is ~900 characters; this only stops absurd inputs.
MAX_MOTION_PROMPT_LENGTH = 4000

# A conversation plan (see features/chat/checklist.py) is a handful of steps; the caps keep the
# part of the system prompt it adds on every turn bounded.
MAX_CHECKLIST_ITEMS = 20
MAX_CHECKLIST_TITLE_LENGTH = 120
MAX_CHECKLIST_NOTE_LENGTH = 1000


class ChecklistItem(CamelModel):
    """One step of a project's conversation plan."""

    # None on a step the editor just added: the server assigns one (see _normalize_checklist).
    id: str | None = Field(default=None, pattern=CHECKLIST_ID_PATTERN)
    # Shown to students (when the plan is visible) and to the model.
    title: str = Field(max_length=MAX_CHECKLIST_TITLE_LENGTH)
    # Only the model sees it, e.g. the expected answers — never sent to the public page.
    note: str | None = Field(default=None, max_length=MAX_CHECKLIST_NOTE_LENGTH)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(ErrorCode.CHECKLIST_INVALID)
        return value.strip()

    @field_validator("note")
    @classmethod
    def blank_note_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


def _normalize_checklist(items: list[ChecklistItem] | None) -> list[ChecklistItem] | None:
    """Give new steps an ID and reject duplicate ones — two steps sharing an ID would tick
    each other off."""
    if items is None:
        return None
    for item in items:
        if item.id is None:
            item.id = secrets.token_hex(4)
    if len({item.id for item in items}) != len(items):
        raise ValueError(ErrorCode.CHECKLIST_INVALID)
    return items


class ProjectOut(CamelModel):
    id: str
    title: str
    description: str | None
    status: str
    llm_api_key_id: str | None
    # Derived from the referenced key — kept in the response so project cards and the
    # analytics model filter don't need an extra request.
    llm_model: str | None
    preprompt: str | None
    start_prompt: str | None
    avatar_model_id: str | None
    builtin_avatar: str | None
    avatar_background_id: str | None
    # Ready-to-load URLs derived from the three fields above (read-only).
    avatar_model_url: str | None
    avatar_background_url: str | None
    grade_level: str | None
    temperature: float
    top_p: float
    published: bool
    share_slug: str | None
    save_conversations: bool
    survey_before_url: str | None
    survey_before_enabled: bool
    survey_after_url: str | None
    survey_after_enabled: bool
    tts_enabled: bool
    tts_api_key_id: str | None
    tts_voice: str | None
    tts_voice_clip_id: str | None
    spoken_language: str
    stt_api_key_id: str | None
    stt_server_engine: SttServerEngine | None
    stt_enabled: bool
    stt_browser_enabled: bool
    tts_browser_enabled: bool
    streaming_enabled: bool
    chat_layout: ChatLayout
    motion_enabled: bool
    thinking_motions_enabled: bool
    motion_prompt: str | None
    password_protected: bool
    require_visitor_name: bool
    # URL of the once-generated start_prompt audio, or None if it hasn't been generated (yet) —
    # see features/projects/start_audio.py. Read-only: generated via its own endpoint, never
    # written directly through ProjectUpdate.
    start_audio_url: str | None
    knowledge_mode: KnowledgeMode
    knowledge_top_k: int
    knowledge_base_ids: list[str]
    checklist_enabled: bool
    checklist_visible: bool
    checklist_ordered: bool
    checklist_items: list[ChecklistItem]
    created_at: datetime


class ProjectUpdate(CamelModel):
    title: str | None = None
    description: str | None = None
    # The model choice goes through the key reference; llm_model is derived from it server-side
    # and is therefore deliberately not writable.
    llm_api_key_id: str | None = None
    preprompt: str | None = None
    start_prompt: str | None = None
    # One of the user's own library avatars, or a bundled default avatar by name (e.g. "julia").
    avatar_model_id: str | None = None
    builtin_avatar: str | None = None
    avatar_background_id: str | None = None
    grade_level: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    save_conversations: bool | None = None
    survey_before_url: str | None = None
    survey_before_enabled: bool | None = None
    survey_after_url: str | None = None
    survey_after_enabled: bool | None = None
    tts_enabled: bool | None = None
    tts_api_key_id: str | None = None
    tts_voice: str | None = None
    tts_voice_clip_id: str | None = None
    spoken_language: str | None = None
    stt_api_key_id: str | None = None
    stt_server_engine: SttServerEngine | None = None
    stt_enabled: bool | None = None
    stt_browser_enabled: bool | None = None
    tts_browser_enabled: bool | None = None
    streaming_enabled: bool | None = None
    chat_layout: ChatLayout | None = None
    motion_enabled: bool | None = None
    thinking_motions_enabled: bool | None = None
    # Blank clears it, i.e. back to the built-in default (see service.py::apply_update).
    motion_prompt: str | None = Field(default=None, max_length=MAX_MOTION_PROMPT_LENGTH)
    require_visitor_name: bool | None = None
    # None = no change (field omitted); "" or explicit null clears/disables the password; a
    # non-empty string sets/changes it — handled separately in features/projects/service.py, never
    # written straight to the DB (see features/projects/service.py::set_or_clear_chat_password).
    chat_password: str | None = None
    knowledge_mode: KnowledgeMode | None = None
    knowledge_top_k: int | None = Field(default=None, ge=1, le=MAX_KNOWLEDGE_TOP_K)
    # Replaces the whole list; only the owner's own knowledge bases are accepted (checked in
    # features/projects/service.py).
    knowledge_base_ids: list[str] | None = Field(default=None, max_length=MAX_KNOWLEDGE_BASES_PER_PROJECT)
    checklist_enabled: bool | None = None
    checklist_visible: bool | None = None
    checklist_ordered: bool | None = None
    # Replaces the whole plan (stored as Project.checklist_json, see service.py::update_project).
    checklist_items: list[ChecklistItem] | None = Field(default=None, max_length=MAX_CHECKLIST_ITEMS)

    @field_validator("builtin_avatar")
    @classmethod
    def validate_builtin_avatar(cls, value: str | None) -> str | None:
        return _check_builtin_avatar(value)

    @field_validator("checklist_items")
    @classmethod
    def validate_checklist_items(cls, value: list[ChecklistItem] | None) -> list[ChecklistItem] | None:
        return _normalize_checklist(value)

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= MAX_TEMPERATURE:
            raise ValueError(ErrorCode.TEMPERATURE_OUT_OF_RANGE)
        return value

    @field_validator("top_p")
    @classmethod
    def validate_top_p(cls, value: float | None) -> float | None:
        if value is not None and not 0.0 <= value <= MAX_TOP_P:
            raise ValueError(ErrorCode.TOP_P_OUT_OF_RANGE)
        return value

    @field_validator("chat_password")
    @classmethod
    def validate_chat_password(cls, value: str | None) -> str | None:
        if not value:
            return value
        if len(value) < MIN_CHAT_PASSWORD_LENGTH:
            raise ValueError(ErrorCode.CHAT_PASSWORD_TOO_SHORT)
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(ErrorCode.CHAT_PASSWORD_TOO_LONG)
        return value


class SystemPromptOut(CamelModel):
    """The system prompt a chat turn really sends, in its parts (see chat/motion.py)."""

    preprompt: str
    # Always the effective text: the teacher's own, or the built-in default.
    motion_prompt: str
    default_motion_prompt: str
    motion_customized: bool
    # False when the project's layout or settings switch gestures off — motion_prompt isn't sent then.
    motion_active: bool
    # The conversation plan as sent on a chat's first turn (no step done yet; the status line
    # changes as the chat goes on), None when the plan is off or empty.
    checklist_prompt: str | None = None
    # preprompt + motion prompt + plan exactly as joined for the model (without knowledge-base
    # excerpts, which are added per message).
    full: str


class ProjectStats(CamelModel):
    total_projects: int
    published_projects: int
    sessions_last_7_days: int
    messages_last_7_days: int


class PreviewMessageRequest(CamelModel):
    message: str
    history: list[ChatHistoryEntry] = []


class PreviewMessageResponse(CamelModel):
    reply: str
    audio_base64: str | None = None
    content_type: str | None = None
    # Avatar gestures, see features/chat/schemas.py::ChatMessageOut.motions.
    motions: list[str] = []
    # Plan ticks, see features/chat/schemas.py::ChatMessageOut.checks.
    checks: list[ChecklistCheck] = []


class ProjectExportData(BaseModel):
    """One project's portable configuration — the same fields ProjectUpdate accepts, minus the
    API key references and chat_password (see features/projects/export.py for why)."""

    title: str = Field(min_length=1)
    description: str | None = None
    preprompt: str | None = None
    start_prompt: str | None = None
    # Library references only survive an import into the same account (see export.py); a
    # bundled default avatar works everywhere.
    avatar_model_id: str | None = None
    builtin_avatar: str | None = None
    avatar_background_id: str | None = None
    grade_level: str | None = None
    temperature: float = 0.5
    top_p: float = 1.0
    save_conversations: bool = False
    survey_before_url: str | None = None
    survey_before_enabled: bool = False
    survey_after_url: str | None = None
    survey_after_enabled: bool = False
    tts_enabled: bool = False
    tts_voice: str | None = None
    # Like avatar_model_id: only kept on import into the account that owns the clip.
    tts_voice_clip_id: str | None = None
    spoken_language: str = "de"
    stt_server_engine: SttServerEngine | None = None
    stt_enabled: bool = True
    stt_browser_enabled: bool = True
    tts_browser_enabled: bool = False
    streaming_enabled: bool = True
    chat_layout: ChatLayout = "avatar_chat"
    motion_enabled: bool = True
    thinking_motions_enabled: bool = False
    motion_prompt: str | None = Field(default=None, max_length=MAX_MOTION_PROMPT_LENGTH)
    require_visitor_name: bool = False
    # The knowledge bases themselves stay in the teacher's library and aren't exported; an
    # imported project starts with knowledge_mode "off" (see export.py::import_project).
    knowledge_mode: KnowledgeMode = "off"
    knowledge_top_k: int = Field(default=4, ge=1, le=MAX_KNOWLEDGE_TOP_K)
    checklist_enabled: bool = False
    checklist_visible: bool = False
    checklist_ordered: bool = True
    checklist_items: list[ChecklistItem] = Field(default=[], max_length=MAX_CHECKLIST_ITEMS)

    @model_validator(mode="before")
    @classmethod
    def upgrade_chat_default_open(cls, data):
        # Exports written before chat_layout existed carry the old chat_default_open flag instead.
        if isinstance(data, dict) and "chat_layout" not in data and "chat_default_open" in data:
            data = dict(data)
            data["chat_layout"] = "avatar_chat" if data.pop("chat_default_open") else "avatar_chat_collapsed"
        return data

    @field_validator("builtin_avatar")
    @classmethod
    def validate_builtin_avatar(cls, value: str | None) -> str | None:
        return _check_builtin_avatar(value)

    @field_validator("checklist_items")
    @classmethod
    def validate_checklist_items(cls, value: list[ChecklistItem]) -> list[ChecklistItem]:
        return _normalize_checklist(value)

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, value: float) -> float:
        if not 0.0 <= value <= MAX_TEMPERATURE:
            raise ValueError(ErrorCode.TEMPERATURE_OUT_OF_RANGE)
        return value

    @field_validator("top_p")
    @classmethod
    def validate_top_p(cls, value: float) -> float:
        if not 0.0 <= value <= MAX_TOP_P:
            raise ValueError(ErrorCode.TOP_P_OUT_OF_RANGE)
        return value
