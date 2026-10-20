"""
Conversation Export (CSV/ZIP)

Renders saved conversations for download from the analytics table: one conversation as a plain
.csv, several bundled into a .zip with one .csv each.
"""

import csv
import io
import json
import re
import zipfile

from app.core.csv import csv_safe
from app.features.chat.models import Conversation
from app.features.projects.models import Project


def _slugify(value: str) -> str:
    """ASCII-only filename fragment — German umlauts spelled out instead of stripped, everything
    else collapsed to hyphens so the result is always safe as a filename and an HTTP header."""
    for umlaut, replacement in {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}.items():
        value = value.replace(umlaut, replacement).replace(umlaut.upper(), replacement.capitalize())
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return value or "gespraech"


def conversation_export_filename(conversation: Conversation, project: Project) -> str:
    """Filename for one conversation's exported CSV, used both for a standalone download and as
    a ZIP entry name when several conversations are exported at once."""
    date_part = conversation.started_at.strftime("%Y%m%d-%H%M")
    who = conversation.visitor_name or conversation.id[:8]
    return f"{_slugify(project.title)}_{date_part}_{_slugify(who)}.csv"


def _sources_cell(sources: list[dict], document_names: dict[str, str]) -> str:
    """ "Skript.pdf S. 3; Arbeitsblatt.docx" — the knowledge-base passages a reply was given."""
    parts = []
    for source in sources:
        name = document_names.get(source.get("documentId"), "(gelöschtes Dokument)")
        parts.append(f"{name} S. {source['page']}" if source.get("page") else name)
    return "; ".join(dict.fromkeys(parts))


def _motions_cell(motions: list[dict]) -> str:
    """ "wave_right@0; nod_yes@42" — the gestures a reply used, each with its character position
    in the reply (see features/chat/motion.py)."""
    return "; ".join(f"{motion['name']}@{motion['offset']}" for motion in motions)


def _checks_cell(checks: list[dict], step_titles: dict[str, str]) -> str:
    """ "✓ Begrüßung; ✗ Quiz" — the conversation-plan steps a reply ticked off (✓) or took back
    (✗), see features/chat/checklist.py."""
    return "; ".join(
        f"{'✓' if check['done'] else '✗'} {step_titles.get(check['id'], '(gelöschter Schritt)')}" for check in checks
    )


def build_conversation_csv(
    conversation: Conversation, project: Project, document_names: dict[str, str] | None = None
) -> str:
    """Render one saved conversation as a CSV: a short metadata header (project, LLM model,
    visitor, start time), then one row per message with a timestamp column plus one column per
    speaker (Avatar/Schüler:in) — each row fills only the column of whichever side sent that
    message, so the two columns read top-to-bottom as each side's messages in order.

    Messages saved before per-message timestamps existed (see
    app/features/chat/schemas.py::ChatHistoryEntry) leave the "Zeitpunkt" cell blank for that row.

    A conversation with a knowledge base gets a fourth "Quellen" column (the documents each reply
    was given, see features/knowledge/), and one in which the avatar used gestures a "Gesten"
    column (see features/chat/motion.py), and one in which it ticked conversation-plan steps a
    "Plan" column (see features/chat/checklist.py); every other export keeps its three columns
    unchanged.
    """
    messages = json.loads(conversation.messages_json)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Projekt", csv_safe(project.title)])
    writer.writerow(["LLM-Modell", csv_safe(project.llm_model or "")])
    writer.writerow(["Name/ID", csv_safe(conversation.visitor_name or "")])
    writer.writerow(["Gestartet", conversation.started_at.isoformat()])
    writer.writerow([])
    with_sources = any("sources" in message for message in messages)
    with_motions = any("motions" in message for message in messages)
    with_checks = any("checks" in message for message in messages)
    step_titles = {item["id"]: item["title"] for item in project.checklist_items}
    writer.writerow(
        ["Zeitpunkt", "Avatar", "Schüler:in"]
        + (["Quellen"] if with_sources else [])
        + (["Gesten"] if with_motions else [])
        + (["Plan"] if with_checks else [])
    )
    for message in messages:
        timestamp = message.get("timestamp") or ""
        content = csv_safe(message.get("content", ""))
        if message.get("role") == "assistant":
            row = [timestamp, content, ""]
            if with_sources:
                row.append(csv_safe(_sources_cell(message.get("sources") or [], document_names or {})))
            if with_motions:
                row.append(_motions_cell(message.get("motions") or []))
            if with_checks:
                row.append(csv_safe(_checks_cell(message.get("checks") or [], step_titles)))
        else:
            row = [timestamp, "", content] + [""] * (with_sources + with_motions + with_checks)
        writer.writerow(row)
    return buffer.getvalue()


def build_export(
    rows: list[tuple[Conversation, Project]], document_names: dict[str, str] | None = None
) -> tuple[bytes | str, str, str]:
    """The download for one or more conversations: (content, media type, filename).

    A single conversation comes back as a plain .csv; more than one is bundled into a .zip (one
    .csv per conversation) — browsers don't let a page trigger several file downloads at once
    without extra prompts, so a bulk export has to be one file.
    """
    if len(rows) == 1:
        conversation, project = rows[0]
        return build_conversation_csv(conversation, project, document_names), "text/csv", conversation_export_filename(conversation, project)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # Two different conversations can otherwise land on the same "<project>_<time>_<name>.csv"
        # name (e.g. two anonymous visitors starting in the same minute) — de-duplicated with a
        # numeric suffix so neither entry silently overwrites the other inside the archive.
        used_names: dict[str, int] = {}
        for conversation, project in rows:
            name = conversation_export_filename(conversation, project)
            if name in used_names:
                used_names[name] += 1
                stem, _, ext = name.rpartition(".")
                name = f"{stem}-{used_names[name]}.{ext}"
            else:
                used_names[name] = 0
            archive.writestr(name, build_conversation_csv(conversation, project, document_names))
    return buffer.getvalue(), "application/zip", "lola-gespraeche.zip"
