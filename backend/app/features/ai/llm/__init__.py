"""
LLM Chat Completion

Sends a chat turn to a project's configured LLM (large language model) provider. Most providers
go through litellm (litellm_provider.py); GWDG Arcana has its own direct HTTP integration
(arcana.py) because litellm doesn't support its RAG header/field.

How to use:
    from app.features.ai import llm

    request = llm.ChatRequest(preprompt, message, temperature, top_p, start_prompt, history)
    reply = llm.complete(api_key, request)
    for delta in llm.stream(api_key, request):
        ...
"""

import logging
from collections.abc import Iterator

from app.core.providers import GWDG_ARCANA_PROVIDER
from app.features.ai.llm.arcana import ArcanaClient, strip_arcana_references
from app.features.ai.llm.base import ChatRequest, LLMClient, LLMUsage
from app.features.ai.llm.litellm_provider import LiteLLMClient
from app.features.api_keys.models import UserApiKey

__all__ = ["ChatRequest", "LLMClient", "LLMUsage", "complete", "get_llm_client", "strip_arcana_references", "stream"]

logger = logging.getLogger(__name__)


def get_llm_client(api_key_record: UserApiKey) -> LLMClient:
    """The client for the provider of `api_key_record`."""
    if api_key_record.provider == GWDG_ARCANA_PROVIDER:
        return ArcanaClient(api_key_record)
    return LiteLLMClient(api_key_record)


def complete(api_key_record: UserApiKey, request: ChatRequest, usage: LLMUsage | None = None) -> str:
    """Send a chat turn to the key's LLM and return the reply text; fills `usage` if given."""
    return get_llm_client(api_key_record).complete(request, usage)


def stream(api_key_record: UserApiKey, request: ChatRequest, usage: LLMUsage | None = None) -> Iterator[str]:
    """Yield reply text deltas. Arcana's citation block is filtered out mid-stream.

    If streaming fails before the very first delta, this falls back to the plain non-streamed call
    and yields the whole reply as one delta. The visitor then still gets an answer out of this one
    request — without it the frontend has to notice the failure and ask the LLM all over again
    (see pages/PublicChat/index.tsx), which costs a second full round trip for a reply the server
    could have fetched itself. The caller needs no special case: a single large delta just chunks
    into more sentences at once.
    """
    client = get_llm_client(api_key_record)
    yielded = False
    try:
        for delta in client.stream(request, usage):
            yielded = True
            yield delta
    except Exception as exc:
        # Same rule as the retry in LiteLLMClient.stream: once part of the reply has left this
        # function it may already have been spoken, so a mid-stream failure has to stay a failure.
        if yielded:
            raise
        logger.warning("Streaming failed, falling back to a plain request: %s", exc)
        reply = client.complete(request, usage)
        if reply:
            yield reply
