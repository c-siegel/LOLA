"""
LLM Client Interface

What every LLM provider integration implements, and the request it receives.
"""

from collections.abc import Iterator
from dataclasses import dataclass, fields
from typing import Protocol


@dataclass(frozen=True)
class ChatRequest:
    """One chat turn to send to an LLM: the project's prompt setup plus the new message."""

    preprompt: str
    message: str
    temperature: float | None = None
    top_p: float | None = None
    start_prompt: str | None = None
    history: list[dict] | None = None
    # Retrieved knowledge-base passages, already formatted (see features/knowledge/prompt.py) —
    # appended to the system prompt by build_messages, so every provider gets them the same way.
    reference_material: str | None = None
    # True/False = switch the model's reasoning on/off; None = leave the model's default alone.
    thinking: bool | None = None


@dataclass
class LLMUsage:
    """Token counts the provider reported for one call, for the Latency Lab.

    A client fills it in when the caller passes one; every field stays None when the provider
    doesn't report it (cached/reasoning counts in particular vary a lot between providers).
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Prompt tokens served from the provider's prompt cache.
    cached_tokens: int | None = None
    # Hidden reasoning tokens, billed as output but never spoken.
    reasoning_tokens: int | None = None

    def update_from(self, raw: object) -> None:
        """Read an OpenAI-style `usage` (an object, as litellm returns, or a dict, as Arcana's JSON)."""
        if raw is None:
            return

        def get(source: object, name: str) -> object:
            return source.get(name) if isinstance(source, dict) else getattr(source, name, None)

        def count(value: object) -> int | None:
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        self.prompt_tokens = count(get(raw, "prompt_tokens"))
        self.completion_tokens = count(get(raw, "completion_tokens"))
        self.cached_tokens = count(get(get(raw, "prompt_tokens_details"), "cached_tokens"))
        self.reasoning_tokens = count(get(get(raw, "completion_tokens_details"), "reasoning_tokens"))

    def to_client(self) -> dict[str, int | None]:
        """camelCase for the API, or an all-None dict (never absent) so the shape is fixed."""
        return {
            "".join(part if i == 0 else part.title() for i, part in enumerate(f.name.split("_"))): getattr(self, f.name)
            for f in fields(self)
        }


class LLMClient(Protocol):
    """A chat-completion provider bound to one stored API key."""

    def complete(self, request: ChatRequest, usage: LLMUsage | None = None) -> str:
        """Send the request and return the full reply text; fills `usage` if given."""
        ...

    def stream(self, request: ChatRequest, usage: LLMUsage | None = None) -> Iterator[str]:
        """Send the request and yield the reply text as it arrives; fills `usage` if given
        (once the stream has been consumed)."""
        ...

    def test(self) -> None:
        """Try the key with a minimal real call; raises on an invalid key or provider error."""
        ...
