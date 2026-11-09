"""
Client for the Knowledge Service

Thin HTTP wrapper around the optional knowledge (RAG) service's internal API (rag/app/main.py).
Every call sends the shared RAG_SERVICE_TOKEN. Two kinds of failure, kept apart because callers
handle them differently:

- RagUnavailable: the service couldn't be reached or answered with a server error. Chat turns
  carry on without passages; dashboard actions show "knowledge service unavailable".
- RagRejected: the service refused the request on purpose (a bad file, a model mismatch) with
  one of its error codes, already translated to the backend's KNOWLEDGE_* codes.

How to use:
    from app.features.knowledge import rag_client

    rag_client.upload_document(meta, filename, data)
"""

import json
import logging
import time

import httpx

from app.core.config import settings
from app.core.error_codes import ErrorCode

logger = logging.getLogger(__name__)

# trust_env=False: the knowledge service is on the internal Compose network. A deployment that
# sets HTTP(S)_PROXY for outbound provider calls must not send these internal requests (which
# carry the shared token and teachers' documents) to that proxy.
_client = httpx.Client(trust_env=False)

# Mirrors rag/app/errors.py. Anything else (or nothing) becomes KNOWLEDGE_SERVICE_ERROR, so a
# new code in the service can never reach the dashboard as an untranslated string.
_PASSED_ON_CODES = {
    "UNSUPPORTED_FILE_TYPE",
    "FILE_TYPE_MISMATCH",
    "FILE_TOO_LARGE",
    "FILE_EMPTY",
    "DOCX_INVALID",
    "DOCX_MACROS",
    "ARCHIVE_TOO_LARGE",
    "PDF_ENCRYPTED",
    "TOO_MANY_PAGES",
    "NO_EXTRACTABLE_TEXT",
    "FILE_TOO_COMPLEX",
    "PARSE_TIMEOUT",
    "PARSE_FAILED",
    "DOCLING_UNAVAILABLE",
    "DOCLING_FAILED",
    "EMBEDDING_FAILED",
    "EMBEDDING_MODEL_NOT_ALLOWED",
    "EMBEDDING_KEY_MISSING",
    "EMBEDDING_MISMATCH",
    "INTERRUPTED",
    "RETRY_NOT_POSSIBLE",
    "DOCUMENT_NOT_FOUND",
}


def knowledge_code(service_code: str | None) -> str:
    """The backend's error code for one of the knowledge service's codes."""
    if service_code in _PASSED_ON_CODES:
        return f"KNOWLEDGE_{service_code}"
    return ErrorCode.KNOWLEDGE_SERVICE_ERROR


class RagUnavailable(Exception):
    """The knowledge service couldn't be reached, or failed on its side."""


class RagRejected(Exception):
    """The knowledge service refused the request; `code` is a backend KNOWLEDGE_* code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.rag_service_token}"}


def _url(path: str) -> str:
    return f"{settings.rag_service_url.rstrip('/')}{path}"


def _request(method: str, path: str, *, timeout: float | None = None, **kwargs) -> httpx.Response:
    try:
        response = _client.request(
            method, _url(path), headers=_headers(), timeout=timeout or settings.rag_request_timeout_seconds, **kwargs
        )
    except httpx.HTTPError as exc:
        raise RagUnavailable(type(exc).__name__) from exc
    if response.status_code >= 500 or response.status_code == 401:
        # 401 means the two services disagree on the token — an operator problem, not the
        # teacher's, so it's reported like an outage (and logged so the operator can find it).
        logger.warning("Knowledge service answered %s for %s %s", response.status_code, method, path)
        raise RagUnavailable(str(response.status_code))
    if response.status_code >= 400:
        try:
            code = response.json().get("detail", {}).get("code")
        except (ValueError, AttributeError):
            code = None
        raise RagRejected(knowledge_code(code))
    return response


_capabilities_cache: tuple[float, dict] | None = None
_CAPABILITIES_TTL_SECONDS = 60.0


def capabilities(timeout: float = 5.0) -> dict:
    """Local model, file types, hard limits and Docling availability — cached for a minute,
    since the dashboard asks on every page load and the answer only changes with a redeploy."""
    global _capabilities_cache
    now = time.monotonic()
    if _capabilities_cache is not None and now - _capabilities_cache[0] < _CAPABILITIES_TTL_SECONDS:
        return _capabilities_cache[1]
    body = _request("GET", "/capabilities", timeout=timeout).json()
    _capabilities_cache = (now, body)
    return body


def upload_document(meta: dict, filename: str, data: bytes) -> dict:
    return _request(
        "POST", "/documents", data={"meta": json.dumps(meta)}, files={"file": (filename, data)}
    ).json()


def document_statuses(document_ids: list[str]) -> list[dict]:
    if not document_ids:
        return []
    return _request("POST", "/documents/status", json={"document_ids": document_ids}).json()


def retry_document(document_id: str, parser: str, embedding: dict, limits: dict) -> dict:
    body = {"parser": parser, "embedding": embedding, "limits": limits}
    return _request("POST", f"/documents/{document_id}/retry", json=body).json()


def delete_document(document_id: str) -> None:
    _request("DELETE", f"/documents/{document_id}")


def delete_knowledge_base(knowledge_base_id: str) -> None:
    _request("DELETE", f"/knowledge-bases/{knowledge_base_id}")


def query(knowledge_base_ids: list[str], text: str, top_k: int, embedding: dict, timeout: float) -> list[dict]:
    body = {"knowledge_base_ids": knowledge_base_ids, "query": text, "top_k": top_k, "embedding": embedding}
    return _request("POST", "/query", json=body, timeout=timeout).json()["passages"]


def embedding_test(embedding: dict) -> int:
    return _request("POST", "/embedding-test", json={"embedding": embedding}).json()["dimensions"]


def sample_chunks(knowledge_base_id: str, sample: int) -> list[dict]:
    """A random sample of the KB's passages, to draft test questions from (features/evaluation/)."""
    return _request("GET", f"/knowledge-bases/{knowledge_base_id}/chunks", params={"sample": sample}).json()
