"""
Knowledge Bases

The logic behind the knowledge routes (features/knowledge/router.py): a teacher's knowledge
bases and documents, the quotas and limits around them, keeping document status in step with the
knowledge service, and deletes that really delete — the derived text in the knowledge service
goes too, or is queued for retry if the service is down (RagPendingDeletion).

The original files are never stored here: an upload goes straight from the request to the
knowledge service, which deletes it once it's parsed.

How to use:
    from app.features.knowledge import service

    kb = service.create_knowledge_base(session, user_id, "Biologie", None, None)
    document = service.upload_document(session, user_id, kb, "Skript.pdf", data, parser="light")
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.core.providers import KEY_TYPE_EMBEDDING, build_embedding_model_string
from app.features.api_keys.crypto import reveal_api_key
from app.features.api_keys.models import UserApiKey
from app.features.api_keys.resolve import effective_api_base, get_owned_key_of_type
from app.features.evaluation import cleanup as evaluation_cleanup
from app.features.knowledge import rag_client, sources
from app.features.knowledge.limits import KnowledgeLimits, current_limits
from app.features.knowledge.models import KnowledgeBase, KnowledgeBibEntry, KnowledgeDocument, RagPendingDeletion
from app.features.knowledge.upload_checks import detect_file_type, safe_display_name
from app.features.projects.models import Project

logger = logging.getLogger(__name__)

# Statuses that never change on their own. "failed" isn't one: a failed document's original
# expires in the knowledge service, which turns its retry button off.
_FINAL_STATUSES = {"ready"}
_MAX_NAME_LENGTH = 100
_MAX_DESCRIPTION_LENGTH = 500


class KnowledgeDisabled(DomainError):
    status_code = 404
    detail = ErrorCode.KNOWLEDGE_DISABLED


class KnowledgeServiceUnavailable(DomainError):
    status_code = 503
    detail = ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE


class KnowledgeError(DomainError):
    """A refused action with its own code (limit reached, duplicate, rejected file, …)."""

    status_code = 400


def require_enabled() -> None:
    if not settings.rag_enabled:
        raise KnowledgeDisabled()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Knowledge bases -------------------------------------------------------------------------


def list_knowledge_bases(session: Session, user_id: str) -> list[KnowledgeBase]:
    return list(
        session.exec(
            select(KnowledgeBase).where(KnowledgeBase.user_id == user_id).order_by(KnowledgeBase.created_at)
        )
    )


def document_stats(session: Session, kb_ids: list[str]) -> dict[str, tuple[int, int]]:
    """{kb_id: (document count, total bytes)} in one query."""
    if not kb_ids:
        return {}
    rows = session.exec(
        select(KnowledgeDocument.knowledge_base_id, func.count(KnowledgeDocument.id), func.sum(KnowledgeDocument.size_bytes))
        .where(KnowledgeDocument.knowledge_base_id.in_(kb_ids))
        .group_by(KnowledgeDocument.knowledge_base_id)
    ).all()
    return {kb_id: (count, int(total or 0)) for kb_id, count, total in rows}


def projects_using(session: Session, user_id: str, kb_id: str) -> list[Project]:
    return [p for p in session.exec(select(Project).where(Project.user_id == user_id)) if kb_id in p.knowledge_base_ids]


def user_usage_bytes(session: Session, user_id: str) -> int:
    total = session.exec(
        select(func.sum(KnowledgeDocument.size_bytes)).where(KnowledgeDocument.user_id == user_id)
    ).one()
    return int(total or 0)


def _clean_name(name: str) -> str:
    name = (name or "").strip()[:_MAX_NAME_LENGTH]
    if not name:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_BASE_NAME_REQUIRED)
    return name


def create_knowledge_base(
    session: Session, user_id: str, name: str, description: str | None, embedding_api_key_id: str | None
) -> KnowledgeBase:
    limits = current_limits(session)
    count = session.exec(select(func.count(KnowledgeBase.id)).where(KnowledgeBase.user_id == user_id)).one()
    if count >= limits.max_kb_per_user:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_BASE_LIMIT_REACHED)

    if embedding_api_key_id:
        key = get_owned_key_of_type(session, user_id, embedding_api_key_id, KEY_TYPE_EMBEDDING)
        if key is None:
            raise KnowledgeError(ErrorCode.UNKNOWN_API_KEY)
        if not key.model_id:
            raise KnowledgeError(ErrorCode.EMBEDDING_KEY_MISSING_MODEL)
        mode, model = "api", build_embedding_model_string(key.provider, key.model_id)
    else:
        try:
            mode, model = "local", rag_client.capabilities()["local_model"]
        except rag_client.RagUnavailable as exc:
            raise KnowledgeServiceUnavailable() from exc

    kb = KnowledgeBase(
        user_id=user_id,
        name=_clean_name(name),
        description=(description or "").strip()[:_MAX_DESCRIPTION_LENGTH] or None,
        embedding_mode=mode,
        embedding_api_key_id=embedding_api_key_id if mode == "api" else None,
        embedding_model=model,
    )
    session.add(kb)
    session.commit()
    session.refresh(kb)
    return kb


def update_knowledge_base(session: Session, kb: KnowledgeBase, name: str | None, description: str | None) -> KnowledgeBase:
    # Only the labels: the embedding model is fixed for the KB's lifetime (see models.py).
    if name is not None:
        kb.name = _clean_name(name)
    if description is not None:
        kb.description = description.strip()[:_MAX_DESCRIPTION_LENGTH] or None
    session.add(kb)
    session.commit()
    session.refresh(kb)
    return kb


def _queue_or_delete_remote(session: Session, kind: str, target_id: str) -> None:
    try:
        if kind == "document":
            rag_client.delete_document(target_id)
        else:
            rag_client.delete_knowledge_base(target_id)
    except (rag_client.RagUnavailable, rag_client.RagRejected):
        session.add(RagPendingDeletion(kind=kind, target_id=target_id))
        session.commit()


def delete_knowledge_base(session: Session, kb: KnowledgeBase) -> None:
    """Delete a KB with all its documents, test sets and evaluation runs; projects that used it
    just lose the link."""
    for project in projects_using(session, kb.user_id, kb.id):
        project.knowledge_base_ids_json = json.dumps([i for i in project.knowledge_base_ids if i != kb.id])
        session.add(project)
    session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb.id))
    session.execute(delete(KnowledgeBibEntry).where(KnowledgeBibEntry.knowledge_base_id == kb.id))
    # Test sets belong to the KB, and runs hold copies of its passages.
    evaluation_cleanup.delete_for_knowledge_base(session, kb.id)
    kb_id = kb.id
    session.delete(kb)
    session.commit()
    _queue_or_delete_remote(session, "knowledge_base", kb_id)


def delete_all_for_user(session: Session, user_id: str) -> None:
    """Account deletion: every KB of the user, in the backend and the knowledge service.
    Doesn't commit — runs inside features/users/account.py's single deletion transaction."""
    kb_ids = [kb.id for kb in list_knowledge_bases(session, user_id)]
    session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.user_id == user_id))
    session.execute(delete(KnowledgeBibEntry).where(KnowledgeBibEntry.user_id == user_id))
    session.execute(delete(KnowledgeBase).where(KnowledgeBase.user_id == user_id))
    # The remote deletes are queued rather than sent here: the account transaction hasn't
    # committed yet, and the periodic cleanup (app/tasks/knowledge_cleanup.py) sends them right after.
    for kb_id in kb_ids:
        session.add(RagPendingDeletion(kind="knowledge_base", target_id=kb_id))


def detach_embedding_key(session: Session, key_id: str) -> None:
    """A deleted embedding key leaves its KBs without credentials. They stay (the indexed text
    is still there) but can't be searched until re-created — shown in the dashboard."""
    for kb in session.exec(select(KnowledgeBase).where(KnowledgeBase.embedding_api_key_id == key_id)):
        kb.embedding_api_key_id = None
        session.add(kb)


