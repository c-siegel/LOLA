"""
Avatar Model Routes

Lets a user upload, list, and delete their own 3D avatar models and thumbnails, used by the
frontend's TalkingHeadAvatar renderer. Uploaded files are validated by their actual content
(magic bytes), not just their file extension, before being stored.

What is a .glb file?
A .glb file is a single binary file containing a 3D model in the glTF format — this is what
the frontend's three.js-based avatar renderer loads to display and animate the avatar.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session

from app.core.deps import get_current_user, get_current_user_optional, get_session
from app.core.error_codes import ErrorCode
from app.core.ownership import find_owned, get_owned
from app.core.urls import avatar_file_url, avatar_thumbnail_url
from app.features.media import service as media
from app.features.media.models import AvatarModel
from app.features.media.schemas import AvatarModelOut
from app.features.projects.models import Project
from app.features.users.models import User
from app.storage.files import immutable_file_response, is_glb, sniff_image

router = APIRouter(prefix="/avatars", tags=["avatar-library"])

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB — generous but bounded (HeadTTS avatars are ~4.5 MB)
_MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024  # 2 MB is plenty for a 256x256 PNG snapshot


def _to_out(avatar: AvatarModel) -> AvatarModelOut:
    return AvatarModelOut(
        id=avatar.id,
        name=avatar.name,
        file_url=avatar_file_url(avatar.id),
        thumbnail_url=avatar_thumbnail_url(avatar.id) if avatar.thumbnail_path else None,
        created_at=avatar.created_at,
    )


@router.get("", response_model=list[AvatarModelOut])
def list_avatar_models(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """List the current user's uploaded avatar models."""
    return [_to_out(a) for a in media.list_avatars(session, current_user.id)]


@router.post("", response_model=AvatarModelOut)
async def upload_avatar_model(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Upload a new .glb avatar model file."""
    if not file.filename or not file.filename.lower().endswith(".glb"):
        raise HTTPException(status_code=400, detail=ErrorCode.AVATAR_FILE_INVALID_TYPE)

    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=ErrorCode.AVATAR_FILE_TOO_LARGE)
    if not is_glb(content):
        raise HTTPException(status_code=400, detail=ErrorCode.AVATAR_FILE_INVALID_CONTENT)

    avatar = media.create_avatar(session, current_user.id, file.filename.removesuffix(".glb"), content)
    return _to_out(avatar)


@router.get("/{avatar_id}/file")
def get_avatar_file(
    avatar_id: str,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Serve an avatar's .glb file — to its owner, or anonymously if used in a published project."""
    # Like get_owned_project (deps.py): returns 404 instead of 403 for foreign/inaccessible
    # avatars, to prevent IDOR (Insecure Direct Object Reference) enumeration. Two legitimate
    # access paths: the owning user (avatar library / preview in the configurator, Screen 1e) —
    # or anonymous access, if the avatar is actually used in a published project (public chat,
    # Screen 1i, needs the avatar visible to students, see PublicChat/index.tsx). Unpublished or
    # someone else's avatars stay inaccessible to everyone else.
    avatar = media.get_avatar(session, avatar_id)
    if avatar is None:
        raise HTTPException(status_code=404, detail=ErrorCode.AVATAR_NOT_FOUND)

    is_owner = current_user is not None and avatar.user_id == current_user.id
    if not is_owner and not media.is_used_by_published_project(
        session, Project.avatar_model_id, avatar_id, avatar.user_id
    ):
        raise HTTPException(status_code=404, detail=ErrorCode.AVATAR_NOT_FOUND)

    return immutable_file_response(avatar.file_path, "model/gltf-binary", filename=f"{avatar.name}.glb")


@router.post("/{avatar_id}/thumbnail", response_model=AvatarModelOut)
async def set_avatar_thumbnail(
    avatar_id: str,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Attach a thumbnail image to an avatar model."""
    # A PNG rendered client-side from the 3D model, once, by the frontend (see
    # frontend/src/lib/avatarThumbnail.ts) — not an arbitrary user-uploaded image, so the same
    # owner-only check as the model upload itself is enough (no public access needed here, the
    # avatar library is a user-only context).
    avatar = get_owned(session, AvatarModel, avatar_id, current_user.id, ErrorCode.AVATAR_NOT_FOUND)

    content = await file.read(_MAX_THUMBNAIL_BYTES + 1)
    if len(content) > _MAX_THUMBNAIL_BYTES:
        raise HTTPException(status_code=400, detail=ErrorCode.AVATAR_THUMBNAIL_TOO_LARGE)
    if sniff_image(content, {"png"}) is None:
        raise HTTPException(status_code=400, detail=ErrorCode.AVATAR_THUMBNAIL_INVALID)

    return _to_out(media.set_avatar_thumbnail(session, avatar, content))


@router.get("/{avatar_id}/thumbnail")
def get_avatar_thumbnail(
    avatar_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Serve an avatar's thumbnail image."""
    avatar = find_owned(session, AvatarModel, avatar_id, current_user.id)
    if avatar is None or not avatar.thumbnail_path:
        raise HTTPException(status_code=404, detail=ErrorCode.AVATAR_THUMBNAIL_NOT_FOUND)
    return immutable_file_response(avatar.thumbnail_path, "image/png")


@router.delete("/{avatar_id}", status_code=204)
def delete_avatar_model(
    avatar_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Delete an avatar model and its thumbnail file."""
    avatar = get_owned(session, AvatarModel, avatar_id, current_user.id, ErrorCode.AVATAR_NOT_FOUND)
    media.delete_avatar(session, avatar)
