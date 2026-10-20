"""
litellm Chat Integration

Every LLM provider except GWDG Arcana goes through litellm, which talks to OpenAI, Anthropic,
Gemini, Ollama, OpenAI-compatible endpoints, ... with one API. Bring-your-own-key: model,
endpoint, and secret all come from the stored key record the project references
(Project.llm_api_key_id).
"""

import logging
import time
from collections.abc import Iterator

import litellm

from app.core.error_codes import ErrorCode
from app.core.providers import build_model_string, get_provider
from app.features.ai.llm.base import ChatRequest
from app.features.ai.llm.history import build_messages, sampling_params, thinking_extra_body
from app.features.api_keys.crypto import reveal_api_key_for_litellm
from app.features.api_keys.models import UserApiKey
from app.features.api_keys.resolve import effective_api_base

logger = logging.getLogger(__name__)

# The same idea as the Arcana retry (see arcana.py), but for every other provider. Why this is
# worth the added latency: measured against GWDG chat-ai with backend/scripts/gwdg_diag.sh, 9 of
# 20 *identical* requests came back as an empty-bodied 500 in ~98ms — too fast for the model to
# have run at all — while the other 11 succeeded. At that failure rate a single attempt is a
# coin flip.
#
# A 5xx, a timeout, or a dropped connection is usually transient; a 4xx (wrong key, unknown model,
# malformed request) would fail identically on a second try, so it isn't retried. RateLimitError
# is deliberately left out too: a fixed short delay rarely clears a quota, and failing fast gets
# us to the non-streamed fallback in app/features/ai/llm/__init__.py::stream sooner.
_TRANSIENT_LLM_ERRORS = (
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
    litellm.APIConnectionError,
    litellm.Timeout,
)
# Delay before each retry, in seconds; one entry per retry, so len() + 1 attempts in total. The
# first retry is deliberately immediate: the failure above arrives before any model ran, so there
# is no load to back off from, and waiting would only add to the time until the visitor hears the
# first word. The later retries do back off, in case the gateway genuinely is shedding load.
_LLM_RETRY_DELAYS = (0.0, 0.5, 1.5)
_LLM_MAX_ATTEMPTS = len(_LLM_RETRY_DELAYS) + 1


def _resolve_call(api_key_record: UserApiKey, model_id: str) -> tuple[str, dict]:
    """Work out the litellm model string and endpoint override for a stored key."""
    # The provider prefix and endpoint come from the registry and the stored key — litellm
    # doesn't care which actual provider sits behind an OpenAI-compatible endpoint, as long as
    # the prefix ("openai/") and api_base are correct.
    model = build_model_string(api_key_record.provider, model_id)
    api_base = effective_api_base(api_key_record)
    return model, ({"api_base": api_base} if api_base else {})


def _retry_transient(call):
    """Call `call`, retrying a transient provider failure up to _LLM_MAX_ATTEMPTS times.

    Only safe for calls that produce nothing until they return — a streamed reply hands out text
    as it goes and needs the finer-grained rule in LiteLLMClient.stream instead.
    """
    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        try:
            return call()
        except _TRANSIENT_LLM_ERRORS as exc:
            if attempt == _LLM_MAX_ATTEMPTS:
                raise
            logger.warning(
                "LLM request failed (attempt %d/%d), retrying: %s",
                attempt,
                _LLM_MAX_ATTEMPTS,
                exc,
            )
            time.sleep(_LLM_RETRY_DELAYS[attempt - 1])


def _thinking_kwargs(api_key_record: UserApiKey, request: ChatRequest) -> dict:
    """litellm kwargs for the project's thinking switch, only for providers that honour it."""
    spec = get_provider(api_key_record.provider)
    body = thinking_extra_body(request) if spec and spec.thinking_switch else {}
    return {"extra_body": body} if body else {}


class LiteLLMClient:
    """Chat completion via litellm, for every provider except Arcana."""

    def __init__(self, api_key_record: UserApiKey) -> None:
        self._key = api_key_record

    def complete(self, request: ChatRequest) -> str:
        """Send the request and return the reply text."""
        api_key = reveal_api_key_for_litellm(self._key.encrypted_api_key)
        model, extra = _resolve_call(self._key, self._key.model_id or "")
        response = _retry_transient(
            lambda: litellm.completion(
                model=model,
                api_key=api_key,
                messages=build_messages(request),
                **sampling_params(request),
                **_thinking_kwargs(self._key, request),
                **extra,
            )
        )
        content = response.choices[0].message.content
        # None (not "") when e.g. a content filter blocked the reply or the model answered with a
        # tool call — raised so callers treat it as the failed LLM call it is.
        if content is None:
            raise ValueError("LLM returned an empty reply")
        return content

    def stream(self, request: ChatRequest) -> Iterator[str]:
        """Streamed chat, retrying transient provider failures.

        Retrying is only safe while nothing has been yielded yet: once the caller holds a delta
        it may already have been synthesized and spoken (see the public chat's streaming
        endpoint), and starting over would make the avatar say the beginning of the reply a
        second time. This mirrors the `past_handshake` rule in ArcanaClient.stream.
        """
        api_key = reveal_api_key_for_litellm(self._key.encrypted_api_key)
        model, extra = _resolve_call(self._key, self._key.model_id or "")

        for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
            yielded = False
            try:
                response = litellm.completion(
                    model=model,
                    api_key=api_key,
                    messages=build_messages(request),
                    stream=True,
                    **sampling_params(request),
                    **_thinking_kwargs(self._key, request),
                    **extra,
                )
                for chunk in response:
                    # litellm emits role-only and finish chunks with delta.content == None.
                    delta = chunk.choices[0].delta.content
                    if delta is not None:
                        yielded = True
                        yield delta
                return
            except _TRANSIENT_LLM_ERRORS as exc:
                if yielded or attempt == _LLM_MAX_ATTEMPTS:
                    raise
                logger.warning(
                    "LLM stream failed (attempt %d/%d), retrying: %s",
                    attempt,
                    _LLM_MAX_ATTEMPTS,
                    exc,
                )
                time.sleep(_LLM_RETRY_DELAYS[attempt - 1])

    def test(self) -> None:
        """Try the key with a minimal real call; raises on an invalid key or provider error."""
        plaintext = reveal_api_key_for_litellm(self._key.encrypted_api_key)
        spec = get_provider(self._key.provider)

        # Prefers testing the model actually configured — that checks the key, endpoint, and model
        # name in one go. Only falls back to the provider's cheap ping model if none is set.
        if self._key.model_id:
            model, extra = _resolve_call(self._key, self._key.model_id)
        elif spec is not None and spec.test_model:
            api_base = effective_api_base(self._key)
            model, extra = spec.test_model, ({"api_base": api_base} if api_base else {})
        else:
            raise ValueError(ErrorCode.API_KEY_NO_TESTABLE_MODEL)

        litellm.completion(
            model=model,
            api_key=plaintext,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            **extra,
        )
