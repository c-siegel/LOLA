"""
GWDG Arcana (RAG) Chat Integration

Direct HTTP integration with the GWDG SAIA API for Arcana knowledge-base (RAG, retrieval-
augmented generation) chats — litellm knows neither the required "inference-service" header nor
the "arcana" field used to address the knowledge base. Also filters out the "References:" block
Arcana appends to its replies (see ArcanaReferenceGuard).
"""

import json
import logging
import re
import time
from collections.abc import Iterator
from typing import Literal

import httpx

from app.core.error_codes import ErrorCode
from app.core.providers import GWDG_ARCANA_PROVIDER, get_provider
from app.features.ai.http import make_ipv4_client
from app.features.ai.llm.base import ChatRequest
from app.features.ai.llm.history import build_messages, sampling_params, thinking_extra_body
from app.features.api_keys.crypto import reveal_api_key
from app.features.api_keys.models import UserApiKey

logger = logging.getLogger(__name__)

_GWDG_ARCANA_TIMEOUT = 60.0  # A RAG lookup + model reply noticeably takes longer than a plain chat call.
# GWDG's API occasionally responds with its own 5xx error (observed: 500 Internal Server Error,
# with no apparent connection to the request itself) — retrying tends to fix it in practice. 4xx
# is NOT retried (a wrong key, invalid request, etc. would just fail the same way again).
_GWDG_ARCANA_MAX_ATTEMPTS = 3
_GWDG_ARCANA_RETRY_DELAY = 1.5

_client = make_ipv4_client()

# When its knowledge base (RAG) is enabled, Arcana appends a "References:" block with the raw
# source chunks (format "[RREF1] file.pdf p.X,y:Y (score)") directly onto the reply text — there's
# no separate API field for it, and no documented toggle to turn it off (checked the GWDG docs,
# see ArcanaClient's docstring). That's just noise for students, so it's cut off here.
_ARCANA_REFERENCES_RE = re.compile(r"\n-{3,}\s*\n\s*References:", re.IGNORECASE)


def strip_arcana_references(content: str) -> str:
    """Cut off Arcana's appended "References:" source-chunk block, if present."""
    match = _ARCANA_REFERENCES_RE.search(content)
    return content[: match.start()].rstrip() if match else content


def _scan_reference_marker(buf: str) -> Literal["confirmed", "diverged", "need_more"]:
    """Try to match `buf` against the marker shape from the start: "\\n-{3,}\\s*\\n\\s*References:"
    (case-insensitive on the literal). Re-examines `buf` from scratch every call instead of
    tracking a persistent phase — the held tail is always small (bounded by how long a divergence
    takes to appear), so this stays cheap and is far easier to reason about than an incremental
    parser with saved sub-state.
    """
    n = len(buf)
    if n == 0 or buf[0] != "\n":
        return "diverged"

    i = 1
    dash_count = 0
    while i < n and buf[i] == "-":
        dash_count += 1
        i += 1
    if i >= n:
        return "need_more"  # more dashes might still be coming
    if dash_count < 3:
        return "diverged"

    # \s*\n\s* is equivalent to "a whitespace run containing at least one \n" — either \s* can
    # itself absorb further newlines, so only the presence of one somewhere in the run matters.
    seen_newline = False
    while i < n and buf[i].isspace():
        seen_newline = seen_newline or buf[i] == "\n"
        i += 1
    if i >= n:
        return "need_more"  # still inside the whitespace run — more could still arrive
    if not seen_newline:
        return "diverged"

    literal = "references:"
    remaining = buf[i:].lower()
    match_len = min(len(remaining), len(literal))
    if remaining[:match_len] != literal[:match_len]:
        return "diverged"
    return "need_more" if len(remaining) < len(literal) else "confirmed"


