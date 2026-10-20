"""
Background Image Routes

Lets a user upload, list, and delete background images shown behind the avatar in a project's
chat. Uploaded files are validated by their actual content (magic bytes), not just their file
extension, before being stored.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session

from app.core.deps import get_current_user, get_current_user_optional, get_session
from app.core.error_codes import ErrorCode
from app.core.ownership import get_owned
from app.core.urls import background_file_url
from app.features.media import service as media
from app.features.media.models import BackgroundImage
from app.features.media.schemas import BackgroundImageOut
from app.features.projects.models import Project
from app.features.users.models import User
from app.storage.files import immutable_file_response, sniff_image

router = APIRouter(prefix="/backgrounds", tags=["background-library"])

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB — generous for a photo, but bounded

_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _to_out(background: BackgroundImage) -> BackgroundImageOut:
    return BackgroundImageOut(
        id=background.id,
        name=background.name,
        file_url=background_file_url(background.id),
        created_at=background.created_at,
    )


@router.get("", response_model=list[BackgroundImageOut])
def list_backgrounds(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """List the current user's uploaded background images."""
    return [_to_out(b) for b in media.list_backgrounds(session, current_user.id)]


@router.post("", response_model=BackgroundImageOut)
async def upload_background(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Upload a new background image."""
    if not file.filename or not file.filename.lower().endswith((".png", ".jpg", ".jpeg")):
        raise HTTPException(status_code=400, detail=ErrorCode.BACKGROUND_INVALID_TYPE)

    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=ErrorCode.BACKGROUND_FILE_TOO_LARGE)

    sniffed = sniff_image(content, {"png", "jpeg"})
    if sniffed is None:
        raise HTTPException(status_code=400, detail=ErrorCode.BACKGROUND_INVALID_CONTENT)

    background = media.create_background(session, current_user.id, Path(file.filename).stem, content, sniffed[1])
    return _to_out(background)


@router.get("/{background_id}/file")
def get_background_file(
    background_id: str,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Serve a background image — to its owner, or anonymously if used in a published project."""
    # Same access pattern as avatars_router.py::get_avatar_file: the owning user (library /
    # preview in the configurator), or anonymous access if the image is actually used as a
    # background in a published project (the public chat needs it visible).
    background = media.get_background(session, background_id)
    if background is None:
        raise HTTPException(status_code=404, detail=ErrorCode.BACKGROUND_NOT_FOUND)

    is_owner = current_user is not None and background.user_id == current_user.id
    if not is_owner and not media.is_used_by_published_project(
        session, Project.avatar_background_id, background_id, background.user_id
    ):
        raise HTTPException(status_code=404, detail=ErrorCode.BACKGROUND_NOT_FOUND)

    extension = Path(background.file_path).suffix.lower()
    media_type = _CONTENT_TYPES.get(extension, "application/octet-stream")
    return immutable_file_response(background.file_path, media_type)


@router.delete("/{background_id}", status_code=204)
def delete_background(
    background_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Delete a background image."""
    background = get_owned(session, BackgroundImage, background_id, current_user.id, ErrorCode.BACKGROUND_NOT_FOUND)
    media.delete_background(session, background)
