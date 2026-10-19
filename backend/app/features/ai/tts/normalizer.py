"""
Speech Text Normalizer

Rewrites assistant reply text into a form that TTS (text-to-speech) engines pronounce correctly
before it is sent for speech synthesis. Markdown formatting, decimal numbers, math symbols, an
em dash, and chemical formulas like CO2 all look right in the chat but are misread out loud —
"**wichtig**" comes out as "star star wichtig star star", "1,23" as "one twenty-three" instead of
"one comma two three", "\\cdot" spelled out letter by letter instead of said as "times", an em dash
("—", used for a parenthetical aside) read as "minus", the same as a real formula's minus sign, and
"CO2" read as one made-up word instead of "C O two". German numbers, dates, prices and
abbreviations are also written out as words (verbalizer.py, switchable via settings.tts_verbalizer).
This module only changes the copy of the text handed to the TTS engine; the reply stored and
displayed in the chat keeps its original formatting.

How to use:
    from app.features.ai.tts.normalizer import normalize_for_speech

    spoken_text = normalize_for_speech(reply_text, language=project.spoken_language)
    # With the project owner's own word list (see pronunciation.py):
    spoken_text = normalize_for_speech(reply_text, project.spoken_language, pronunciation=matcher)
"""

import re
from collections.abc import Collection

from app.core.config import settings
from app.features.ai.tts.pronunciation import PronunciationMatcher
from app.features.ai.tts.verbalizer import verbalize

_DEFAULT_LANGUAGE = "de"

# The chat UI renders full Markdown, including GitHub-flavored extras like strikethrough (see
# remarkGfm in components/ChatBubble.tsx) — but a TTS engine has no notion of any of it, so left
# alone it reads the raw delimiter characters aloud. Stripped first, before every rule below, so a
# formula wrapped in bold (e.g. "**3 * 4**") still gets its multiplication/decimal treatment
# afterwards instead of the asterisks confusing those rules.
#
# Deliberately NOT handled: tables — they'd need actual structure-aware reading (row/column
# order), not a text substitution, so they're out of scope here.
_CODE_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
# Single-asterisk/underscore italics (*wichtig*) are more ambiguous than bold: a lone "*" is
# indistinguishable from the multiplication symbol (see _SYMBOL_RULES) until you look at what's
# actually inside it. "3 * 4" and "3*4*5" never reach here as a pairable match in the first place —
# CommonMark itself only treats "*"/"_" as emphasis when NOT flanked by whitespace on the inside,
# which a spaced-out multiplication always is, and a spaceless chain like "3*4*5" only pairs up if
# read left-to-right as two separate operators, not one emphasis span. What that leaves is
# genuinely ambiguous — a spaceless "*4*" alone in running text, which could be either — so
# _strip_or_keep_italic below only commits to stripping a match once its content contains an actual
# letter (real emphasis always wraps a word/phrase; a bare number or operator expression never
# does), and leaves anything else untouched for _SYMBOL_RULES to read as multiplication instead.
# The underscore variant additionally requires a non-word character on both outsides, so it never
# fires inside a snake_case identifier like "my_variable_name".
_ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", re.DOTALL)
_ITALIC_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_STRIKETHROUGH = re.compile(r"~~(.+?)~~", re.DOTALL)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_HEADING = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^>+[ \t]*", re.MULTILINE)
_BULLET_LIST_MARKER = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_ORDERED_LIST_MARKER = re.compile(r"^[ \t]*\d+[.)][ \t]+", re.MULTILINE)
_HORIZONTAL_RULE = re.compile(r"^[ \t]*([-*_])[ \t]*(?:\1[ \t]*){2,}$", re.MULTILINE)


def _strip_or_keep_italic(match: re.Match[str]) -> str:
    """Strips a single-asterisk/underscore emphasis match, unless its content looks more like a
    bare multiplication ("4") than actual emphasized text ("wichtig") — see _ITALIC's own comment."""
    content = match.group(1) if match.group(1) is not None else match.group(2)
    return content if _ITALIC_HAS_LETTER.search(content) else match.group(0)


def _strip_markdown(text: str) -> str:
    """Removes Markdown formatting the chat UI renders but a TTS engine would read literally."""
    text = _CODE_FENCE.sub(lambda m: m.group(1), text)
    text = _HORIZONTAL_RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _BULLET_LIST_MARKER.sub("", text)
    text = _ORDERED_LIST_MARKER.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), text)
    text = _ITALIC.sub(_strip_or_keep_italic, text)
    text = _STRIKETHROUGH.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    return text