class ArcanaReferenceGuard:
    """Filters Arcana's trailing '---\\nReferences:' block out of a token stream.

    feed() returns text that is safe to speak. Once the marker is confirmed, `finished` becomes
    True and the caller must stop consuming the stream.
    """

    def __init__(self) -> None:
        self._buf = ""
        # SCANNING: how far into _buf has already been confirmed to contain no trigger ("\n-").
        self._scan_pos = 0
        self._holding = False
        self.finished = False

    def feed(self, delta: str) -> str:
        if self.finished:
            return ""
        self._buf += delta
        released: list[str] = []
        while True:
            if not self._holding:
                trigger = self._find_trigger()
                if trigger is not None:
                    released.append(self._buf[:trigger])
                    self._buf = self._buf[trigger:]
                    self._holding = True
                    continue
                # Nothing pending except possibly a lone trailing "\n" still awaiting its
                # lookahead character — everything before that is definitely safe text.
                if self._scan_pos:
                    released.append(self._buf[: self._scan_pos])
                    self._buf = self._buf[self._scan_pos :]
                    self._scan_pos = 0
                break

            result = _scan_reference_marker(self._buf)
            if result == "need_more":
                break
            if result == "confirmed":
                self.finished = True
                self._buf = ""
                break
            # Diverged — a plain markdown "---" rule, not the marker. Go back to scanning, but
            # resume one character past the opening "\n" so the same failed attempt isn't found
            # again immediately (which would loop forever) — a later, genuine marker further
            # down the still-held text is still found this way.
            self._holding = False
            self._scan_pos = 1
        return "".join(released)

    def flush(self) -> str:
        """End of stream: resolve whatever is still held. If a marker attempt was left hanging
        (e.g. the reply ends exactly on "\\n---"), there is no more input to disambiguate it, so
        fall back to the same regex used post-hoc on a complete reply."""
        remainder = self._buf
        self._buf = ""
        if self._holding and _ARCANA_REFERENCES_RE.search(remainder):
            return ""
        return remainder

    def _find_trigger(self) -> int | None:
        """Index of the next "\n" immediately followed by "-" at/after _scan_pos, or None."""
        buf = self._buf
        n = len(buf)
        i = self._scan_pos
        while i < n:
            if buf[i] == "\n":
                if i + 1 >= n:
                    # Never decide on the final character of the buffer — the next token might
                    # still turn this into a real trigger.
                    self._scan_pos = i
                    return None
                if buf[i + 1] == "-":
                    return i
            i += 1
        self._scan_pos = i
        return None


