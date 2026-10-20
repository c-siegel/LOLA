"""
Pronunciation Word List Routes

Lets a teacher maintain their own list of terms the avatar should pronounce differently ("pH" ->
"p H"), per spoken language, and import/export it as text. The list applies to the speech of
every one of their projects in that language (see service.py::rules_for); the logic lives in
service.py, the test box's in preview.py.
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlmodel import Session

from app.core.deps import get_current_user, get_session
from app.core.error_codes import ErrorCode
from app.core.ownership import get_owned
from app.features.pronunciation import presets, preview, service
from app.features.pronunciation.models import PronunciationEntry
from app.features.pronunciation.schemas import (
    Language,
    PresetApplyResult,
    PresetEntryOut,
    PresetPackOut,
    PresetRemoveResult,
    PronunciationEntryCreate,
    PronunciationEntryOut,
    PronunciationEntryUpdate,
    PronunciationImportIn,
    PronunciationImportResult,
    PronunciationPreviewIn,
    PronunciationPreviewOut,
)
from app.features.users.models import User

router = APIRouter(prefix="/pronunciation", tags=["pronunciation"])


def _to_out(entry: PronunciationEntry) -> PronunciationEntryOut:
    return PronunciationEntryOut(
        id=entry.id,
        language=entry.language,
        term=entry.term,
        spoken=entry.spoken,
        whole_word=entry.whole_word,
        case_sensitive=entry.case_sensitive,
        source_pack=entry.source_pack,
        created_at=entry.created_at,
    )


@router.get("/entries", response_model=list[PronunciationEntryOut])
def list_entries(
    language: Language | None = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List the current user's word list, optionally for one language."""
    return [_to_out(e) for e in service.list_entries(session, current_user.id, language)]


@router.post("/entries", response_model=PronunciationEntryOut, status_code=201)
def create_entry(
    data: PronunciationEntryCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Add a term to the current user's word list."""
    return _to_out(service.create_entry(session, current_user.id, data))


@router.put("/entries/{entry_id}", response_model=PronunciationEntryOut)
def update_entry(
    entry_id: str,
    data: PronunciationEntryUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Change a term, its spoken form or its options (the language stays)."""
    entry = get_owned(session, PronunciationEntry, entry_id, current_user.id, ErrorCode.PRONUNCIATION_ENTRY_NOT_FOUND)
    return _to_out(service.update_entry(session, entry, data))


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(
    entry_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Remove a term from the word list."""
    entry = get_owned(session, PronunciationEntry, entry_id, current_user.id, ErrorCode.PRONUNCIATION_ENTRY_NOT_FOUND)
    service.delete_entry(session, entry)


@router.post("/import", response_model=PronunciationImportResult)
def import_entries(
    data: PronunciationImportIn,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Add terms from pasted text or a .txt/.csv file — one "term = spoken" or "term;spoken" per line.

    With dryRun nothing is saved; the result says what would be added, updated and skipped.
    """
    return service.import_text(
        session, current_user.id, data.language, data.text, overwrite=data.overwrite, dry_run=data.dry_run
    )


@router.get("/export")
def export_entries(
    language: Language = Query(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Download the word list for one language as CSV (the same format the import reads)."""
    # A BOM so Excel opens the umlauts and symbols as UTF-8 instead of guessing a legacy codepage.
    content = "﻿" + service.export_csv(session, current_user.id, language)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pronunciation-{language}.csv"'},
    )


@router.post("/preview", response_model=PronunciationPreviewOut)
def preview_text(
    data: PronunciationPreviewIn,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Show how a sample sentence is handed to the TTS, and with synthesize=true, play it.

    A plain `def`: synthesis blocks on the provider (or the local sidecar) for seconds, which
    FastAPI then runs in its thread pool instead of on the event loop.
    """
    return preview.preview(session, current_user.id, data)


@router.get("/presets", response_model=list[PresetPackOut])
def list_presets(
    language: Language | None = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """The ready-made word lists (e.g. physics), with their entries and whether they're applied."""
    return [
        PresetPackOut(
            id=pack.id,
            language=pack.language,
            version=pack.version,
            entries=[
                PresetEntryOut(
                    term=r.term, spoken=r.spoken, whole_word=r.whole_word, case_sensitive=r.case_sensitive
                )
                for r in pack.entries
            ],
            applied_count=presets.applied_count(session, current_user.id, pack.id, pack.language),
        )
        for pack in presets.all_packs()
        if language is None or pack.language == language
    ]


@router.post("/presets/{pack_id}/apply", response_model=PresetApplyResult)
def apply_preset(
    pack_id: str,
    language: Language = Query(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Copy a preset pack into the word list; terms the user already has are kept as they are."""
    added, skipped = presets.apply_pack(session, current_user.id, pack_id, language)
    return PresetApplyResult(added=added, skipped=skipped)


@router.delete("/presets/{pack_id}", response_model=PresetRemoveResult)
def remove_preset(
    pack_id: str,
    language: Language = Query(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Remove the entries a preset pack added (edited ones are the user's own and stay)."""
    return PresetRemoveResult(removed=presets.remove_pack(session, current_user.id, pack_id, language))
