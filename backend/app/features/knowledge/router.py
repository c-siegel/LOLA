"""
Knowledge Base Routes

A teacher's knowledge bases (RAG): create one, upload documents into it, watch them get indexed,
try a search, delete. Projects attach knowledge bases through the normal project update (see
features/projects/service.py). Every route answers 404 KNOWLEDGE_DISABLED unless the deployment
runs the knowledge service (Settings.rag_enabled) — except /providers/rag-status, which tells the
dashboard whether to show any of this.

Uploads are checked here first (size, type, quotas, rate limit, upload_checks.py), then streamed
to the knowledge service, which checks again and parses in a sandbox. See docs/rag-plan.md §6.
"""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlmodel import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_session
from app.core.error_codes import ErrorCode
from app.core.ownership import get_owned
from app.core.rate_limit import enforce_knowledge_upload_rate_limit
from app.features.knowledge import bibtex, metadata, rag_client, service, sources
from app.features.knowledge.limits import current_limits
from app.features.knowledge.models import KnowledgeBase, KnowledgeDocument
from app.features.knowledge.schemas import (
    BibEntryOut,
    BibImportOut,
    SourceMetadata,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    KnowledgeDocumentOut,
    KnowledgeLimitsOut,
    KnowledgePassageOut,
    KnowledgeRetryIn,
    KnowledgeSearchIn,
    KnowledgeStatusOut,
)
from app.features.knowledge.service import KnowledgeError
from app.features.users.models import User

router = APIRouter(tags=["knowledge"])


def _enabled() -> None:
    service.require_enabled()


def _kb_out(session: Session, kb: KnowledgeBase, stats: dict[str, tuple[int, int]]) -> KnowledgeBaseOut:
    count, size = stats.get(kb.id, (0, 0))
    return KnowledgeBaseOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        embedding_mode=kb.embedding_mode,
        embedding_model=kb.embedding_model,
        embedding_api_key_id=kb.embedding_api_key_id,
        embedding_available=kb.embedding_mode == "local" or kb.embedding_api_key_id is not None,
        document_count=count,
        size_bytes=size,
        used_by_projects=len(service.projects_using(session, kb.user_id, kb.id)),
        created_at=kb.created_at,
    )


def _documents_out(session: Session, documents: list[KnowledgeDocument]) -> list[KnowledgeDocumentOut]:
    meta = sources.document_metadata(session, documents)
    out = []
    for document in documents:
        effective = meta[document.id]
        out.append(
            KnowledgeDocumentOut.model_validate(
                {
                    **document.model_dump(),
                    "metadata": {**effective, "cite": metadata.citation_text(effective)},
                    "own_metadata": metadata.manual_fields(document),
                }
            )
        )
    return out


def _document_out(session: Session, document: KnowledgeDocument) -> KnowledgeDocumentOut:
    return _documents_out(session, [document])[0]


