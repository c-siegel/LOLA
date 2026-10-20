"""
Pronunciation Word List Service

Manages each teacher's own pronunciation rules (features/pronunciation/models.py): adding,
editing and deleting entries, importing/exporting them as plain text/CSV, and handing the TTS a
compiled rule set for a teacher's list (see features/ai/tts/pronunciation.py).

How to use:
    from app.features.pronunciation.service import rules_for

    rules = rules_for(session, project.user_id, project.spoken_language)
    synthesize_speech(text, voice, key, project.spoken_language, pronunciation=rules)
"""

import csv
import io
from dataclasses import dataclass, field

from normt2s import Rule, RuleSet
from sqlmodel import Session, select

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.features.ai.tts.pronunciation import compile_rules
from app.features.pronunciation.models import PronunciationEntry
from app.features.pronunciation.schemas import (
    PronunciationEntryCreate,
    PronunciationEntryUpdate,
    PronunciationImportLineError,
    PronunciationImportResult,
    clean_rule,
)

# Per teacher and language. Far more than a subject's worth of terms, and keeps the one compiled
# pattern every reply runs through small.
MAX_ENTRIES_PER_LANGUAGE = 500

# In an import line, this as the spoken form means "read the term letter by letter".
SPELL_OUT_MARKER = "!spell"
_EXPORT_HEADER = ["term", "spoken", "whole_word", "case_sensitive"]
_HEADER_NAMES = {"term", "begriff"}
_TRUE = {"1", "true", "yes", "ja", "x", "wahr", "y", "j"}
_FALSE = {"0", "false", "no", "nein", "falsch", "n", ""}
# Same formula-injection guard as the conversation export (features/analytics/csv_export.py): a
# term like "=mc²" or "+" would otherwise become a live formula when the file is opened in Excel
# or Sheets. The import strips the quote again, so an export always re-imports unchanged.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


class EntryNotFound(DomainError):
    status_code = 404
    detail = ErrorCode.PRONUNCIATION_ENTRY_NOT_FOUND


class DuplicateEntry(DomainError):
    status_code = 409
    detail = ErrorCode.PRONUNCIATION_ENTRY_DUPLICATE


class LimitReached(DomainError):
    status_code = 400
    detail = ErrorCode.PRONUNCIATION_LIMIT_REACHED


def list_entries(session: Session, user_id: str, language: str | None = None) -> list[PronunciationEntry]:
    query = select(PronunciationEntry).where(PronunciationEntry.user_id == user_id)
    if language is not None:
        query = query.where(PronunciationEntry.language == language)
    return list(session.exec(query.order_by(PronunciationEntry.term)))


def get_owned_entry(session: Session, user_id: str, entry_id: str) -> PronunciationEntry:
    # 404 rather than 403 for someone else's entry, so IDs can't be probed (IDOR defense).
    entry = session.get(PronunciationEntry, entry_id)
    if entry is None or entry.user_id != user_id:
        raise EntryNotFound()
    return entry


def _find_term(session: Session, user_id: str, language: str, term: str) -> PronunciationEntry | None:
    return session.exec(
        select(PronunciationEntry)
        .where(PronunciationEntry.user_id == user_id)
        .where(PronunciationEntry.language == language)
        .where(PronunciationEntry.term == term)
    ).first()


def _count(session: Session, user_id: str, language: str) -> int:
    return len(
        session.exec(
            select(PronunciationEntry.id)
            .where(PronunciationEntry.user_id == user_id)
            .where(PronunciationEntry.language == language)
        ).all()
    )


