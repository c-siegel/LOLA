"""
Docling Parsing (optional)

Sends a PDF or DOCX to a docling-serve container (Compose profile "docling") for layout-aware
conversion — tables, multi-column pages, and OCR for scanned pages — and gets Markdown back.
Docling runs in its own hardened container (see docker/docker-compose.yml), so it isn't wrapped in
this service's subprocess sandbox; the same upload checks (checks.py) still run before anything
is sent, and the page and character limits are applied to what comes back.

docling-serve API (v1): POST /v1/convert/file, multipart "files" plus conversion options as form
fields; the reply carries the Markdown in document.md_content. A page-break placeholder is
requested so page numbers survive the conversion.
"""

import httpx

from app import errors
from app.config import settings
from app.errors import RagError
from app.parsing.types import ParseResult, cap_sections, split_markdown

_PAGE_BREAK = "<!-- lola-page-break -->"

_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def docling_available() -> bool:
    return bool(settings.docling_url)


def parse_with_docling(file_type: str, data: bytes, limits, client: httpx.Client | None = None) -> ParseResult:
    if not settings.docling_url:
        raise RagError(errors.DOCLING_UNAVAILABLE)
    if file_type not in _MIME_TYPES:
        raise RagError(errors.UNSUPPORTED_FILE_TYPE)

    form = {
        "to_formats": "md",
        "do_ocr": "true",
        "ocr_engine": settings.docling_ocr_engine,
        # Images become a placeholder; nothing but text is ever indexed.
        "image_export_mode": "placeholder",
        "md_page_break_placeholder": _PAGE_BREAK,
        "abort_on_error": "false",
        "document_timeout": str(int(settings.docling_timeout_seconds)),
    }
    http = client or httpx.Client(timeout=httpx.Timeout(10.0, read=settings.docling_timeout_seconds + 30))
    try:
        response = http.post(
            f"{settings.docling_url.rstrip('/')}/v1/convert/file",
            data=form,
            files={"files": (f"document.{file_type}", data, _MIME_TYPES[file_type])},
        )
    except httpx.TransportError as exc:
        raise RagError(errors.DOCLING_UNAVAILABLE) from exc
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        raise RagError(errors.DOCLING_FAILED)
    try:
        markdown = response.json()["document"]["md_content"] or ""
    except (ValueError, KeyError, TypeError) as exc:
        raise RagError(errors.DOCLING_FAILED) from exc

    pages = markdown.split(_PAGE_BREAK)
    if len(pages) > limits.max_pages:
        raise RagError(errors.TOO_MANY_PAGES)
    # Without page breaks in the reply (a DOCX, or a docling-serve version that ignores the
    # option) there's one block, and page numbers stay unknown rather than all becoming "1".
    paged = len(pages) > 1
    sections = []
    for number, page in enumerate(pages, start=1):
        sections.extend(split_markdown(page, page=number if paged else None))
    sections, truncated = cap_sections(sections, limits.max_chars)
    return ParseResult(sections, page_count=len(pages) if paged else None, truncated=truncated)
