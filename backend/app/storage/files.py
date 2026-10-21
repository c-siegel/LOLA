"""
Uploaded-File Storage

The shared file handling behind every upload: avatar models and thumbnails, background images,
profile pictures, and generated start-prompt audio. Files live on local disk under the upload
directories configured in core/config.py; the database only stores their paths.

How to use:
    from app.storage.files import save_file, sniff_image, unlink_quietly

    sniffed = sniff_image(content, {"png", "jpeg"})  # None if it's neither
    path = save_file(Path(settings.background_upload_dir) / user_id, f"{uuid4()}.png", content)
"""

from pathlib import Path

from fastapi import UploadFile
from fastapi.responses import FileResponse

from app.core.errors import DomainError

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GLTF_MAGIC = b"glTF"  # first 4 bytes of a real .glb file (binary glTF), per the glTF spec

# Safe to cache forever wherever the URL contains a fresh UUID per upload — re-uploading always
# creates a new id, so the content behind one URL never changes.
_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def sniff_image(content: bytes, allowed: set[str]) -> tuple[str, str] | None:
    """Detect an image type from the file's magic bytes: (media type, file extension), or None if
    it isn't one of `allowed` ("png", "jpeg", "webp").

    The file extension is just a hint from the user, not proof — checking the real signature at
    the start of the file stops arbitrary (possibly malicious) files from being stored and later
    served back out under a false extension.
    """
    if "png" in allowed and content[:8] == _PNG_MAGIC:
        return "image/png", ".png"
    if "jpeg" in allowed and content[:3] == _JPEG_MAGIC:
        return "image/jpeg", ".jpg"
    if "webp" in allowed and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def is_glb(content: bytes) -> bool:
    """Whether `content` starts with the binary glTF signature ("glTF")."""
    return content[:4] == _GLTF_MAGIC


def read_upload(file: UploadFile, max_bytes: int, too_large: str) -> bytes:
    """The upload's bytes; refuses anything over `max_bytes` with HTTP 400 and `too_large`.

    Reads one byte past the limit rather than the whole file, so an oversized upload is caught
    without loading all of it. Blocking: call it from a plain `def` route, which FastAPI runs in
    its thread pool.
    """
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise DomainError(too_large)
    return content


def save_file(directory: Path, filename: str, content: bytes) -> Path:
    """Write `content` to `directory`/`filename`, creating the directory if needed."""
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / filename
    file_path.write_bytes(content)
    return file_path


def unlink_quietly(path: str | None) -> None:
    """Delete a stored file if it's still there.

    missing_ok: a file already gone (manually cleaned up, or a failed earlier delete) must not
    abort whatever deletion it's part of.
    """
    if path:
        Path(path).unlink(missing_ok=True)


def immutable_file_response(path: str, media_type: str, filename: str | None = None) -> FileResponse:
    """Serve a stored file with a cache-forever header (for URLs that embed a per-upload UUID)."""
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": _IMMUTABLE_CACHE_CONTROL},
    )
