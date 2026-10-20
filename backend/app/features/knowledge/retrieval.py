"""
Retrieval for a Chat Turn

Looks up the passages of a project's knowledge bases that best match the student's message,
right before the LLM call (see features/chat/pipeline.py). Two phases, like the rest of the chat
pipeline:

- prepare_knowledge(): while the request's DB session is open, snapshot which KBs the project
  uses, how to embed for them (decrypting an API key if needed) and the documents' display
  names — plain values only.
- retrieve(): per turn, without touching the DB. Bounded by RAG_QUERY_TIMEOUT_MS; any failure
  returns no passages, so a slow or stopped knowledge service makes the avatar answer as if no
  material were attached — it never breaks the turn.

How to use:
    knowledge = prepare_knowledge(session, project, llm_provider)   # None if the project uses none
    passages = retrieve(knowledge, message, history)
"""

import logging
import time
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.core.config import settings
from app.core.ownership import find_owned
from app.core.providers import GWDG_ARCANA_PROVIDER
from app.features.knowledge import rag_client
from app.features.knowledge.models import KnowledgeBase, KnowledgeDocument
from app.features.knowledge.metadata import citation_text
from app.features.knowledge.service import KnowledgeError, embedding_config
from app.features.knowledge.sources import document_metadata
from app.features.projects.models import Project

logger = logging.getLogger(__name__)

# A follow-up like "und das zweite?" says nothing on its own; with the previous student message in
# front, it retrieves what the conversation is about. Cheaper than asking an LLM to rewrite it.
_SHORT_MESSAGE_WORDS = 8
_MAX_QUERY_CHARS = 2000


@dataclass(frozen=True)
class Passage:
    chunk_id: int
    document_id: str
    knowledge_base_id: str
    text: str
    page: int | None
    heading: str | None
    score: float
    filename: str | None = None
    # From the document's source metadata (features/knowledge/metadata.py), for the prompt label.
    cite: str | None = None
    source_type: str | None = None
    priority: str | None = None


@dataclass(frozen=True)
class KnowledgeContext:
    mode: str  # "supplement" | "strict"
    top_k: int
    # One query per distinct embedding config — KBs built with different models can't be
    # searched together: (knowledge_base_ids, embedding config) pairs. Empty in strict mode when
    # nothing is searchable, which build_messages still has to tell the model.
    groups: tuple[tuple[tuple[str, ...], dict], ...]
    filenames: dict[str, str]
    # document_id → {"cite", "source_type", "priority"}, only for documents that have any.
    sources: dict[str, dict] = field(default_factory=dict)


def prepare_knowledge(session: Session, project: Project, llm_provider: str | None) -> KnowledgeContext | None:
    """None if the instance, the project or its LLM (Arcana brings its own RAG) uses no KB."""
    if not settings.rag_enabled or project.knowledge_mode not in ("supplement", "strict"):
        return None
    if llm_provider == GWDG_ARCANA_PROVIDER:
        return None
    groups: dict[str, tuple[list[str], dict]] = {}
    for kb_id in project.knowledge_base_ids:
        kb = find_owned(session, KnowledgeBase, kb_id, project.user_id)
        if kb is None:
            continue
        try:
            config = embedding_config(session, kb)
        except KnowledgeError:
            continue  # its embedding key was deleted; the dashboard already flags this KB
        signature = f"{config['mode']}|{config['model']}|{kb.embedding_api_key_id or ''}"
        groups.setdefault(signature, ([], config))[0].append(kb.id)
    if not groups and project.knowledge_mode != "strict":
        return None
    kb_ids = [kb_id for ids, _ in groups.values() for kb_id in ids]
    documents = (
        list(session.exec(select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id.in_(kb_ids))))
        if kb_ids
        else []
    )
    filenames = {d.id: d.filename for d in documents}
    source_info = {}
    for document_id, meta in document_metadata(session, documents).items():
        info = {"cite": citation_text(meta), "source_type": meta.get("source_type"), "priority": meta.get("priority")}
        if any(info.values()):
            source_info[document_id] = info
    return KnowledgeContext(
        mode=project.knowledge_mode,
        top_k=project.knowledge_top_k,
        groups=tuple((tuple(ids), config) for ids, config in groups.values()),
        filenames=filenames,
        sources=source_info,
    )


def query_text(message: str, history: list[dict] | None) -> str:
    """The text to search with: the message, plus the previous student message if it's short."""
    text = message.strip()
    if len(text.split()) < _SHORT_MESSAGE_WORDS and history:
        previous = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
        if previous:
            text = f"{previous.strip()}\n{text}"
    return text[:_MAX_QUERY_CHARS]


def retrieve(knowledge: KnowledgeContext | None, message: str, history: list[dict] | None) -> list[Passage]:
    if knowledge is None or not knowledge.groups:
        return []
    text = query_text(message, history)
    deadline = time.monotonic() + settings.rag_query_timeout_ms / 1000
    found: list[Passage] = []
    for kb_ids, config in knowledge.groups:
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            logger.warning("Knowledge retrieval ran out of time; answering without the remaining knowledge bases")
            break
        try:
            results = rag_client.query(list(kb_ids), text, knowledge.top_k, config, timeout=remaining)
        except (rag_client.RagUnavailable, rag_client.RagRejected) as exc:
            # No message text in the log: it's a student's words.
            logger.warning("Knowledge retrieval failed (%s); answering without passages", exc)
            continue
        found.extend(
            Passage(
                chunk_id=r["chunk_id"],
                document_id=r["document_id"],
                knowledge_base_id=r["knowledge_base_id"],
                text=r["text"],
                page=r.get("page"),
                heading=r.get("heading"),
                score=r.get("score", 0.0),
                filename=knowledge.filenames.get(r["document_id"]),
                **knowledge.sources.get(r["document_id"], {}),
            )
            for r in results
        )
    # With several groups each returns its own top_k; keep the best overall. RRF scores are on
    # the same scale for every query, so they compare across groups.
    return sorted(found, key=lambda p: p.score, reverse=True)[: knowledge.top_k]


def sources_for_transcript(passages: list[Passage]) -> list[dict]:
    """What's saved with an assistant message: IDs and page only, never the text, so deleting a
    document doesn't leave copies of it in saved conversations."""
    return [
        {"documentId": p.document_id, "chunkId": p.chunk_id, "page": p.page, "score": round(p.score, 4)}
        for p in passages
    ]