def create_entry(session: Session, user_id: str, data: PronunciationEntryCreate) -> PronunciationEntry:
    if _find_term(session, user_id, data.language, data.term) is not None:
        raise DuplicateEntry()
    if _count(session, user_id, data.language) >= MAX_ENTRIES_PER_LANGUAGE:
        raise LimitReached()
    entry = PronunciationEntry(
        user_id=user_id,
        language=data.language,
        term=data.term,
        spoken=data.spoken,
        whole_word=data.whole_word,
        case_sensitive=data.case_sensitive,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def update_entry(session: Session, entry: PronunciationEntry, data: PronunciationEntryUpdate) -> PronunciationEntry:
    if data.term != entry.term:
        existing = _find_term(session, entry.user_id, entry.language, data.term)
        if existing is not None:
            raise DuplicateEntry()
    entry.term = data.term
    entry.spoken = data.spoken
    entry.whole_word = data.whole_word
    entry.case_sensitive = data.case_sensitive
    # Edited means it's the teacher's own now — removing the preset pack later must not take it.
    entry.source_pack = None
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def delete_entry(session: Session, entry: PronunciationEntry) -> None:
    session.delete(entry)
    session.commit()


def _to_rule(entry: PronunciationEntry) -> Rule:
    return Rule(entry.term, entry.spoken, entry.whole_word, entry.case_sensitive)


def rules_for(session: Session, user_id: str, language: str) -> RuleSet | None:
    """The compiled word list of `user_id` for `language`, or None if it's empty.

    Read fresh on every call (one small indexed query), so an edit applies to the very next reply
    without any cache to invalidate; compile_rules caches the compiled pattern by content instead.
    """
    entries = list_entries(session, user_id, language)
    if not entries:
        return None
    return compile_rules(_to_rule(e) for e in entries)


# --- Import / export -------------------------------------------------------------------------


@dataclass
class _ParsedLine:
    line: int
    term: str
    spoken: str
    whole_word: bool = True
    case_sensitive: bool = False


@dataclass
class _ParseResult:
    rows: dict[str, _ParsedLine] = field(default_factory=dict)
    errors: list[PronunciationImportLineError] = field(default_factory=list)


def _parse_flag(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ValueError(ErrorCode.PRONUNCIATION_IMPORT_FLAG_INVALID)


def _unquote_formula_guard(value: str) -> str:
    if len(value) > 1 and value[0] == "'" and value[1] in _CSV_FORMULA_TRIGGERS:
        return value[1:]
    return value


def _split_line(raw: str) -> list[str]:
    """One import line's fields, from any of the three formats teachers produce.

    "term;spoken[;whole_word;case_sensitive]" (CSV as German Excel saves it, and the export
    format), tab-separated (cells copied straight out of a spreadsheet), or "term = spoken". For
    the "=" form the *last* "=" splits, because a formula term ("E=mc²") contains one too, while a
    spoken form practically never does.
    """
    if ";" in raw:
        return next(csv.reader([raw], delimiter=";"))
    if "\t" in raw:
        return raw.split("\t")
    if " = " in raw:
        return list(raw.rsplit(" = ", 1))
    if "=" in raw:
        return list(raw.rsplit("=", 1))
    raise ValueError(ErrorCode.PRONUNCIATION_IMPORT_LINE_INVALID)


def _parse(text: str) -> _ParseResult:
    result = _ParseResult()
    first_row = True
    for number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            fields = [_unquote_formula_guard(f.strip()) for f in _split_line(raw)]
            if first_row and fields[0].lower() in _HEADER_NAMES:
                first_row = False
                continue
            first_row = False
            if len(fields) < 2 or len(fields) > 4:
                raise ValueError(ErrorCode.PRONUNCIATION_IMPORT_LINE_INVALID)
            spoken = fields[1]
            use_spell_out = spoken.lower() == SPELL_OUT_MARKER
            term, spoken = clean_rule(fields[0], "" if use_spell_out else spoken, use_spell_out)
            whole_word = _parse_flag(fields[2]) if len(fields) > 2 and fields[2] else True
            case_sensitive = _parse_flag(fields[3]) if len(fields) > 3 else False
        except ValueError as exc:
            result.errors.append(PronunciationImportLineError(line=number, content=raw[:200], error=str(exc)))
            continue
        # A term listed twice in one file: the later line wins, like editing it twice would.
        result.rows[term] = _ParsedLine(number, term, spoken, whole_word, case_sensitive)
    return result


def import_text(
    session: Session, user_id: str, language: str, text: str, *, overwrite: bool, dry_run: bool
) -> PronunciationImportResult:
    """Add (and with `overwrite`, update) entries from pasted text or an uploaded file.

    Invalid lines are reported, not fatal — the rest still imports. Going over the entry limit is
    fatal, though: a half-imported list would be harder to make sense of than none.
    """
    parsed = _parse(text)
    existing = {e.term: e for e in list_entries(session, user_id, language)}
    added = updated = skipped = 0
    to_add: list[PronunciationEntry] = []
    for row in parsed.rows.values():
        entry = existing.get(row.term)
        if entry is None:
            to_add.append(
                PronunciationEntry(
                    user_id=user_id,
                    language=language,
                    term=row.term,
                    spoken=row.spoken,
                    whole_word=row.whole_word,
                    case_sensitive=row.case_sensitive,
                )
            )
            added += 1
        elif overwrite and (entry.spoken, entry.whole_word, entry.case_sensitive) != (
            row.spoken,
            row.whole_word,
            row.case_sensitive,
        ):
            if not dry_run:
                entry.spoken, entry.whole_word, entry.case_sensitive = row.spoken, row.whole_word, row.case_sensitive
                entry.source_pack = None
                session.add(entry)
            updated += 1
        else:
            skipped += 1
    if len(existing) + added > MAX_ENTRIES_PER_LANGUAGE:
        session.rollback()
        raise LimitReached()
    if dry_run:
        session.rollback()
    else:
        session.add_all(to_add)
        session.commit()
    return PronunciationImportResult(added=added, updated=updated, skipped=skipped, errors=parsed.errors)


def _csv_safe(value: str) -> str:
    return "'" + value if value and value[0] in _CSV_FORMULA_TRIGGERS else value


def export_csv(session: Session, user_id: str, language: str) -> str:
    """The word list for `language` as semicolon-separated CSV, in the format import_text reads."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(_EXPORT_HEADER)
    for entry in list_entries(session, user_id, language):
        writer.writerow(
            [
                _csv_safe(entry.term),
                _csv_safe(entry.spoken),
                "1" if entry.whole_word else "0",
                "1" if entry.case_sensitive else "0",
            ]
        )
    return buffer.getvalue()
