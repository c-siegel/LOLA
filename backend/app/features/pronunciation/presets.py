"""
Pronunciation Preset Packs

Ready-made word lists for a subject (presets/<id>.<language>.json, e.g. physics.de.json) that a
teacher can copy into their own list with one click and then edit like any entry of their own.
The pack files are read-only and versioned; applying one never touches a term the teacher already
has, and removing it only takes the entries it added that the teacher hasn't edited since.

Pack file format:
    {"id": "physics", "language": "de", "version": 1, "caseSensitive": true,
     "entries": [{"term": "{number} m", "spoken": "{number} Meter"},
                 {"term": "Δ", "spoken": "Delta", "wholeWord": false}, ...]}
`caseSensitive`/`wholeWord` at the top are the defaults for every entry; an entry may override them.
The pack's display name and description live in the frontend locales
(pronunciation.presets.<id>), like every other user-visible text.

How to use:
    from app.features.pronunciation import presets

    presets.apply_pack(session, user_id, "physics", "de")
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from normt2s import Rule
from sqlalchemy import delete, func
from sqlmodel import Session, select

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.features.pronunciation import service
from app.features.pronunciation.models import PronunciationEntry
from app.features.pronunciation.schemas import clean_rule

_PRESET_DIR = Path(__file__).parent / "presets"


class PresetNotFound(DomainError):
    status_code = 404
    detail = ErrorCode.PRONUNCIATION_PRESET_NOT_FOUND


@dataclass(frozen=True)
class PresetPack:
    id: str
    language: str
    version: int
    entries: tuple[Rule, ...]


def _load(path: Path) -> PresetPack:
    data = json.loads(path.read_text(encoding="utf-8"))
    default_case = bool(data.get("caseSensitive", False))
    default_whole = bool(data.get("wholeWord", True))
    entries = []
    for item in data["entries"]:
        # Same validation as a teacher's own entry — a broken pack fails loudly at load (and in
        # the tests), not silently when someone applies it.
        term, spoken = clean_rule(item["term"], item["spoken"])
        entries.append(
            Rule(
                term,
                spoken,
                whole_word=bool(item.get("wholeWord", default_whole)),
                case_sensitive=bool(item.get("caseSensitive", default_case)),
            )
        )
    return PresetPack(data["id"], data["language"], int(data["version"]), tuple(entries))


@lru_cache(maxsize=1)
def all_packs() -> tuple[PresetPack, ...]:
    """Every pack shipped with the app, sorted by id and language."""
    packs = [_load(path) for path in sorted(_PRESET_DIR.glob("*.json"))]
    return tuple(sorted(packs, key=lambda p: (p.id, p.language)))


def get_pack(pack_id: str, language: str) -> PresetPack:
    for pack in all_packs():
        if pack.id == pack_id and pack.language == language:
            return pack
    raise PresetNotFound()


def applied_count(session: Session, user_id: str, pack_id: str, language: str) -> int:
    """How many of the teacher's entries (still) come from this pack."""
    return session.exec(
        select(func.count())
        .select_from(PronunciationEntry)
        .where(PronunciationEntry.user_id == user_id)
        .where(PronunciationEntry.language == language)
        .where(PronunciationEntry.source_pack == pack_id)
    ).one()


def apply_pack(session: Session, user_id: str, pack_id: str, language: str) -> tuple[int, int]:
    """Copy the pack's entries into the teacher's list; returns (added, skipped).

    A term the teacher already has is skipped — their own version always wins. Applying a pack
    twice is therefore harmless.
    """
    pack = get_pack(pack_id, language)
    existing = {e.term for e in service.list_entries(session, user_id, language)}
    new = [rule for rule in pack.entries if rule.term not in existing]
    if len(existing) + len(new) > service.MAX_ENTRIES_PER_LANGUAGE:
        raise service.LimitReached()
    session.add_all(
        PronunciationEntry(
            user_id=user_id,
            language=language,
            term=rule.term,
            spoken=rule.spoken,
            whole_word=rule.whole_word,
            case_sensitive=rule.case_sensitive,
            source_pack=pack.id,
        )
        for rule in new
    )
    session.commit()
    return len(new), len(pack.entries) - len(new)


def remove_pack(session: Session, user_id: str, pack_id: str, language: str) -> int:
    """Delete the entries this pack added and the teacher hasn't edited since; returns how many."""
    get_pack(pack_id, language)
    result = session.execute(
        delete(PronunciationEntry)
        .where(PronunciationEntry.user_id == user_id)
        .where(PronunciationEntry.language == language)
        .where(PronunciationEntry.source_pack == pack_id)
    )
    session.commit()
    return result.rowcount