@router.get("/providers/rag-status", response_model=KnowledgeStatusOut)
def rag_status(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """Whether this deployment offers knowledge bases, and the limits the upload form shows."""
    if not settings.rag_enabled:
        return KnowledgeStatusOut(available=False)
    limits = current_limits(session)
    out = KnowledgeStatusOut(
        available=True,
        limits=KnowledgeLimitsOut(
            max_upload_mb=limits.max_upload_mb,
            max_pages=limits.max_pages,
            max_documents_per_kb=limits.max_documents_per_kb,
            max_kb_per_user=limits.max_kb_per_user,
            user_quota_mb=limits.user_quota_mb,
        ),
        usage_bytes=service.user_usage_bytes(session, current_user.id),
    )
    try:
        capabilities = rag_client.capabilities()
    except (rag_client.RagUnavailable, rag_client.RagRejected):
        return out
    out.reachable = True
    out.local_model = capabilities.get("local_model")
    out.docling_available = bool(capabilities.get("docling_available"))
    out.file_types = list(capabilities.get("file_types") or [])
    return out


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseOut], dependencies=[Depends(_enabled)])
def list_knowledge_bases(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    kbs = service.list_knowledge_bases(session, current_user.id)
    stats = service.document_stats(session, [kb.id for kb in kbs])
    return [_kb_out(session, kb, stats) for kb in kbs]


@router.post("/knowledge-bases", response_model=KnowledgeBaseOut, status_code=201, dependencies=[Depends(_enabled)])
def create_knowledge_base(
    data: KnowledgeBaseCreate, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    kb = service.create_knowledge_base(session, current_user.id, data.name, data.description, data.embedding_api_key_id)
    return _kb_out(session, kb, {})


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut, dependencies=[Depends(_enabled)])
def update_knowledge_base(
    kb_id: str,
    data: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    kb = service.update_knowledge_base(session, kb, data.name, data.description)
    return _kb_out(session, kb, service.document_stats(session, [kb.id]))


@router.delete("/knowledge-bases/{kb_id}", status_code=204, dependencies=[Depends(_enabled)])
def delete_knowledge_base(
    kb_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    """Delete a knowledge base with its documents — including their indexed text in the
    knowledge service. Projects that used it simply lose the link."""
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    service.delete_knowledge_base(session, kb)


@router.get(
    "/knowledge-bases/{kb_id}/documents", response_model=list[KnowledgeDocumentOut], dependencies=[Depends(_enabled)]
)
def list_documents(kb_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """The KB's documents with their current indexing status (polled while any is in progress)."""
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    return _documents_out(session, service.list_documents(session, kb))


@router.post(
    "/knowledge-bases/{kb_id}/documents",
    response_model=KnowledgeDocumentOut,
    status_code=202,
    dependencies=[Depends(_enabled)],
)
def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    parser: str = Form("light"),
    # The teacher confirms they may make this material available to everyone with the project's
    # link — students can ask the avatar to quote it (docs/rag-plan.md §6.6).
    consent: bool = Form(False),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Upload one document. Accepted documents are indexed in the background (status "queued").

    A plain `def`: hashing and forwarding a file of up to the size limit shouldn't block the
    event loop.
    """
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    if not consent:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_UPLOAD_CONSENT_REQUIRED)
    limits = current_limits(session)
    enforce_knowledge_upload_rate_limit(current_user.id, limits.upload_rate_per_10min)
    max_bytes = limits.max_upload_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise KnowledgeError(ErrorCode.KNOWLEDGE_FILE_TOO_LARGE, status_code=413)
    document = service.upload_document(
        session,
        current_user.id,
        kb,
        file.filename or "document",
        data,
        parser="docling" if parser == "docling" else "light",
        limits=limits,
    )
    return _document_out(session, document)


@router.post(
    "/knowledge-documents/{document_id}/retry",
    response_model=KnowledgeDocumentOut,
    status_code=202,
    dependencies=[Depends(_enabled)],
)
def retry_document(
    document_id: str,
    data: KnowledgeRetryIn,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Index a failed document again — no re-upload needed while the knowledge service still has
    the original (see KnowledgeDocumentOut.retryable)."""
    document = get_owned(
        session, KnowledgeDocument, document_id, current_user.id, ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND
    )
    kb = get_owned(
        session, KnowledgeBase, document.knowledge_base_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND
    )
    limits = current_limits(session)
    # Indexing again costs the same as an upload, so it counts against the same limit.
    enforce_knowledge_upload_rate_limit(current_user.id, limits.upload_rate_per_10min)
    return _document_out(session, service.retry_document(session, kb, document, parser=data.parser, limits=limits))


@router.delete("/knowledge-documents/{document_id}", status_code=204, dependencies=[Depends(_enabled)])
def delete_document(
    document_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    document = get_owned(
        session, KnowledgeDocument, document_id, current_user.id, ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND
    )
    service.delete_document(session, document)


@router.post(
    "/knowledge-bases/{kb_id}/search", response_model=list[KnowledgePassageOut], dependencies=[Depends(_enabled)]
)
def search_knowledge_base(
    kb_id: str,
    data: KnowledgeSearchIn,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """The passages a student's question would retrieve — lets the teacher check the KB."""
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    passages = service.search(session, kb, data.query)
    return [KnowledgePassageOut(**p) for p in passages]


@router.patch(
    "/knowledge-documents/{document_id}/metadata", response_model=KnowledgeDocumentOut, dependencies=[Depends(_enabled)]
)
def update_document_metadata(
    document_id: str,
    data: SourceMetadata,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Replace the teacher's own source details for a document. Fields left empty fall back to
    the linked bibliography entry and the file's header. Never re-indexes: metadata only goes
    into the prompt."""
    document = get_owned(
        session, KnowledgeDocument, document_id, current_user.id, ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND
    )
    return _document_out(session, sources.update_metadata(session, document, data.model_dump()))


@router.post(
    "/knowledge-bases/{kb_id}/bibliography", response_model=BibImportOut, dependencies=[Depends(_enabled)]
)
def import_bibliography(
    kb_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Replace the knowledge base's bibliography with a BibTeX file and link documents to it."""
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    enforce_knowledge_upload_rate_limit(current_user.id, current_limits(session).upload_rate_per_10min)
    data = file.file.read(bibtex.MAX_BIB_BYTES + 1)
    try:
        result = sources.import_bibliography(session, kb, data)
    except sources.BibliographyInvalid as exc:
        status_code = 413 if str(exc) == ErrorCode.KNOWLEDGE_BIB_TOO_LARGE else 400
        raise KnowledgeError(str(exc), status_code=status_code) from exc
    return BibImportOut(**result)


@router.get(
    "/knowledge-bases/{kb_id}/bibliography", response_model=list[BibEntryOut], dependencies=[Depends(_enabled)]
)
def list_bibliography(kb_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    return [BibEntryOut(**entry) for entry in sources.list_bibliography(session, kb)]


@router.delete("/knowledge-bases/{kb_id}/bibliography", status_code=204, dependencies=[Depends(_enabled)])
def delete_bibliography(
    kb_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    kb = get_owned(session, KnowledgeBase, kb_id, current_user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    sources.delete_bibliography(session, kb)