# --- Embedding configuration -------------------------------------------------------------------


def embedding_config(session: Session, kb: KnowledgeBase) -> dict:
    """What the knowledge service needs to embed for `kb` — including the decrypted key for an
    API model, which is only ever sent to the service per request, never stored there."""
    if kb.embedding_mode == "local":
        return {"mode": "local", "model": kb.embedding_model}
    key = (
        get_owned_key_of_type(session, kb.user_id, kb.embedding_api_key_id, KEY_TYPE_EMBEDDING)
        if kb.embedding_api_key_id
        else None
    )
    if key is None:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_EMBEDDING_KEY_INVALID)
    return {
        "mode": "api",
        # The model the KB was built with, not whatever the key says today: a different model
        # would make every stored vector meaningless.
        "model": kb.embedding_model,
        "api_key": reveal_api_key(key.encrypted_api_key) or None,
        "api_base": effective_api_base(key),
    }


def test_embedding_key(key: UserApiKey) -> None:
    """The API dashboard's "Test" for an embedding key: one real embedding call, made by the
    knowledge service (this backend has no embedding code of its own)."""
    require_enabled()
    if not key.model_id:
        raise ValueError(ErrorCode.EMBEDDING_KEY_MISSING_MODEL)
    config = {
        "mode": "api",
        "model": build_embedding_model_string(key.provider, key.model_id),
        "api_key": reveal_api_key(key.encrypted_api_key) or None,
        "api_base": effective_api_base(key),
    }
    try:
        rag_client.embedding_test(config)
    except rag_client.RagRejected as exc:
        raise ValueError(exc.code) from exc
    except rag_client.RagUnavailable as exc:
        raise ValueError(ErrorCode.KNOWLEDGE_SERVICE_UNAVAILABLE) from exc


