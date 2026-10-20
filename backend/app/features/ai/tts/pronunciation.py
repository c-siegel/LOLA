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
from dataclasses import replace
from functools import lru_cache

from normt2s import Rule, RuleSet

NUMBER_PLACEHOLDER = "{number}"


def placeholder_count(text: str) -> int:
    return text.count(NUMBER_PLACEHOLDER)


def spell_out(term: str) -> str:
    """"GPT" -> "G P T": letters and digits one by one, so the TTS reads them individually."""
    return " ".join(char for char in term if char.isalnum())


def _with_digits_read(rule: Rule) -> Rule:
    """`rule`, renormalized if its spoken form has a digit of its own ("1 s" -> "1 Sekunde").

    normt2s says a rule's output exactly as written, so that "1" would stay a digit next to
    numbers read as words. A teacher who types a digit there means the number, and the word list
    has no column for the flag, so it is derived here.
    """
    if any(char.isdigit() for char in rule.spoken.replace(NUMBER_PLACEHOLDER, "")):
        return replace(rule, renormalize=True)
    return rule


@lru_cache(maxsize=256)
def _compile(rules: tuple[Rule, ...]) -> RuleSet:
    return RuleSet(_with_digits_read(rule) for rule in rules)


def compile_rules(rules: Iterable[Rule]) -> RuleSet:
    """A RuleSet for `rules`, cached by their content — a teacher's list only changes when they
    edit it, so every chat reply after that reuses the same compiled pattern."""
    return _compile(tuple(rules))
