"""
Knowledge Base Request/Response Shapes

For features/knowledge/router.py. Nothing here ever carries a document's text except the teacher's
own test search, and nothing ever carries an API key.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.core.schema import CamelModel


class KnowledgeBaseCreate(CamelModel):
    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # None: the knowledge service's local model (the default). Otherwise one of the teacher's
    # embedding keys — then the documents' text is sent to that provider when indexed.
    embedding_api_key_id: str | None = None


class KnowledgeBaseUpdate(CamelModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class KnowledgeBaseOut(CamelModel):
    id: str
    name: str
    description: str | None
    embedding_mode: str
    embedding_model: str
    embedding_api_key_id: str | None
    # An API-embedded KB whose key was deleted can't be searched any more (see
    # service.detach_embedding_key) — the dashboard says so instead of failing silently.
    embedding_available: bool
    document_count: int
    size_bytes: int
    used_by_projects: int
    created_at: datetime


SourceType = Literal["script", "worksheet", "article", "book", "thesis", "report", "web", "transcript", "other"]
SourcePriority = Literal["primary", "secondary", "supplementary"]


class SourceMetadata(CamelModel):
    """A document's source metadata (features/knowledge/metadata.py). All optional."""

    title: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=1000, le=2100)
    container: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    citation: str | None = Field(default=None, max_length=500)
    source_type: SourceType | None = None
    priority: SourcePriority | None = None
    note: str | None = Field(default=None, max_length=300)
    bibtex_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_:\-./+]{1,100}$")


class SourceMetadataOut(SourceMetadata):
    # The citation the avatar uses: the explicit one, or one generated from the fields.
    cite: str | None = None


class KnowledgeDocumentOut(CamelModel):
    id: str
    knowledge_base_id: str
    filename: str
    file_type: str
    size_bytes: int
    parser: str
    status: Literal["queued", "processing", "ready", "failed"]
    error_code: str | None
    page_count: int | None
    chunk_count: int | None
    truncated: bool
    retryable: bool
    # Combined from the teacher's entries, the linked bibliography entry and the file's header.
    metadata: SourceMetadataOut
    # Only what the teacher entered — the form shows the rest as inherited placeholders.
    own_metadata: SourceMetadata
    created_at: datetime


class KnowledgeRetryIn(CamelModel):
    parser: Literal["light", "docling"] = "light"


class KnowledgeSearchIn(CamelModel):
    query: str = Field(min_length=1, max_length=2000)


class KnowledgePassageOut(CamelModel):
    chunk_id: int
    document_id: str
    filename: str | None
    text: str
    page: int | None
    heading: str | None
    score: float


class KnowledgeLimitsOut(CamelModel):
    max_upload_mb: int
    max_pages: int
    max_documents_per_kb: int
    max_kb_per_user: int
    user_quota_mb: int


class KnowledgeStatusOut(CamelModel):
    """GET /providers/rag-status — whether the dashboard shows the knowledge features at all."""

    available: bool
    # Whether the knowledge service is actually answering right now (available can be true while
    # the container is down — uploads then fail with a clear error).
    reachable: bool = False
    local_model: str | None = None
    docling_available: bool = False
    file_types: list[str] = []
    limits: KnowledgeLimitsOut | None = None
    usage_bytes: int = 0
    # Whether projects may use adaptive reasoning here (see chat/reasoning_router.py).
    adaptive_reasoning: bool = False


class BibEntryOut(CamelModel):
    key: str
    title: str | None
    author: str | None
    year: int | None


class BibImportOut(CamelModel):
    entries: int
    # Malformed, duplicate or over the 2,000-entry limit.
    skipped: int
    # Documents of the knowledge base now linked to one of the entries.
    linked: int
    # File names of the documents that aren't.
    unlinked: list[str]
