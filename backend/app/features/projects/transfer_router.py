"""
Project Export/Import Routes

Download a project's configuration as YAML, and create a new draft project from such a file —
for backups, moving to another LOLA account, or sharing with a colleague.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlmodel import Session

from app.core.deps import get_current_user, get_owned_project, get_session
from app.core.error_codes import ErrorCode
from app.features.projects.export import (
    MAX_IMPORT_UPLOAD_BYTES,
    ProjectImportError,
    export_filename,
    export_project_yaml,
    import_project,
    parse_project_yaml,
)
from app.features.projects.models import Project
from app.features.projects.schemas import ProjectOut
from app.features.users.models import User

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("/import", response_model=ProjectOut)
def import_project_route(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Create a new (draft) project from a previously exported YAML file.

    This router is registered before the project CRUD router (see app/api_router.py), so
    "import" is never swallowed as a project id.
    """
    content = file.file.read(MAX_IMPORT_UPLOAD_BYTES + 1)
    if len(content) > MAX_IMPORT_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=ErrorCode.PROJECT_IMPORT_FILE_TOO_LARGE)
    try:
        data = parse_project_yaml(content)
    except ProjectImportError as exc:
        raise HTTPException(status_code=400, detail=ErrorCode.PROJECT_IMPORT_INVALID) from exc
    return import_project(session, current_user.id, data)


@router.get("/{project_id}/export")
def export_project(project: Project = Depends(get_owned_project)):
    """Download a project's configuration as a YAML file, for backup or moving it to another
    LOLA account. Never includes API keys, the publish state, or the chat password."""
    return Response(
        content=export_project_yaml(project),
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{export_filename(project)}"'},
    )
