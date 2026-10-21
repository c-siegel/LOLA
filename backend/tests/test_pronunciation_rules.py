"""Unit tests for what LOLA owns of the teacher-defined pronunciation rules: validating a rule
(features/pronunciation/schemas.py::clean_rule), compiling a word list
(features/ai/tts/pronunciation.py) and where it runs inside normalize_for_speech. How a term
matches (whole words, longest first, `{number}`) is normt2s's job and tested there."""

import pytest
from normt2s import Rule

from app.core.error_codes import ErrorCode
from app.features.ai.tts.normalizer import normalize_for_speech
from app.features.ai.tts.pronunciation import compile_rules, spell_out
from app.features.pronunciation.schemas import MAX_SPOKEN_LENGTH, MAX_TERM_LENGTH, clean_rule
from app.features.pronunciation.service import MAX_ENTRIES_PER_LANGUAGE


def test_spell_out() -> None:
    assert spell_out("GPT-4") == "G P T 4"


def test_clean_rule_collapses_whitespace() -> None:
    assert clean_rule("  z.\n B. ", " zum   Beispiel ") == ("z. B.", "zum Beispiel")


def test_clean_rule_spells_out_on_request() -> None:
    assert clean_rule("GPT", "", use_spell_out=True) == ("GPT", "G P T")


@pytest.mark.parametrize(
    ("term", "spoken", "spell", "error"),
    [
        ("", "x", False, ErrorCode.PRONUNCIATION_TERM_INVALID),
        # A bare placeholder would rewrite every number the avatar says.
        ("{number}", "x", False, ErrorCode.PRONUNCIATION_TERM_INVALID),
        ("x" * (MAX_TERM_LENGTH + 1), "x", False, ErrorCode.PRONUNCIATION_TERM_INVALID),
        ("pH", "", False, ErrorCode.PRONUNCIATION_SPOKEN_INVALID),
        ("pH", "x" * (MAX_SPOKEN_LENGTH + 1), False, ErrorCode.PRONUNCIATION_SPOKEN_INVALID),
        ("pH", "{number} p H", False, ErrorCode.PRONUNCIATION_SPOKEN_NUMBER_MISMATCH),
        ("{number} m", "", True, ErrorCode.PRONUNCIATION_SPELL_OUT_WITH_NUMBER),
    ],
)
def test_clean_rule_rejects(term: str, spoken: str, spell: bool, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        clean_rule(term, spoken, spell)


def test_compile_rules_is_cached_by_content() -> None:
    first = compile_rules([Rule("pH", "p H")])
    assert compile_rules((Rule("pH", "p H"),)) is first
    assert compile_rules([Rule("pH", "p H", case_sensitive=True)]) is not first


def test_digits_in_the_spoken_form_are_read_as_numbers() -> None:
    # normt2s says a rule's output as written; LOLA renormalizes a rule whose spoken form has a
    # digit of its own, so "1 s" -> "1 Sekunde" doesn't leave a lone digit among number words.
    rules = compile_rules([Rule("1 s", "1 Sekunde", case_sensitive=True), Rule("pH", "p H 7")])
    assert all(rule.renormalize for rule in rules)
    assert normalize_for_speech("in 1 s", "de", rules) == "in eine Sekunde"
    plain = compile_rules([Rule("{number} m", "{number} Meter"), Rule("pH", "p H")])
    assert not any(rule.renormalize for rule in plain)


def test_lolas_limits_fit_within_normt2s() -> None:
    # A full word list of longest terms must compile — normt2s raises past its own (higher) limits.
    rules = [Rule(f"{i:03d}" + "x" * (MAX_TERM_LENGTH - 3), "y") for i in range(MAX_ENTRIES_PER_LANGUAGE)]
    assert len(compile_rules(rules)) == MAX_ENTRIES_PER_LANGUAGE


def test_runs_after_markdown_and_before_numbers_become_words() -> None:
    rules = compile_rules([Rule("pH", "p H", case_sensitive=True), Rule("{number} m/s", "{number} Meter pro Sekunde")])
    # Bold is stripped before the teacher's term can match, and the number the template carried
    # over is still read as a number.
    assert normalize_for_speech("**pH** bei 1,5 m/s", "de", rules) == "p H bei eins Komma fünf Meter pro Sekunde"


def test_collects_the_rules_that_matched() -> None:
    used, unused = Rule("pH", "p H"), Rule("GPT", "G P T")
    applied: list[Rule] = []
    normalize_for_speech("pH und pH", "de", compile_rules([used, unused]), applied)
    assert applied == [used]


def test_an_empty_word_list_changes_nothing() -> None:
    assert normalize_for_speech("1,5 m/s", "de") == normalize_for_speech("1,5 m/s", "de", compile_rules(()))