# --- Documents -------------------------------------------------------------------------------


def list_documents(session: Session, kb: KnowledgeBase) -> list[KnowledgeDocument]:
    documents = list(
        session.exec(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.knowledge_base_id == kb.id)
            .order_by(KnowledgeDocument.created_at)
        )
    )
    refresh_statuses(session, documents)
    return documents


def refresh_statuses(session: Session, documents: list[KnowledgeDocument]) -> None:
    """Copy the knowledge service's status onto documents that aren't indexed yet.

    The dashboard polls the document list while anything is in progress, so this is the only
    sync needed — no callback from the service. A document the service doesn't know at all
    (its data volume was reset) is marked failed rather than left "processing" forever.
    """
    pending = [d for d in documents if d.status not in _FINAL_STATUSES]
    if not pending:
        return
    try:
        remote = {s["document_id"]: s for s in rag_client.document_statuses([d.id for d in pending])}
    except (rag_client.RagUnavailable, rag_client.RagRejected):
        return
    for document in pending:
        status = remote.get(document.id)
        if status is None:
            document.status, document.error_code = "failed", ErrorCode.KNOWLEDGE_INTERRUPTED
            document.retryable = False
        else:
            document.status = status["status"]
            document.error_code = rag_client.knowledge_code(status["error_code"]) if status.get("error_code") else None
            document.page_count = status.get("page_count")
            document.chunk_count = status.get("chunk_count")
            document.truncated = bool(status.get("truncated"))
            document.retryable = bool(status.get("retryable"))
            if document.status == "ready":
                sources.apply_header(session, document, status.get("metadata"))
        document.updated_at = _now()
        session.add(document)
    session.commit()