# LaTeX/Markdown math delimiters ($$...$$ and $...$) — a TTS engine has no notion of "this is
# math," so left alone it reads "$$" as "Dollar Dollar". Only the delimiter characters are
# stripped; the formula inside stays for the symbol rules below to rewrite. Checked before those
# rules so a formula like "$$1,23 \cdot x$$" still gets its decimal/symbol treatment afterwards.
# Trade-off: a lone "$" used as a currency sign (e.g. "5$ oder 10$") also gets silently dropped
# instead of read as "Dollar" — accepted for now since paired math delimiters are far more common
# in this tutor's replies than currency mentions; add a currency-aware rule here if that changes.
_DISPLAY_MATH_DELIMITERS = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH_DELIMITERS = re.compile(r"\$(.+?)\$")


def _strip_math_delimiters(text: str) -> str:
    text = _DISPLAY_MATH_DELIMITERS.sub(r"\1", text)
    return _INLINE_MATH_DELIMITERS.sub(r"\1", text)


# An em dash ("—", U+2014) is a different character from the plain hyphen-minus ("-", U+002D) a
# formula actually uses (see _SYMBOL_RULES below) — so the two are already distinguishable by
# codepoint alone, no context-sensitive guessing needed; nothing here has to guess "is this a
# minus or a dash" from context. The mispronunciation happens on the TTS engine's own side: several
# engines read "—" the same way they'd read a minus sign, since both are visually "a horizontal
# line". Replacing it with a comma before the text ever reaches the TTS engine sidesteps that
# entirely, and never touches a real minus since that's a different character.
#
# The en dash ("–", U+2013) is ambiguous. Unspaced it's a range ("10–20 Minuten" = "10 to 20
# minutes") and stays — rewriting it to a comma would silently turn a range into a list ("10, 20
# minutes"); the verbalizer reads ranges as "bis". Spaced between words, though, it's the German
# Gedankenstrich ("Das kostet 12 Euro – also etwa 13"), the same aside an em dash marks in English,
# and some engines stop speaking there: Pocket TTS dropped everything after it. So a spaced en dash
# becomes a comma too — unless digits stand on both sides ("10 – 20" is still a range).
_EM_DASH_AS_PAUSE = re.compile(r"\s*—\s*")
_SPACED_EN_DASH = re.compile(r"(?<=(?P<before>\S))\s+–\s+(?=(?P<after>\S))")


def _despeak_em_dash(text: str) -> str:
    """Replaces a prose em dash or spaced en dash with a comma, so it reads as a pause instead of
    "minus" (or as the end of the text)."""
    text = _EM_DASH_AS_PAUSE.sub(", ", text)
    return _SPACED_EN_DASH.sub(
        lambda m: m.group(0) if m.group("before").isdigit() and m.group("after").isdigit() else ", ", text
    )


# A TTS engine reads "CO2"/"CO_2" (the two common ways to write the CO2 molecule; the underscore is
# a LaTeX-style subscript) as if it were one word instead of spelling it out. The underscore variant
# also uses the same character _ITALIC looks for above, but that rule only strips a *paired*
# "_..._" span, so a single subscript underscore like this reaches here untouched.
_CO2_FORMULA = re.compile(r"\bCO_?2\b")
_CO2_NUMBER_WORD: dict[str, str] = {"de": "Zwei", "en": "Two"}


def _spell_out_co2(text: str, language: str) -> str:
    """Rewrites "CO2"/"CO_2" into "C O Zwei"/"C O Two" so the TTS spells it out letter by letter."""
    number_word = _CO2_NUMBER_WORD.get(language, _CO2_NUMBER_WORD[_DEFAULT_LANGUAGE])
    return _CO2_FORMULA.sub(f"C O {number_word}", text)


# (pattern, separator word) per language. Requires a digit immediately on both sides of the
# separator, so it never fires on a prose list ("1, 2, 3") or a sentence ending in a digit
# ("...Punkt 1. Nächstes...").
_DECIMAL_SEPARATOR: dict[str, tuple[re.Pattern[str], str]] = {
    "de": (re.compile(r"(\d+),(\d+)"), "Komma"),
    "en": (re.compile(r"(\d+)\.(\d+)"), "point"),
}

