"""
Escalation Rules

Decides, without calling any model, whether a student's message looks like an argument that
deserves a reasoning model ("adaptive" reasoning mode, see docs/adaptive-reasoning-plan.md). Zero
latency and no extra dependencies; the word lists are tables per language so a language is added
by adding a row, not by changing the logic.

Biased towards recall: a missed argument gives shallow feedback on a thesis, while an unneeded
escalation only costs time and money, and the reasoning budget (core/rate_limit.py) caps that.
"""

import re
from dataclasses import dataclass

# Phrases that mark a claim, a reason or a counterargument. Matched as whole words, any case.
ARGUMENT_CUES: dict[str, tuple[str, ...]] = {
    "de": (
        "weil", "deshalb", "deswegen", "daher", "darum", "folglich", "somit", "denn",
        "ich behaupte", "meine these", "meiner meinung nach", "ich bin der meinung", "ich finde, dass",
        "ich denke, dass", "ich glaube, dass", "dagegen spricht", "dafür spricht", "einerseits",
        "andererseits", "widerspricht", "ich stimme", "stimme ich nicht zu", "ich widerspreche",
        "gegenargument", "argument", "beweis", "begründung", "aber was ist mit", "das heißt",
    ),
    "en": (
        "because", "therefore", "thus", "hence", "consequently", "that's why", "which means",
        "i argue", "i claim", "my thesis", "in my opinion", "i believe that", "i think that",
        "i disagree", "i agree", "on the one hand", "on the other hand", "contradicts",
        "counterargument", "argument", "evidence", "proof", "but what about", "this means",
    ),
}

# Below this a message is a greeting, an acknowledgement or a one-word answer.
MIN_WORDS = 4
# A long contribution is rarely small talk.
LONG_MESSAGE_WORDS = 40
# An answer to the avatar's question has to say more than "yes" or a name.
ANSWER_MIN_WORDS = 8

_CUE_PATTERNS = {
    language: re.compile("|".join(rf"(?<!\w){re.escape(cue)}(?!\w)" for cue in cues), re.IGNORECASE)
    for language, cues in ARGUMENT_CUES.items()
}


@dataclass(frozen=True)
class Escalation:
    deep: bool
    # A short code for logs and the Latency Lab, never message text.
    reason: str


def check(message: str, history: list[dict], language: str) -> Escalation:
    patterns = _CUE_PATTERNS.get(language.split("-")[0].lower())
    if patterns is None:
        return Escalation(False, "language_unsupported")
    words = len(message.split())
    if words < MIN_WORDS:
        return Escalation(False, "short_message")
    if patterns.search(message):
        return Escalation(True, "argument_cue")
    if words >= LONG_MESSAGE_WORDS:
        return Escalation(True, "long_message")
    if words >= ANSWER_MIN_WORDS and _last_avatar_message_asked(history):
        return Escalation(True, "answer_to_question")
    return Escalation(False, "no_cue")


def _last_avatar_message_asked(history: list[dict]) -> bool:
    for entry in reversed(history):
        if entry.get("role") == "assistant":
            return entry.get("content", "").rstrip().rstrip("\"'»“”)").endswith("?")
    return False
