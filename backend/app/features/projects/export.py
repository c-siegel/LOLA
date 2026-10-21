"""
Project Export/Import

Turns a Project into a portable YAML document, and back — so an educator can back up a
project, move it to another LOLA account, or share a configuration with a colleague
without also handing over their own API keys.

What is YAML? A human-readable text format for structured data (like JSON, but easier to
hand-edit) — see app/features/projects/transfer_router.py for where this is used.

How to use:
    from app.features.projects.export import export_project_yaml, import_project

    yaml_text = export_project_yaml(project)
    new_project = import_project(session, current_user.id, parse_project_yaml(yaml_text))
"""

import json
import re

import yaml
from pydantic import ValidationError
from sqlmodel import Session

from app.core.ownership import find_owned
from app.features.media.models import AvatarModel, BackgroundImage, VoiceClip
from app.features.projects.models import Project
from app.features.projects.schemas import ProjectExportData

# Bumped only if a future format change stops being readable by older versions of this parser.
# Version 2 references the avatar/background by id (plus builtin_avatar for a bundled default
# avatar); version 1 files carried their URLs instead and are still accepted (see _upgrade_v1).
FORMAT_VERSION = 2

_YAML_HEADER = (
    "# LOLA project export\n"
    "# Re-import this file (Overview page) to recreate this project's configuration.\n"
    "# API keys, the publish state, and the chat password are never included here — set those\n"
    "# up again after importing.\n"
)

# The URL shapes a version-1 export stored (the API's routes at the time, without any prefix).
_V1_LIBRARY_AVATAR_RE = re.compile(r"^/avatar-models/([^/]+)/file$")
_V1_BUILTIN_AVATAR_RE = re.compile(r"^/avatars/([a-z0-9-]{1,40})\.glb$")
_V1_BACKGROUND_RE = re.compile(r"^/backgrounds/([^/]+)/file$")

# Generous cap for a hand-edited YAML text file — real exports are a few hundred bytes.
MAX_IMPORT_UPLOAD_BYTES = 256 * 1024


class ProjectImportError(ValueError):
    """Raised when an uploaded file isn't a valid LOLA project export."""


def export_project_yaml(project: Project) -> str:
    """Serialize `project`'s portable configuration (no API keys, publishing state, or IDs) as YAML."""
    data = ProjectExportData.model_validate(project, from_attributes=True)
    payload = {"lola_export": FORMAT_VERSION, "project": data.model_dump()}
    return _YAML_HEADER + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def export_filename(project: Project) -> str:
    """Filesystem-safe .yml filename for `project`, e.g. "my-project.yml"."""
    slug = re.sub(r"[^a-z0-9]+", "-", project.title.strip().lower()).strip("-")
    return f"{slug or 'project'}.yml"


def parse_project_yaml(raw: bytes | str) -> ProjectExportData:
    """Parse and validate an uploaded file as a LOLA project export.

    Raises ProjectImportError for anything that isn't a well-formed export — not the file's own
    fault vs. a bug distinction; every failure just means "can't import this file".
    """
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ProjectImportError("not valid YAML") from exc
    if not isinstance(document, dict) or not isinstance(document.get("project"), dict):
        raise ProjectImportError("missing a 'project' section")
    fields = document["project"]
    if document.get("lola_export", 1) == 1:
        fields = _upgrade_v1(fields)
    try:
        return ProjectExportData.model_validate(fields)
    except ValidationError as exc:
        raise ProjectImportError(str(exc)) from exc


def _upgrade_v1(fields: dict) -> dict:
    """Turn a version-1 export's avatar/background URLs into version-2 references."""
    fields = dict(fields)
    avatar_url = fields.pop("avatar_model_url", None) or ""
    background_url = fields.pop("avatar_background_url", None) or ""
    if match := _V1_LIBRARY_AVATAR_RE.match(avatar_url):
        fields["avatar_model_id"] = match.group(1)
    elif match := _V1_BUILTIN_AVATAR_RE.match(avatar_url):
        fields["builtin_avatar"] = match.group(1)
    if match := _V1_BACKGROUND_RE.match(background_url):
        fields["avatar_background_id"] = match.group(1)
    return fields


def import_project(session: Session, user_id: str, data: ProjectExportData) -> Project:
    """Create a new project for `user_id` from a parsed export.

    Always a new project (never overwrites an existing one) and always a draft — API keys are
    never part of an export (see ProjectExportData), so llm_model/publishing would otherwise be
    silently broken/misleading until the educator re-configures them anyway.
    """
    fields = data.model_dump()
    # Keep a library reference only if it still points at one of this user's own uploads —
    # dropped instead of failing the import, e.g. when importing a colleague's export whose avatar
    # isn't in this account's library, so the project just falls back to the default look.
    for field, model in (
        ("avatar_model_id", AvatarModel),
        ("avatar_background_id", BackgroundImage),
        ("tts_voice_clip_id", VoiceClip),
    ):
        if fields[field] and find_owned(session, model, fields[field], user_id) is None:
            fields[field] = None
    # Knowledge bases aren't part of an export, so an imported project has none attached — it
    # must not claim to answer from material it doesn't have.
    fields["knowledge_mode"] = "off"
    checklist_items = fields.pop("checklist_items")
    project = Project(user_id=user_id, checklist_json=json.dumps(checklist_items), **fields)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project
