"""
Teacher-Defined Pronunciation Rules

Applies a teacher's own word list ("pH" -> "p H", "{number} m/s" -> "{number} Meter pro Sekunde")
to the text handed to a TTS engine. Teachers edit these rules on the "Pronunciation" dashboard
page (see features/pronunciation), so unlike the built-in rules in normalizer.py they are plain
text, never regular expressions: every term is escaped, and the only pattern syntax is the
`{number}` placeholder.

How a rule matches
- A term matches literally. A space in a term matches any run of whitespace, so "z. B." also
  matches a line-wrapped "z.\nB.".
- `whole_word` (the default) only stops a term from matching inside a longer *word*: "N" doesn't
  match the "N" in "Newton", but "kg" still matches in "10kg" — units are often glued to their
  number, and that must keep working.
- `{number}` stands for a number like 5, 1,5 or 3.000,25 and is put back where the spoken form
  says `{number}` (in order, if there are several).
- All rules are applied in a single pass, longest term first, so "km/h" wins over "km", and a
  replacement is never matched again by another rule.
- If a replacement would glue onto a neighbouring letter or digit ("Δt" -> "Deltat"), a space is
  put in between, so teachers never have to think about leading/trailing spaces.

How to use:
    from app.features.ai.tts.pronunciation import PronunciationRule, compile_rules

    matcher = compile_rules((PronunciationRule("pH", "p H", case_sensitive=True),))
    matcher.apply("Der pH-Wert")  # "Der p H-Wert"
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

NUMBER_PLACEHOLDER = "{number}"
# Digits with optional decimal/thousands separators in either language ("1,5", "1.5",
# "3.000,25"). The decimal rule in normalizer.py runs afterwards and still spells out the fraction.
_NUMBER_PATTERN = r"\d+(?:[.,]\d+)*"

# A letter (any script, so "Ω" and "λ" count) — \w minus digits and the underscore.
_LETTER = r"[^\W\d_]"
_LETTER_OR_DIGIT = r"[^\W_]"


@dataclass(frozen=True)
class PronunciationRule:
    """One teacher-defined replacement; frozen so a tuple of rules can key the compile cache."""

    term: str
    spoken: str
    whole_word: bool = True
    case_sensitive: bool = False


def _is_letter(char: str) -> bool:
    return char.isalpha()


def _edge_guard(edge: str | None, *, before: bool) -> str:
    """The lookaround that keeps a whole-word term from matching inside a longer word.

    `edge` is the term's first/last character, or None for a `{number}` placeholder. A letter edge
    must not touch another letter (digits are fine: "10kg"); a digit edge must not touch a letter
    or digit, so "{number} m" doesn't start halfway through "123".
    """
    if edge is not None and _is_letter(edge):
        neighbour = _LETTER
    elif edge is None or edge.isdigit():
        neighbour = _LETTER_OR_DIGIT
    else:
        # Punctuation/symbol edges ("°C", "≈") have no word to be part of.
        return ""
    return f"(?<!{neighbour})" if before else f"(?!{neighbour})"


def _term_pattern(term: str, group_prefix: str) -> tuple[str, int]:
    """Regex source for `term` (escaped, spaces flexible, `{number}` as named groups) and how many
    placeholders it has."""
    parts = term.split(NUMBER_PLACEHOLDER)
    pieces: list[str] = []
    for index, literal in enumerate(parts):
        if index > 0:
            pieces.append(f"(?P<{group_prefix}n{index - 1}>{_NUMBER_PATTERN})")
        words = literal.split(" ")
        pieces.append(r"\s+".join(re.escape(word) for word in words))
    return "".join(pieces), len(parts) - 1


def placeholder_count(text: str) -> int:
    return text.count(NUMBER_PLACEHOLDER)


def literal_length(term: str) -> int:
    """Length of `term` without its placeholders — what "longest term first" sorts by."""
    return len(term.replace(NUMBER_PLACEHOLDER, ""))


def spell_out(term: str) -> str:
    """"GPT" -> "G P T": letters and digits one by one, so the TTS reads them individually."""
    return " ".join(char for char in term if char.isalnum())


class PronunciationMatcher:
    """A compiled set of rules; build one through compile_rules() so it's cached."""

    def __init__(self, rules: Iterable[PronunciationRule]) -> None:
        # Longest literal text first, so a more specific term wins wherever two could match at the
        # same position (regex alternation takes the first alternative that matches); on a tie a
        # plain term beats a template, and a case-sensitive term beats a case-insensitive one.
        ordered = sorted(
            (r for r in rules if r.term.strip()),
            key=lambda r: (-literal_length(r.term), placeholder_count(r.term) > 0, not r.case_sensitive),
        )
        self._rules = ordered
        alternatives: list[str] = []
        self._placeholders: list[int] = []
        for index, rule in enumerate(ordered):
            body, placeholders = _term_pattern(rule.term, f"r{index}")
            if rule.whole_word:
                first = None if rule.term.startswith(NUMBER_PLACEHOLDER) else rule.term[0]
                last = None if rule.term.endswith(NUMBER_PLACEHOLDER) else rule.term[-1]
                body = f"{_edge_guard(first, before=True)}{body}{_edge_guard(last, before=False)}"
            if not rule.case_sensitive:
                body = f"(?i:{body})"
            alternatives.append(f"(?P<r{index}>{body})")
            self._placeholders.append(placeholders)
        self._pattern = re.compile("|".join(alternatives)) if alternatives else None

    def __bool__(self) -> bool:
        return self._pattern is not None

    @property
    def rules(self) -> tuple[PronunciationRule, ...]:
        return tuple(self._rules)

    def apply(self, text: str, applied: list[PronunciationRule] | None = None) -> str:
        """Rewrite `text`; appends every rule that matched to `applied`, if given."""
        if self._pattern is None:
            return text

        def _replace(match: re.Match[str]) -> str:
            # The outer alternative group closes last, so it's the one lastgroup names (the inner
            # {number} groups never are).
            index = int(match.lastgroup[1:])  # type: ignore[index]
            rule = self._rules[index]
            if applied is not None and rule not in applied:
                applied.append(rule)
            numbers = [match.group(f"r{index}n{i}") for i in range(self._placeholders[index])]
            spoken = rule.spoken
            for number in numbers:
                spoken = spoken.replace(NUMBER_PLACEHOLDER, number, 1)
            return _pad(match.string, match.start(), match.end(), spoken)

        return self._pattern.sub(_replace, text)


def _pad(text: str, start: int, end: int, replacement: str) -> str:
    """Adds a space where `replacement` would otherwise run straight into a neighbouring letter or
    digit of the original text ("Δt" -> "Delta t", "100km/h" -> "100 Kilometer pro Stunde")."""
    if not replacement:
        return replacement
    if start > 0 and text[start - 1].isalnum() and replacement[0].isalnum():
        replacement = " " + replacement
    if end < len(text) and text[end].isalnum() and replacement[-1].isalnum():
        replacement = replacement + " "
    return replacement


@lru_cache(maxsize=256)
def _compile(rules: tuple[PronunciationRule, ...]) -> PronunciationMatcher:
    return PronunciationMatcher(rules)


def compile_rules(rules: Iterable[PronunciationRule]) -> PronunciationMatcher:
    """A matcher for `rules`, cached by their content — a teacher's list only changes when they
    edit it, so every chat reply after that reuses the same compiled pattern."""
    return _compile(tuple(rules))