def upload_document(
    session: Session,
    user_id: str,
    kb: KnowledgeBase,
    filename: str,
    data: bytes,
    *,
    parser: str,
    limits: KnowledgeLimits,
) -> KnowledgeDocument:
    """Check, record and hand over one upload; indexing continues in the knowledge service."""
    display_name = safe_display_name(filename)
    file_type = detect_file_type(display_name, data)
    if parser == "docling" and file_type not in ("pdf", "docx"):
        parser = "light"

    count = session.exec(
        select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.knowledge_base_id == kb.id)
    ).one()
    if count >= limits.max_documents_per_kb:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_DOCUMENT_LIMIT_REACHED)
    if user_usage_bytes(session, user_id) + len(data) > limits.user_quota_mb * 1024 * 1024:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_QUOTA_EXCEEDED)
    digest = hashlib.sha256(data).hexdigest()
    duplicate = session.exec(
        select(KnowledgeDocument.id).where(KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.sha256 == digest)
    ).first()
    if duplicate:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_DOCUMENT_DUPLICATE)

    config = embedding_config(session, kb)
    document = KnowledgeDocument(
        knowledge_base_id=kb.id,
        user_id=user_id,
        filename=display_name,
        file_type=file_type,
        size_bytes=len(data),
        sha256=digest,
        parser=parser,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    meta = {
        "document_id": document.id,
        "knowledge_base_id": kb.id,
        "filename": display_name,
        "parser": parser,
        "embedding": config,
        "limits": limits.for_service(),
    }
    try:
        rag_client.upload_document(meta, display_name, data)
    except (rag_client.RagUnavailable, rag_client.RagRejected) as exc:
        session.delete(document)
        session.commit()
        if isinstance(exc, rag_client.RagRejected):
            raise KnowledgeError(exc.code) from exc
        raise KnowledgeServiceUnavailable() from exc
    return document


def retry_document(
    session: Session, kb: KnowledgeBase, document: KnowledgeDocument, *, parser: str, limits: KnowledgeLimits
) -> KnowledgeDocument:
    """Index a failed document again from the original the knowledge service kept, optionally
    with another parser (Docling for a scan without a text layer)."""
    if document.status != "failed":
        raise KnowledgeError(ErrorCode.KNOWLEDGE_RETRY_NOT_POSSIBLE, status_code=409)
    if parser == "docling" and document.file_type not in ("pdf", "docx"):
        parser = "light"
    try:
        rag_client.retry_document(document.id, parser, embedding_config(session, kb), limits.for_service())
    except rag_client.RagRejected as exc:
        if exc.code in (ErrorCode.KNOWLEDGE_RETRY_NOT_POSSIBLE, ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND):
            document.retryable = False
            session.add(document)
            session.commit()
            raise KnowledgeError(ErrorCode.KNOWLEDGE_RETRY_NOT_POSSIBLE, status_code=409) from exc
        raise KnowledgeError(exc.code) from exc
    except rag_client.RagUnavailable as exc:
        raise KnowledgeServiceUnavailable() from exc
    document.status, document.error_code, document.retryable = "queued", None, False
    document.parser = parser
    document.updated_at = _now()
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def delete_document(session: Session, document: KnowledgeDocument) -> None:
    document_id = document.id
    session.delete(document)
    session.commit()
    _queue_or_delete_remote(session, "document", document_id)


def retry_pending_deletions(session: Session) -> int:
    """Send queued deletes; returns how many went through. Called periodically."""
    done = 0
    for pending in session.exec(select(RagPendingDeletion).order_by(RagPendingDeletion.created_at)).all():
        try:
            if pending.kind == "document":
                rag_client.delete_document(pending.target_id)
            else:
                rag_client.delete_knowledge_base(pending.target_id)
        except rag_client.RagUnavailable:
            break  # still down; try again next round
        except rag_client.RagRejected:
            logger.warning("Knowledge service refused queued %s deletion %s", pending.kind, pending.target_id)
        session.delete(pending)
        done += 1
    session.commit()
    return done


# --- Search ----------------------------------------------------------------------------------


def search(session: Session, kb: KnowledgeBase, query: str, top_k: int = 5) -> list[dict]:
    """The teacher's test search on one KB: the passages a student's question would retrieve."""
    try:
        passages = rag_client.query(
            [kb.id], query, top_k, embedding_config(session, kb), timeout=settings.rag_request_timeout_seconds
        )
    except rag_client.RagRejected as exc:
        raise KnowledgeError(exc.code) from exc
    except rag_client.RagUnavailable as exc:
        raise KnowledgeServiceUnavailable() from exc
    names = document_names(session, [p["document_id"] for p in passages])
    return [{**p, "filename": names.get(p["document_id"])} for p in passages]


def document_names(session: Session, document_ids: list[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    rows = session.exec(
        select(KnowledgeDocument.id, KnowledgeDocument.filename).where(KnowledgeDocument.id.in_(set(document_ids)))
    ).all()
    return dict(rows)
