"""
Cached Start-Prompt Audio

A project's start_prompt (the avatar's opening line) is synthesized once, on the educator's
request, and stored — so it doesn't need to be re-synthesized on every visitor's chat load.
Invalidated whenever the prompt, voice, or TTS key changes (see service.py::update_project).
"""

from pathlib import Path

from sqlmodel import Session

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.features.ai.tts import synthesize_speech
from app.features.api_keys.crypto import scrub_key_from_text
from app.features.api_keys.resolve import resolve_tts_key
from app.features.media.service import voice_reference_for_project
from app.features.projects.models import Project
from app.features.pronunciation.service import rules_for
from app.features.users.models import User
from app.storage.files import save_file, unlink_quietly

# The only two content types synthesize_speech ever returns (MP3 from every cloud provider, real
# WAV from the local-TTS sidecar) — used to pick a matching file extension when caching the audio
# to disk, and the reverse mapping to serve it back with the right Content-Type. Not Python's
# stdlib `mimetypes` module: its audio/wav guess is inconsistent across platforms ("audio/x-wav"
# on some), and this only ever needs to cover these two known cases.
_AUDIO_FILE_EXTENSIONS = {"audio/mpeg": ".mp3", "audio/wav": ".wav"}
_AUDIO_CONTENT_TYPE_BY_EXTENSION = {ext: content_type for content_type, ext in _AUDIO_FILE_EXTENSIONS.items()}


class StartPromptRequired(DomainError):
    status_code = 400
    detail = ErrorCode.START_PROMPT_REQUIRED


class TtsNotConfigured(DomainError):
    status_code = 400
    detail = ErrorCode.TTS_NOT_CONFIGURED


def generate_start_audio(session: Session, project: Project) -> Project:
    """Synthesize the project's start_prompt and store it as the project's start audio."""
    if not project.start_prompt or not project.start_prompt.strip():
        raise StartPromptRequired()
    api_key = resolve_tts_key(session, project) if project.tts_enabled else None
    # A missing key is only a hard failure without the local-TTS fallback available — with it,
    # synthesize_speech(..., None, ...) below succeeds via the sidecar instead.
    if not project.tts_enabled or (api_key is None and not settings.local_tts_enabled):
        raise TtsNotConfigured()
    try:
        audio_bytes, content_type = synthesize_speech(
            project.start_prompt,
            project.tts_voice,
            api_key,
            project.spoken_language,
            voice_clip=voice_reference_for_project(session, project) if api_key is None else None,
            pronunciation=rules_for(session, project.user_id, project.spoken_language),
        )
    except Exception as exc:
        # The educator's own context — the concrete (key-scrubbed) provider message helps them
        # debug, same as the preview chat.
        raise DomainError(
            status_code=502,
            detail={
                "code": ErrorCode.START_AUDIO_GENERATION_FAILED,
                # No key to scrub when the local-TTS sidecar (not a cloud provider) failed.
                "message": scrub_key_from_text(str(exc), api_key.encrypted_api_key) if api_key else str(exc),
            },
        ) from exc

    # Deterministic filename (not a fresh UUID per generation, unlike the avatar library) —
    # regenerating just overwrites the same file, so there's never a stale one left behind. The
    # extension follows the actual content type, and servable_start_audio infers the media type
    # the same way, so a mismatched Content-Type header never reaches the browser.
    extension = _AUDIO_FILE_EXTENSIONS.get(content_type, ".mp3")
    directory = Path(settings.start_audio_upload_dir) / project.user_id
    file_name = f"{project.id}{extension}"
    # A previous generation may have used a different provider (and therefore extension) — clear
    # it so switching providers doesn't leave an orphaned file behind alongside the new one.
    if project.start_audio_path and project.start_audio_path != str(directory / file_name):
        unlink_quietly(project.start_audio_path)
    file_path = save_file(directory, file_name, audio_bytes)

    project.start_audio_path = str(file_path)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _owner_enabled(session: Session, project: Project) -> bool:
    owner = session.get(User, project.user_id)
    return owner is not None and owner.enabled


def servable_start_audio(session: Session, project_id: str, current_user: User | None) -> tuple[str, str] | None:
    """The stored start audio's (path, media type) if `current_user` may hear it — its owner, or
    anyone once the project is published by an enabled account — else None."""
    project = session.get(Project, project_id)
    if project is None or not project.start_audio_path:
        return None
    is_owner = current_user is not None and project.user_id == current_user.id
    if not is_owner and not (project.published and _owner_enabled(session, project)):
        return None
    # Defaulting to mpeg keeps serving files written before WAV support (always real MP3s then).
    media_type = _AUDIO_CONTENT_TYPE_BY_EXTENSION.get(Path(project.start_audio_path).suffix, "audio/mpeg")
    return project.start_audio_path, media_type
