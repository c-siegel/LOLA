"""
Avatar, Background And Voice Libraries

Each user's reusable uploads: 3D avatar models (.glb, with an optional client-rendered PNG
thumbnail), background images shown behind the avatar, and voice clips that local TTS clones a
project's voice from. Stores the files (see app/storage/files.py) and their DB rows, and decides
who may download them.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import settings
from app.core.ownership import find_owned
from app.features.ai.tts.local import VoiceReference, forget_voice
from app.features.media.models import AvatarModel, BackgroundImage, VoiceClip
from app.features.projects.models import Project
from app.features.users.models import User
from app.storage.files import save_file, unlink_quietly


def is_used_by_published_project(session: Session, column, item_id: str, owner_id: str) -> bool:
    """Whether a published project of `owner_id` references `item_id` in `column` (Project.avatar_model_id
    or Project.avatar_background_id) — the condition for serving a library file anonymously, since
    the public chat needs the avatar/background visible to students."""
    # Only the asset owner's own projects count: otherwise anyone could publish a project pointing
    # at a guessed or leaked ID and so make someone else's private upload public.
    # A disabled owner's chats are offline (see core/deps.py::get_published_project), so their files are too.
    query = (
        select(Project)
        .join(User, User.id == Project.user_id)
        .where(column == item_id, Project.published == True, Project.user_id == owner_id, User.enabled == True)  # noqa: E712
    )
    return session.exec(query).first() is not None


# ==================== AVATARS ====================


def list_avatars(session: Session, user_id: str) -> list[AvatarModel]:
    return list(session.exec(select(AvatarModel).where(AvatarModel.user_id == user_id)).all())


def get_avatar(session: Session, avatar_id: str) -> AvatarModel | None:
    return session.get(AvatarModel, avatar_id)


def create_avatar(session: Session, user_id: str, name: str, content: bytes) -> AvatarModel:
    """Store an already-validated .glb file and its library entry."""
    # UUID filename instead of the original filename — avoids collisions and path traversal via
    # the filename.
    file_path = save_file(Path(settings.avatar_upload_dir) / user_id, f"{uuid.uuid4()}.glb", content)
    avatar = AvatarModel(user_id=user_id, name=name, file_path=str(file_path))
    session.add(avatar)
    session.commit()
    session.refresh(avatar)
    return avatar


def set_avatar_thumbnail(session: Session, avatar: AvatarModel, content: bytes) -> AvatarModel:
    """Store an already-validated PNG thumbnail for `avatar`."""
    thumbnail_path = save_file(
        Path(settings.avatar_thumbnail_upload_dir) / avatar.user_id, f"{uuid.uuid4()}.png", content
    )
    avatar.thumbnail_path = str(thumbnail_path)
    session.add(avatar)
    session.commit()
    session.refresh(avatar)
    return avatar


def delete_avatar(session: Session, avatar: AvatarModel) -> None:
    """Delete an avatar's files and its library entry."""
    # No protection against "this avatar is currently selected in a project" — a project that
    # still points at the deleted file simply gets a 404 when it tries to load it (already
    # handled by TalkingHeadAvatar, which shows an initials fallback — see
    # components/TalkingHeadAvatar.tsx).
    unlink_quietly(avatar.file_path)
    unlink_quietly(avatar.thumbnail_path)
    session.delete(avatar)
    session.commit()


# ==================== BACKGROUNDS ====================


def list_backgrounds(session: Session, user_id: str) -> list[BackgroundImage]:
    return list(session.exec(select(BackgroundImage).where(BackgroundImage.user_id == user_id)).all())


def get_background(session: Session, background_id: str) -> BackgroundImage | None:
    return session.get(BackgroundImage, background_id)


def create_background(session: Session, user_id: str, name: str, content: bytes, extension: str) -> BackgroundImage:
    """Store an already-validated image (extension taken from its sniffed content) and its library entry."""
    file_path = save_file(Path(settings.background_upload_dir) / user_id, f"{uuid.uuid4()}{extension}", content)
    background = BackgroundImage(user_id=user_id, name=name, file_path=str(file_path))
    session.add(background)
    session.commit()
    session.refresh(background)
    return background


def delete_background(session: Session, background: BackgroundImage) -> None:
    """Delete a background's file and its library entry."""
    # No protection against "this image is currently selected in a project" — a project that
    # still points at it simply gets a 404 on load and falls back to the neutral default surface
    # (the background-color stays visible, see PublicChat.module.css/.avatarStage).
    unlink_quietly(background.file_path)
    session.delete(background)
    session.commit()


# ==================== VOICE CLIPS ====================


def list_voice_clips(session: Session, user_id: str) -> list[VoiceClip]:
    query = select(VoiceClip).where(VoiceClip.user_id == user_id).order_by(VoiceClip.created_at)
    return list(session.exec(query).all())


def create_voice_clip(session: Session, user_id: str, name: str, wav: bytes, duration_seconds: float) -> VoiceClip:
    """Store an already-normalized WAV clip (see voice_audio.py) and its library entry.

    Only reachable after the uploader confirmed consent (see voices_router.py), hence the
    timestamp is simply "now".
    """
    file_path = save_file(Path(settings.voice_clip_upload_dir) / user_id, f"{uuid.uuid4()}.wav", wav)
    clip = VoiceClip(
        user_id=user_id,
        name=name,
        file_path=str(file_path),
        sha256=hashlib.sha256(wav).hexdigest(),
        duration_seconds=duration_seconds,
        consent_confirmed_at=datetime.now(timezone.utc),
    )
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return clip


def delete_voice_clip(session: Session, clip: VoiceClip) -> None:
    """Delete a clip's file, its library entry, and every use of it.

    Unlike a deleted avatar (which a project just fails to load), a deleted voice must not keep
    speaking: projects using it go back to the default voice, and a start-prompt audio already
    generated with it is dropped, since it would still play the deleted voice.
    """
    for project in session.exec(select(Project).where(Project.tts_voice_clip_id == clip.id)).all():
        project.tts_voice_clip_id = None
        unlink_quietly(project.start_audio_path)
        project.start_audio_path = None
        session.add(project)
    unlink_quietly(clip.file_path)
    session.delete(clip)
    session.commit()
    forget_voice(clip.sha256)


def voice_reference_for_project(session: Session, project: Project) -> VoiceReference | None:
    """The voice clip a project's local TTS should clone, or None for the default voice.

    Only one of the project owner's own clips counts — a project row pointing at someone else's
    clip (e.g. a hand-edited import) must never make that person's voice speak.
    """
    if not project.tts_voice_clip_id:
        return None
    clip = find_owned(session, VoiceClip, project.tts_voice_clip_id, project.user_id)
    return VoiceReference(sha256=clip.sha256, path=clip.file_path) if clip else None
