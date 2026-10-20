"""
Chat Request/Response Shapes

The request and response shapes for the public chat (features/chat/public_router.py) and the
voice-message transcription routes.
"""

from typing import Literal

from pydantic import Field, field_validator

from app.core.error_codes import ErrorCode
from app.core.schema import CamelModel
from app.features.auth.schemas import MAX_PASSWORD_BYTES

# Generous for a real typed/spoken chat turn (roughly a full page of text), but bounded — without
# this, an anonymous visitor could send arbitrarily large messages (or stuff bloat into `history`,
# see ChatMessageIn below) within the existing per-visitor rate limit, inflating token cost
# against the project owner's own LLM API key with no server-side size cap at all.
_MAX_CHAT_MESSAGE_CHARS = 8000

# A conversation-plan step's ID (see features/chat/checklist.py) — short and plain, because it
# travels in every visitor's request history.
CHECKLIST_ID_PATTERN = r"^[a-z0-9]{6,12}$"


def _check_chat_message_length(value: str) -> str:
    if len(value) > _MAX_CHAT_MESSAGE_CHARS:
        raise ValueError(ErrorCode.CHAT_MESSAGE_TOO_LONG)
    return value


# At most one tick per step per message is meaningful; this only stops a client from padding its
# history (see ChatHistoryEntry.checks).
MAX_CHECKS_PER_MESSAGE = 20


class ChecklistCheck(CamelModel):
    """A conversation-plan step ticked off (done) or taken back in an assistant message, see
    features/chat/checklist.py."""

    id: str = Field(pattern=CHECKLIST_ID_PATTERN)
    done: bool


class ChatHistoryEntry(CamelModel):
    # Literal instead of a free str, as protection against prompt injection via the history — a
    # manipulated client could otherwise smuggle in e.g. role="system" and try to overwrite the
    # real system prompt. Anything other than "user"/"assistant" fails with 422 before any code runs.
    role: Literal["user", "assistant"]
    content: str
    # ISO 8601 timestamp of this message, set server-side when a conversation is saved (see
    # features/chat/conversation_store.py::save_turn) — always None on the way in, since the
    # frontend never sends one with its request history. Saved conversations from before this
    # field existed also don't have one.
    timestamp: str | None = None
    # The plan ticks the server returned with this assistant message — echoed back by the
    # browser so the progress can be replayed without any server-side state (see
    # features/chat/checklist.py). Visitor-controlled like the rest of the history, which is
    # fine: forging ticks only changes the visitor's own guidance, and the saved transcript holds
    # what the model really wrote.
    checks: list[ChecklistCheck] = Field(default=[], max_length=MAX_CHECKS_PER_MESSAGE)

    # Caps each entry the same way as the current message (see ChatMessageIn) — history is
    # otherwise just as visitor-controlled (the frontend echoes it back on every request) and
    # would be an easy way around a length cap that only checked `message`.
    @field_validator("content")
    @classmethod
    def validate_content_length(cls, value: str) -> str:
        return _check_chat_message_length(value)


class PublicChecklistItemOut(CamelModel):
    id: str
    title: str


class PublicChecklistOut(CamelModel):
    ordered: bool
    items: list[PublicChecklistItemOut]


class PublicProjectOut(CamelModel):
    title: str
    teacher_name: str
    # The avatar's first message — empty means the frontend shows a generic greeting (see
    # pages/PublicChat/index.tsx). Also passed to the model as context, see
    # features/ai/llm/__init__.py::complete.
    start_prompt: str | None = None
    avatar_model_url: str | None = None
    avatar_background_url: str | None = None
    spoken_language: str = "de"
    tts_enabled: bool = False
    stt_enabled: bool = False
    # Base URL of the on-device (WebGPU) transcription model, or None if this project transcribes
    # on the server only (deployment opt-out, project opt-out, or password-locked) — see
    # features/api_keys/resolve.py::browser_stt_model_url_for.
    browser_stt_model_url: str | None = None
    # Base URL of the on-device speech output model, or None if this project speaks through the
    # server only — see features/api_keys/resolve.py::browser_tts_model_url_for. The page sends
    # speak_on_device once the model has loaded on the visitor's device.
    browser_tts_model_url: str | None = None
    # The preset voice the device should speak with (the project's tts_voice) — None without
    # on-device speech output. A name the model doesn't have, e.g. a cloud provider's voice, makes
    # the device use the model's default voice (see frontend workers/pocketWorker.ts).
    browser_tts_voice: str | None = None
    # Whether the frontend should use POST /messages/stream instead of /messages — see
    # features/chat/pipeline.py::stream_turn. Meaningless (and always False here) without
    # tts_enabled, since the whole point is audio starting before the full reply is ready.
    streaming_enabled: bool = False
    # Avatar and/or chat, see features/projects/models.py::Project.chat_layout.
    chat_layout: str = "avatar_chat"
    # Whether the page loads the MotionEngine for the gestures in replies (see
    # features/projects/models.py::Project.motion_enabled).
    motion_enabled: bool = False
    # Random thinking gestures while the reply is generated (see
    # features/projects/models.py::Project.thinking_motions_enabled).
    thinking_motions_enabled: bool = False
    # The conversation plan as students see it — None when it is off, empty or not visible to
    # students (see features/chat/public_router.py::load_tutor). Never carries the steps' notes.
    checklist: PublicChecklistOut | None = None
    # Already combined server-side with the respective checkbox (see
    # features/chat/public_router.py::load_tutor) — not enabled or without a URL both end up as None here, the
    # anonymous page never needs to know about the checkbox itself.
    survey_before_url: str | None = None
    survey_after_url: str | None = None
    # Whether this project requires a password before start_prompt/avatar/etc. are shown at all —
    # when True and unlocked is False, every field above is deliberately left at its default
    # (nothing persona-related leaks pre-unlock), see features/chat/public_router.py::load_tutor.
    password_protected: bool = False
    unlocked: bool = True
    # Whether a visitor must type a name or ID before the chat starts (see
    # features/chat/visitor_name.py) — unlike password_protected/unlocked above, this doesn't
    # gate any other field here: the name has no secrecy purpose, so nothing needs to be withheld
    # while it's still missing. Whether it's already been entered lives entirely client-side (see
    # lib/visitorNameStorage.ts), since there's nothing server-side to verify it against.
    require_visitor_name: bool = False
    # Told to students, not used for any logic: whether this chat is being recorded, and which AI
    # model answers them. Both exist so the page can be honest about what happens to what they
    # type — see the notice under the chat and the "?" in the header (pages/PublicChat).
    save_conversations: bool = False
    llm_model: str | None = None
    # Route to the once-generated start_prompt audio (see features/projects/start_audio_router.py) —
    # None means it hasn't been generated (yet), in which case the frontend just shows the
    # start_prompt as text without trying to speak it. Populated the same way as start_prompt
    # (stays None while password-locked), see features/chat/public_router.py::load_tutor.
    start_audio_url: str | None = None