# Math symbol/command -> spoken words, per language. Order matters: a multi-character command
# must come before any shorter rule it overlaps with (e.g. \frac{}{} before the bare "/" rule),
# so the longer pattern gets first pick. To teach the TTS a new symbol, add a row here.
_SYMBOL_RULES: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "de": [
        (re.compile(r"\\frac\{([^{}]+)\}\{([^{}]+)\}"), r"\1 geteilt durch \2"),
        (re.compile(r"\\sqrt\{([^{}]+)\}"), r"Wurzel aus \1"),
        (re.compile(r"\\cdot|\\times|×|·"), "mal"),
        (re.compile(r"\\div|÷"), "geteilt durch"),
        (re.compile(r"\\pm|±"), "plus minus"),
        (re.compile(r"\\pi\b"), "Pi"),
        (re.compile(r"(?<=[\d)])\s*/\s*(?=[\d(])"), " geteilt durch "),
        (re.compile(r"(?<=[\d)])\s*\*\s*(?=[\d(])"), " mal "),
        (re.compile(r"\^"), " hoch "),
    ],
    "en": [
        (re.compile(r"\\frac\{([^{}]+)\}\{([^{}]+)\}"), r"\1 divided by \2"),
        (re.compile(r"\\sqrt\{([^{}]+)\}"), r"the square root of \1"),
        (re.compile(r"\\cdot|\\times|×|·"), "times"),
        (re.compile(r"\\div|÷"), "divided by"),
        (re.compile(r"\\pm|±"), "plus minus"),
        (re.compile(r"\\pi\b"), "pi"),
        (re.compile(r"(?<=[\d)])\s*/\s*(?=[\d(])"), " divided by "),
        (re.compile(r"(?<=[\d)])\s*\*\s*(?=[\d(])"), " times "),
        (re.compile(r"\^"), " to the power of "),
    ],
}


def _spell_out_decimal_digits(text: str, language: str) -> str:
    """Turns "1,23" into "1 Komma 2 3" so the fractional digits are read one by one instead of
    being parsed as a single two/three-digit number."""
    pattern, separator_word = _DECIMAL_SEPARATOR.get(language, _DECIMAL_SEPARATOR[_DEFAULT_LANGUAGE])

    def _replace(match: re.Match[str]) -> str:
        spoken_fraction = " ".join(match.group(2))
        return f"{match.group(1)} {separator_word} {spoken_fraction}"

    return pattern.sub(_replace, text)


def normalize_for_speech(
    text: str,
    language: str,
    pronunciation: PronunciationMatcher | None = None,
    applied: list | None = None,
    verbalizer_rules: Collection[str] | None = None,
) -> str:
    """Rewrites Markdown, decimal numbers, and math symbols in `text` into a form a TTS engine
    reads correctly, and writes numbers, dates, prices and abbreviations out as words.

    `pronunciation` is the teacher's own word list (see pronunciation.py); `applied`, if given,
    collects the rules from it that matched (for the dashboard's test box). `verbalizer_rules`
    picks the verbalizer.py rules to apply; None means settings.tts_verbalizer_rules.
    """
    if verbalizer_rules is None:
        verbalizer_rules = settings.tts_verbalizer_rules
    result = _strip_markdown(text)
    result = _strip_math_delimiters(result)
    # The teacher's rules run after the Markdown/math delimiters are gone (so "**pH**" still
    # matches "pH") but before every built-in rewrite below: their terms are written the way the
    # reply shows them ("1,5 m/s", "E=mc²"), not the way the decimal and symbol rules would
    # already have rewritten them.
    if pronunciation:
        result = pronunciation.apply(result, applied)
    result = _despeak_em_dash(result)
    result = _spell_out_co2(result, language)
    # Prices, dates and units need the raw "12,50 €" before the decimal rule splits it up; the
    # plain number rule needs the symbol rules to have seen the digits first (see verbalizer.py).
    result = verbalize(result, language, "before_symbols", verbalizer_rules)
    result = _spell_out_decimal_digits(result, language)
    for pattern, replacement in _SYMBOL_RULES.get(language, _SYMBOL_RULES[_DEFAULT_LANGUAGE]):
        result = pattern.sub(replacement, result)
    return verbalize(result, language, "after_symbols", verbalizer_rules)
