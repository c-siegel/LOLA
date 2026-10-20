"""
Project Table

A user's configured AI persona: prompt, avatar, LLM/TTS keys, and publishing state. See the
root README for what a "project" is in this app, and app/features/chat/public_router.py for how a
published project is served to visitors.

How to use:
    from app.features.projects.models import Project

    project = session.get(Project, project_id)
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Field, SQLModel

from app.core.urls import avatar_file_url, background_file_url, builtin_avatar_url, start_audio_url


class Project(SQLModel, table=True):
    """One user's configured AI persona (avatar, prompt, LLM/TTS setup, publishing state)."""

    # Fields carried over 1:1 from the source repo.
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    title: str
    description: str | None = None
    status: str = "draft"
    # Reference to the key set up in the API dashboard (provider + model + endpoint). This makes
    # the model choice unambiguous even if two keys for the same provider exist.
    llm_api_key_id: str | None = Field(default=None, foreign_key="userapikey.id", index=True)
    # Denormalized copy of the litellm model string from the referenced key — written whenever
    # the project is saved, so analytics/filtering (analytics_service) and the project cards
    # don't need a join on the key.
    llm_model: str | None = None
    preprompt: str | None = None
    # Shown to students as the avatar's first message AND passed to the model as context (see
    # features/ai/llm/__init__.py::complete) — unlike the previous, purely client-side
    # greeting, the model now actually knows this text too (e.g. for a posed task students are
    # meant to respond to). Empty means the frontend shows a generic greeting.
    start_prompt: str | None = None
    # The avatar is either one of the user's own library models (avatar_model_id, see
    # features/media/models.py) or one of the frontend's bundled default avatars (builtin_avatar,
    # e.g. "julia" for frontend/public/avatars/julia.glb). If both are set, the library model wins;
    # if neither is, the frontend shows its default avatar.
    avatar_model_id: str | None = Field(default=None, foreign_key="avatarmodel.id", index=True)
    builtin_avatar: str | None = None
    # A background image from the user's library — None keeps showing the neutral light-gray
    # default surface.
    avatar_background_id: str | None = Field(default=None, foreign_key="backgroundimage.id", index=True)
    grade_level: str | None = None
    pedagogy: str | None = None
    safety: str | None = None
    lesson_behavior: str | None = None
    custom_prompt: str | None = None
    prompt_mode_auto: bool = True
    manual_system_prompt: str | None = None
    # Both are passed straight through to the model as the standard sampling parameters (see
    # features/ai/llm). Providers accept 0.0-2.0 for temperature and 0.0-1.0 for top_p;
    # the same range is enforced in features/projects/schemas.py so a bad value is rejected here rather
    # than by the provider mid-chat. Changing both at once is discouraged (see the hint texts in
    # the configurator), but nothing stops an educator from doing it.
    temperature: float = 0.5
    # 1.0 = no nucleus filtering, which is what every provider defaults to.
    top_p: float = 1.0
    # Whether the model reasons ("thinks") before it answers: "default" leaves the model's own
    # behaviour alone, "on"/"off" are sent to providers that can switch it (see
    # features/ai/llm/history.py::thinking_extra_body). Mainly for comparing latency with and
    # without reasoning on the same model.
    thinking_mode: str = "default"
    published: bool = False
    share_slug: str | None = Field(default=None, unique=True, index=True)
    save_conversations: bool = False
    # Optional Tally.so surveys (for research purposes), passed through unchanged as an iframe
    # source — a valid/allowed Tally URL is the user's own responsibility (same pattern as the
    # free-text model field, see lib/llmModels.ts). One checkbox per survey decouples "a URL is
    # set" from "the survey is currently active".
    survey_before_url: str | None = None
    survey_before_enabled: bool = False
    survey_after_url: str | None = None
    survey_after_enabled: bool = False
    tts_enabled: bool = False
    # Sentence-chunked streaming for the public chat (see features/chat/streaming.py) — ignored
    # for the GWDG Arcana LLM provider's citation-guarded stream, which always streams regardless
    # of this flag (see features/ai/llm/arcana.py::ArcanaReferenceGuard).
    streaming_enabled: bool = True
    # Reference to a key of type "tts" (mirrors llm_api_key_id) — replaces the old tts_provider
    # (a free string), so multiple similar TTS keys (e.g. two custom endpoints) stay unambiguous.
    # See features/api_keys/resolve.py::resolve_tts_key.
    tts_api_key_id: str | None = Field(default=None, foreign_key="userapikey.id", index=True)
    tts_voice: str | None = None
    # A clip from the owner's voice library to clone the voice from — only used by local TTS
    # (no tts_api_key_id set), see features/media/service.py::voice_reference_for_project. None
    # speaks with the sidecar's default voice for spoken_language.
    tts_voice_clip_id: str | None = Field(default=None, foreign_key="voiceclip.id", index=True)
    # Does NOT control lip-sync quality (that's now audio-driven via HeadAudio, independent of
    # language) — instead it's the language hint for STT (features/ai/stt) and a short
    # preprompt instruction, so text replies actually come back in this language.
    spoken_language: str = "de"
    # Reference to a key of type "stt" (mirrors tts_api_key_id/llm_api_key_id) — selects a
    # "bring your own key" (BYOK) STT provider (currently GWDG SAIA). None (the default) keeps
    # transcribing locally through the instance-wide Whisper server instead, see
    # features/ai/stt/__init__.py::transcribe_audio.
    stt_api_key_id: str | None = Field(default=None, foreign_key="userapikey.id", index=True)
    # Which local engine transcribes on the server when no stt_api_key_id is set: "whisper" or
    # "parakeet" (see features/ai/stt/__init__.py::get_stt_client). None (the default) follows
    # the deployment's Settings.stt_engine.
    stt_server_engine: str | None = None
    # Only unlocks the microphone button in the public chat — stt_api_key_id (above) decides which
    # engine actually transcribes.
    stt_enabled: bool = True
    # Whether this project transcribes on the visitor's device (WebGPU) before falling back to
    # stt_api_key_id/local server Whisper — only takes effect when the deployment also has it
    # enabled (see Settings.browser_stt_enabled), see
    # features/api_keys/resolve.py::browser_stt_model_url_for. On by default; False opts out.
    stt_browser_enabled: bool = True
    # Whether the visitor's device speaks the replies (Pocket TTS in the browser) instead of the
    # server — only takes effect with tts_enabled, when the deployment offers it
    # (Settings.browser_tts_enabled) and for its language, see
    # features/api_keys/resolve.py::browser_tts_model_url_for. The server TTS (tts_api_key_id or
    # the local sidecar) stays the fallback for devices that can't load the model. Off by default:
    # every visitor's device has to download the model first.
    tts_browser_enabled: bool = False
    # What the public chat page shows (see schemas.py::ChatLayout): "avatar_chat" (avatar + open
    # chat), "avatar_chat_collapsed" (chat starts collapsed but can be expanded), "avatar_only"
    # (voice only, no chat column) or "chat_only" (no 3D avatar is loaded at all).
    chat_layout: str = "avatar_chat"
    # Body language from the vendored MotionEngine (frontend/src/vendor/motion-engine): the LLM is
    # told it may mark gestures as "::name::" in its reply (see features/chat/motion.py), which
    # are stripped before TTS and played by the avatar. Has no effect with chat_layout
    # "chat_only", where no avatar is shown. On by default; False keeps TalkingHead's own idle
    # motion only.
    motion_enabled: bool = True
    # While the reply is being generated, the avatar plays random "thinking" gestures (see
    # frontend/src/components/TalkingHeadAvatar). Needs motion_enabled, since it uses the same
    # MotionEngine. Off by default: it is a matter of taste and of how fast replies arrive.
    thinking_motions_enabled: bool = False
    # The teacher's own wording of the gesture instructions appended to the system prompt (see
    # features/chat/motion.py::effective_motion_prompt). None = the built-in MOTION_PROMPT, so
    # existing projects follow future improvements to it.
    motion_prompt: str | None = None
    # Optional teacher-set access gate for the public chat link (see
    # features/chat/unlock.py) — bcrypt hash, same scheme as User.password_hash. None means anyone with the
    # share link can chat, same as before this field existed.
    chat_password_hash: str | None = None
    # Whether a visitor must type a name or ID before the chat starts (see
    # features/chat/visitor_name.py) — unlike chat_password_hash this isn't a secret, just a
    # label that ends up on Conversation.visitor_name so a saved transcript can be told apart from
    # every other visitor's. Only takes effect together with save_conversations: it's pointless
    # (and needlessly identifying) to ask for a name when nothing is being kept.
    require_visitor_name: bool = False
    # Filesystem path to the once-synthesized audio for start_prompt (see
    # features/ai/tts, features/projects/start_audio.py) — None until the educator
    # clicks "Generate audio", so every visitor's chat load doesn't re-synthesize the same fixed
    # text. Cleared automatically whenever start_prompt/tts_voice/tts_api_key_id changes (see
    # features/projects/service.py::update_project), so a stale voice/text is never served.
    start_audio_path: str | None = None
    # Knowledge base (RAG, see features/knowledge/): "off", "supplement" (use the attached
    # material where it helps) or "strict" (answer only from it). Only takes effect when the
    # deployment has Settings.rag_enabled and at least one knowledge base is attached.
    knowledge_mode: str = "off"
    # How many passages are added to the system prompt per turn.
    knowledge_top_k: int = 4
    # IDs of the owner's knowledge bases, as a JSON list — a list of a handful of IDs that's only
    # ever read whole, so a join table would add queries without adding anything. Kept clean by
    # features/knowledge/service.py when a knowledge base is deleted.
    knowledge_base_ids_json: str = "[]"
    # Conversation plan (see features/chat/checklist.py): steps the avatar ticks off with
    # "::done_N::" / "::undone_N::" markers. Off by default, like every opt-in teaching aid.
    checklist_enabled: bool = False
    # Whether students see the plan in the public chat. Item notes are never shown either way.
    checklist_visible: bool = False
    # True: the steps are meant to be worked through in order; False: in any order.
    checklist_ordered: bool = True
    # The steps as a JSON list of {"id", "title", "note"} — like knowledge_base_ids_json, a short
    # list only ever read whole. The IDs are stable across reordering (the model only sees
    # positions), so ticks stored in a running chat keep pointing at the right step.
    checklist_json: str = "[]"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def password_protected(self) -> bool:
        """Whether a visitor must unlock this project's chat with a password before using it."""
        return self.chat_password_hash is not None

    @property
    def knowledge_base_ids(self) -> list[str]:
        """The attached knowledge bases' IDs (see knowledge_base_ids_json)."""
        try:
            ids = json.loads(self.knowledge_base_ids_json or "[]")
        except ValueError:
            return []
        return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []

    @property
    def checklist_items(self) -> list[dict]:
        """The conversation plan's steps (see checklist_json), each {"id", "title", "note"}."""
        try:
            items = json.loads(self.checklist_json or "[]")
        except ValueError:
            return []
        if not isinstance(items, list):
            return []
        return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("title"), str)]

    @property
    def start_audio_url(self) -> str | None:
        """URL of the cached start-prompt audio, or None if it hasn't been generated yet."""
        if not self.start_audio_path:
            return None
        try:
            version = Path(self.start_audio_path).stat().st_mtime_ns
        except OSError:
            version = None
        return start_audio_url(self.id, version)

    @property
    def avatar_model_url(self) -> str | None:
        """URL of the avatar's .glb file (library model or bundled default), or None for the frontend default."""
        if self.avatar_model_id:
            return avatar_file_url(self.avatar_model_id)
        if self.builtin_avatar:
            return builtin_avatar_url(self.builtin_avatar)
        return None

    @property
    def avatar_background_url(self) -> str | None:
        """URL of the background image, or None for the neutral default surface."""
        return background_file_url(self.avatar_background_id) if self.avatar_background_id else None