class ChatUnlockRequest(CamelModel):
    password: str

    # bcrypt.checkpw (see features/chat/unlock.py) raises an unhandled ValueError above 72
    # bytes instead of truncating — without this, a single oversized guess 500s this endpoint,
    # which needs no login at all.
    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(ErrorCode.CHAT_PASSWORD_TOO_LONG)
        return value


class ChatUnlockOut(CamelModel):
    unlock_token: str


class ChatMessageIn(CamelModel):
    message: str
    # The visible conversation so far, from the frontend (lives only in the browser tab, see
    # pages/PublicChat/index.tsx) — without it, every request would be stateless and the model
    # wouldn't know earlier rounds of the conversation. Additionally capped server-side, see
    # features/ai/llm/history.py::build_messages.
    history: list[ChatHistoryEntry] = []
    # The visitor's device has the on-device speech output model loaded and speaks the reply
    # itself: the server skips its own TTS and sends the spoken text instead (ChatMessageOut.spoken,
    # the stream's "spoken" per chunk). Ignored unless the project offers on-device speech output
    # (see features/api_keys/resolve.py::browser_tts_model_url_for).
    speak_on_device: bool = False

    # Belt and braces alongside the frontend's own guard (sendMessage's trim-check in
    # pages/PublicChat/index.tsx) — a blank message has no legitimate use here, so nothing should
    # ever forward one to the LLM as a real user turn, whether that's a frontend bug or a direct
    # API call.
    @field_validator("message")
    @classmethod
    def validate_message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(ErrorCode.CHAT_MESSAGE_EMPTY)
        return _check_chat_message_length(value)


class ChatMessageOut(CamelModel):
    reply: str
    audio_base64: str | None = None
    content_type: str | None = None
    # Wall-clock duration of the LLM/TTS calls in send_message() — None for tts_ms means TTS didn't
    # run at all (disabled/no key), not that it was instant. The public chat doesn't show them;
    # latency is measured in the dashboard's latency test (latency_router.py).
    llm_ms: float | None = None
    tts_ms: float | None = None
    # Gestures for the avatar, in reply order (see features/chat/motion.py) — empty when the
    # project has motion off.
    motions: list[str] = []
    # Plan steps ticked off or taken back in this reply (see features/chat/checklist.py) — the
    # browser stores them on the message and echoes them back in ChatHistoryEntry.checks.
    checks: list[ChecklistCheck] = []
    # The reply as the visitor's device should say it — only with ChatMessageIn.speak_on_device.
    spoken: str | None = None


class TranscriptionOut(CamelModel):
    text: str
    # Wall-clock duration of the whisper call, for the client-side latency log (see
    # pages/PublicChat/index.tsx) — left None by callers that don't measure it (e.g. the
    # configurator preview's transcribe route).
    stt_ms: float | None = None


class LatencyTestMessageIn(ChatMessageIn):
    """A message for the dashboard's latency test (latency_router.py): the project's chat, with
    some of its configuration overridden for this one request — never saved."""

    # Another of the teacher's own LLM keys (each key carries its model), None: the project's.
    llm_api_key_id: str | None = None
    # "project": as configured; "local": the local-TTS sidecar; "none": text only (also what the
    # page uses when it synthesizes speech in the browser instead).
    tts_mode: Literal["project", "local", "none"] = "project"
    # False: the whole reply is generated and synthesized first, like the plain /messages route.
    streaming: bool = True


class LatencyTranscriptionOut(CamelModel):
    text: str
    # Server-side transcription time only, without upload — the page measures the round trip.
    stt_ms: float
    # What actually transcribed: "whisper", "parakeet", or the STT key's provider.
    engine: str
