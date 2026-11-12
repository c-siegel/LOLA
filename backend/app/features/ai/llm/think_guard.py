"""
Think-Tag Guard

Some models (Qwen3, DeepSeek-R1, gpt-oss behind Ollama, or vLLM without a reasoning parser) put
their reasoning into the reply text as <think>...</think>. The avatar would speak it, so every
provider's reply passes through this guard before it reaches the caller.
"""

import re

# Opening tag -> matching closing tag. Matched case-insensitively.
_BLOCKS = {"<think>": "</think>", "<reasoning>": "</reasoning>"}
_OPEN_RE = re.compile("|".join(re.escape(tag) for tag in _BLOCKS), re.IGNORECASE)


def _partial_suffix_len(text: str, tags: list[str]) -> int:
    """Length of the longest suffix of `text` that is a proper prefix of one of `tags`.

    That suffix may be the start of a tag whose rest arrives in the next delta, so it is held back.
    """
    for size in range(min(len(text), max(map(len, tags)) - 1), 0, -1):
        tail = text[-size:].lower()
        if any(tag.startswith(tail) for tag in tags):
            return size
    return 0


class ThinkTagGuard:
    """Cuts <think>/<reasoning> blocks out of a token stream.

    feed() returns text that is safe to speak. A block that is never closed is dropped to the end
    of the stream: half a chain of thought is no better to hear than all of it.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._close_re: re.Pattern[str] | None = None
        self._close_tag = ""
        # Models put blank lines between </think> and the answer; they would only delay the speech.
        self._strip_leading_space = False

    def feed(self, delta: str) -> str:
        self._buf += delta
        released: list[str] = []
        while True:
            if self._close_re is None:
                if self._strip_leading_space:
                    self._buf = self._buf.lstrip()
                    self._strip_leading_space = not self._buf
                match = _OPEN_RE.search(self._buf)
                if match:
                    released.append(self._buf[: match.start()])
                    self._buf = self._buf[match.end() :]
                    self._close_tag = _BLOCKS[match.group().lower()]
                    self._close_re = re.compile(re.escape(self._close_tag), re.IGNORECASE)
                    continue
                hold = _partial_suffix_len(self._buf, list(_BLOCKS))
                released.append(self._buf[: len(self._buf) - hold])
                self._buf = self._buf[len(self._buf) - hold :]
                break

            match = self._close_re.search(self._buf)
            if match:
                self._buf = self._buf[match.end() :]
                self._close_re = None
                self._strip_leading_space = True
                continue
            # Inside a block everything is discarded, except a possible start of the closing tag.
            hold = _partial_suffix_len(self._buf, [self._close_tag])
            self._buf = self._buf[len(self._buf) - hold :]
            break
        return "".join(released)

    def flush(self) -> str:
        """End of stream: a held partial tag was ordinary text after all, unless a block is open."""
        remainder = "" if self._close_re is not None else self._buf
        self._buf = ""
        return remainder


def strip_think_tags(text: str) -> str:
    """The same filtering for a complete reply."""
    guard = ThinkTagGuard()
    return guard.feed(text) + guard.flush()
