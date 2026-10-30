"""
Bridge Phrases

A reasoning model can take many seconds before its first word. For those turns the avatar first
says a short, neutral phrase, so the student isn't left in silence (docs/adaptive-reasoning-plan.md).
The phrase says nothing about the question, so it can't contradict the real answer.

The audio of the phrases is synthesized in the background the first time a project needs it and
kept here, so the bridge costs no TTS call at the moment a turn escalates. If a phrase isn't
ready yet, that turn simply goes without one.
"""

import random
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

PHRASES: dict[str, tuple[str, ...]] = {
    "de": (
        "Spannender Gedanke, lass mich kurz überlegen.",
        "Moment, das schaue ich mir genauer an.",
        "Hm, dazu muss ich kurz nachdenken.",
        "Einen Augenblick, ich denke kurz darüber nach.",
    ),
    "en": (
        "Interesting thought, let me think about that for a second.",
        "Hold on, let me look at that more closely.",
        "Hmm, I need a moment to think about that.",
        "Give me a second, I'm thinking it through.",
    ),
}

_MAX_CACHED = 256
# After a failed rendering (a broken TTS key, a provider outage) that phrase isn't retried for this
# long, so every turn doesn't repeat four failing TTS calls on the teacher's key.
_RETRY_AFTER_FAILURE_SECONDS = 300.0


def phrases_for(language: str) -> tuple[str, ...]:
    """The bridge phrases of a language; none for a language without any (then no bridge is spoken)."""
    return PHRASES.get(language.split("-")[0].lower(), ())


def pick(candidates: list[str]) -> str | None:
    return random.choice(candidates) if candidates else None


class AudioCache:
    """Rendered phrase audio (base64, content type) by whatever identifies the voice it was made
    with, so a changed voice or TTS key simply stops matching. Bounded, least recently used out
    first. The first element of a key is the project id and the fifth the voice clip's hash, so the
    audio can be dropped when either is deleted (see forget)."""

    def __init__(self) -> None:
        self._items: OrderedDict[tuple, tuple[str, str]] = OrderedDict()
        self._pending: set[tuple] = set()
        self._failed: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> tuple[str, str] | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: tuple, value: tuple[str, str]) -> None:
        with self._lock:
            self._items[key] = value
            while len(self._items) > _MAX_CACHED:
                self._items.popitem(last=False)
            self._pending.discard(key)
            self._failed.pop(key, None)

    def claim(self, key: tuple) -> bool:
        """True for exactly one caller per missing key, so a phrase isn't rendered twice at once
        (nor again soon after a failed attempt)."""
        with self._lock:
            if key in self._items or key in self._pending:
                return False
            failed_at = self._failed.get(key)
            if failed_at is not None and time.monotonic() - failed_at < _RETRY_AFTER_FAILURE_SECONDS:
                return False
            self._pending.add(key)
            return True

    def release(self, key: tuple, *, failed: bool = False) -> None:
        with self._lock:
            self._pending.discard(key)
            if failed:
                self._failed[key] = time.monotonic()

    def forget(self, matches: Callable[[tuple], bool]) -> None:
        """Drop every rendering whose key matches. Voice audio of a person must not outlive the
        project, voice clip or account it was made for."""
        with self._lock:
            for key in [key for key in (*self._items, *self._failed) if matches(key)]:
                self._items.pop(key, None)
                self._failed.pop(key, None)

    def forget_project(self, project_id: str) -> None:
        self.forget(lambda key: key[0] == project_id)

    def forget_voice_clip(self, sha256: str) -> None:
        self.forget(lambda key: key[4] == sha256)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._pending.clear()
            self._failed.clear()


audio_cache = AudioCache()
