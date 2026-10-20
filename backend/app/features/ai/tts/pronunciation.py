"""
Teacher-Defined Pronunciation Rules

A teacher's own word list ("pH" -> "p H", "{number} m/s" -> "{number} Meter pro Sekunde"), edited
on the "Pronunciation" dashboard page (see features/pronunciation). The rules are normt2s Rules:
plain text, never regular expressions, with `{number}` as the only placeholder. normt2s applies
them inside its pipeline — after Markdown is stripped, before numbers become words — in one
longest-first pass; see normalizer.py. This module only keeps what LOLA's validation needs and the
compile cache.

How to use:
    from normt2s import Rule
    from app.features.ai.tts.pronunciation import compile_rules

    rules = compile_rules([Rule("pH", "p H", case_sensitive=True)])
    normalize_for_speech("Der pH-Wert", "de", rules)  # "Der p H-Wert"
"""

from collections.abc import Iterable
from functools import lru_cache

from normt2s import Rule, RuleSet

NUMBER_PLACEHOLDER = "{number}"


def placeholder_count(text: str) -> int:
    return text.count(NUMBER_PLACEHOLDER)


def spell_out(term: str) -> str:
    """"GPT" -> "G P T": letters and digits one by one, so the TTS reads them individually."""
    return " ".join(char for char in term if char.isalnum())


@lru_cache(maxsize=256)
def _compile(rules: tuple[Rule, ...]) -> RuleSet:
    return RuleSet(rules)


def compile_rules(rules: Iterable[Rule]) -> RuleSet:
    """A RuleSet for `rules`, cached by their content — a teacher's list only changes when they
    edit it, so every chat reply after that reuses the same compiled pattern."""
    return _compile(tuple(rules))