class ArcanaClient:
    """Direct HTTP client for the GWDG SAIA API with an Arcana knowledge base.

    "enable-tools": true is sent together with the Arcana ID because, per the GWDG docs ("How to
    use Arcana"), both are enabled together in the web interface too — there's no explicit
    statement in the GWDG docs that the knowledge lookup is skipped without "enable-tools"; this
    behavior has NOT been verified against a real account. See
    https://docs.hpc.gwdg.de/services/ai-services/saia/index.html.
    """

    def __init__(self, api_key_record: UserApiKey) -> None:
        self._key = api_key_record

    def _endpoint_and_headers(self) -> tuple[str, dict]:
        if not self._key.model_id:
            raise ValueError(ErrorCode.ARCANA_KEY_MISSING_MODEL)
        if not self._key.arcana_id:
            raise ValueError(ErrorCode.ARCANA_KEY_MISSING_ID)

        api_key = reveal_api_key(self._key.encrypted_api_key)
        default_api_base = get_provider(GWDG_ARCANA_PROVIDER).default_api_base
        api_base = (self._key.api_base or default_api_base).rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "inference-service": "saia-openai-gateway",
        }
        return f"{api_base}/chat/completions", headers

    def _body(self, request: ChatRequest, *, stream: bool) -> dict:
        body = {
            "model": self._key.model_id,
            "messages": build_messages(request),
            "enable-tools": True,
            "arcana": {"id": self._key.arcana_id},
        }
        if stream:
            body["stream"] = True
        return {**body, **sampling_params(request), **thinking_extra_body(request)}

    def complete(self, request: ChatRequest) -> str:
        """Send the request and return the reply with Arcana's references block removed."""
        url, headers = self._endpoint_and_headers()
        body = self._body(request, stream=False)

        for attempt in range(1, _GWDG_ARCANA_MAX_ATTEMPTS + 1):
            try:
                response = _client.post(url, headers=headers, json=body, timeout=_GWDG_ARCANA_TIMEOUT)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Log the response body too, otherwise all you see at the end is "500 Internal
                # Server Error" with no clue what actually went wrong on GWDG's side.
                is_transient = exc.response.status_code >= 500
                logger.warning(
                    "GWDG Arcana answered %s (attempt %d/%d)%s: %s",
                    exc.response.status_code,
                    attempt,
                    _GWDG_ARCANA_MAX_ATTEMPTS,
                    ", retrying" if is_transient and attempt < _GWDG_ARCANA_MAX_ATTEMPTS else "",
                    exc.response.text[:500],
                )
                if not is_transient or attempt == _GWDG_ARCANA_MAX_ATTEMPTS:
                    raise
            except httpx.TransportError as exc:
                # Connection drop/timeout — also typically transient.
                logger.warning(
                    "GWDG Arcana unreachable (attempt %d/%d): %s", attempt, _GWDG_ARCANA_MAX_ATTEMPTS, exc
                )
                if attempt == _GWDG_ARCANA_MAX_ATTEMPTS:
                    raise
            else:
                content = response.json()["choices"][0]["message"]["content"]
                return strip_arcana_references(content)
            time.sleep(_GWDG_ARCANA_RETRY_DELAY)

    def stream(self, request: ChatRequest) -> Iterator[str]:
        """Streamed variant of complete() — same endpoint, headers, and body, plus
        "stream": True, parsed as OpenAI-style SSE (server-sent events). Each delta is passed
        through an ArcanaReferenceGuard so the citation block never reaches the caller.
        """
        url, headers = self._endpoint_and_headers()
        body = self._body(request, stream=True)

        # A read timeout (time between chunks), not a whole-call timeout — a long streamed reply
        # must not trip this just because the total stream duration exceeds _GWDG_ARCANA_TIMEOUT.
        # Connect still fails fast so a dead handshake doesn't hang the retry loop.
        timeout = httpx.Timeout(10.0, read=_GWDG_ARCANA_TIMEOUT)

        for attempt in range(1, _GWDG_ARCANA_MAX_ATTEMPTS + 1):
            past_handshake = False
            try:
                with _client.stream("POST", url, headers=headers, json=body, timeout=timeout) as response:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        response.read()  # buffers the body so .text is readable below
                        is_transient = exc.response.status_code >= 500
                        logger.warning(
                            "GWDG Arcana answered %s (attempt %d/%d)%s: %s",
                            exc.response.status_code,
                            attempt,
                            _GWDG_ARCANA_MAX_ATTEMPTS,
                            ", retrying" if is_transient and attempt < _GWDG_ARCANA_MAX_ATTEMPTS else "",
                            exc.response.text[:500],
                        )
                        if not is_transient or attempt == _GWDG_ARCANA_MAX_ATTEMPTS:
                            raise
                        time.sleep(_GWDG_ARCANA_RETRY_DELAY)
                        continue

                    # Past this point a failure must NOT retry — the visitor may already have
                    # heard part of the reply, and replaying it would duplicate speech.
                    past_handshake = True
                    guard = ArcanaReferenceGuard()
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if data == "[DONE]":
                            break
                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                        if delta is None:
                            continue
                        safe_text = guard.feed(delta)
                        if safe_text:
                            yield safe_text
                        if guard.finished:
                            return
                    tail = guard.flush()
                    if tail:
                        yield tail
                    return
            except httpx.TransportError as exc:
                if past_handshake:
                    raise
                # Connection drop/timeout before any content arrived — also typically transient.
                logger.warning(
                    "GWDG Arcana unreachable (attempt %d/%d): %s", attempt, _GWDG_ARCANA_MAX_ATTEMPTS, exc
                )
                if attempt == _GWDG_ARCANA_MAX_ATTEMPTS:
                    raise
                time.sleep(_GWDG_ARCANA_RETRY_DELAY)

    def test(self) -> None:
        """No cheap test model available (this call doesn't go through litellm) — the test costs
        the same RAG lookup as a real chat call."""
        self.complete(ChatRequest(preprompt="Du bist ein hilfreicher Assistent.", message="ping"))
