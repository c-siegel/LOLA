"""
Reasoning Switch

Maps a request's `reasoning` ("off", "on" or an effort level, None = leave the model alone) to
what each provider path understands. Mapping verified against litellm 1.97 (see pyproject.toml):
it translates `reasoning_effort` itself for Anthropic (`thinking` + a larger `max_tokens`), Gemini
(`thinkingConfig`) and Ollama (`think`).
"""

from functools import lru_cache
from typing import Literal

import litellm

from app.core.providers import OLLAMA_PROVIDER, build_model_string, get_provider

REASONING_EFFORTS = ("low", "medium", "high")

# "none": the model can't be switched, so nothing is sent. "switch": on or off only (vLLM-style
# servers read `enable_thinking` from the chat template). "effort": off, on, or a level.
ReasoningSupport = Literal["none", "switch", "effort"]


@lru_cache(maxsize=256)
def reasoning_support(provider: str, model_id: str) -> ReasoningSupport:
    """What the provider path behind this key and model can do with a reasoning setting.

    Per model, not per provider: gpt-4o and o3 share a provider but not the parameter, and
    litellm raises an unsupported-params error when it is sent to the wrong one.
    """
    spec = get_provider(provider)
    if spec is not None and spec.thinking_switch:
        return "switch"
    model = build_model_string(provider, model_id)
    try:
        if litellm.supports_reasoning(model=model):
            return "effort"
        # Ollama's models aren't flagged as reasoning models, but litellm maps the parameter.
        if provider == OLLAMA_PROVIDER and "reasoning_effort" in (litellm.get_supported_openai_params(model=model) or []):
            return "effort"
    except Exception:  # an unknown model string must never break a chat turn
        pass
    return "none"


def reasoning_kwargs(provider: str, model_id: str, reasoning: str | None) -> dict:
    """The litellm keyword arguments that apply `reasoning`, or {} to send nothing."""
    if reasoning is None:
        return {}
    support = reasoning_support(provider, model_id)
    if support == "none":
        return {}
    if support == "switch":
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": reasoning != "off"}}}

    if reasoning == "off":
        # Anthropic thinks only when asked to, so off is simply not asking.
        if provider == "anthropic":
            return {}
        if provider in ("gemini", OLLAMA_PROVIDER):
            return {"reasoning_effort": "disable"}
        # o-series models can't be switched off, only turned down.
        return {"reasoning_effort": "minimal" if "gpt-5" in model_id else "low"}
    if reasoning == "on":
        # Anthropic and Ollama need an explicit request; the others reason by default.
        return {"reasoning_effort": "medium"} if provider in ("anthropic", OLLAMA_PROVIDER) else {}
    return {"reasoning_effort": reasoning}


def sampling_allowed(provider: str, kwargs: dict) -> bool:
    """Whether temperature/top_p may be sent along with these reasoning kwargs.

    OpenAI's reasoning models reject them, and Anthropic's extended thinking forbids a custom
    temperature, so they are dropped whenever a reasoning level is actually sent to those two.
    """
    return not (provider in ("openai", "anthropic") and "reasoning_effort" in kwargs)
