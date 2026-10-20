"""Unit tests for the teacher-defined pronunciation rules (features/ai/tts/pronunciation.py) and
where they run inside normalize_for_speech."""

import pytest

from app.features.ai.tts.normalizer import normalize_for_speech
from app.features.ai.tts.pronunciation import PronunciationRule, compile_rules, spell_out


def apply(text: str, *rules: PronunciationRule) -> str:
    return compile_rules(rules).apply(text)


def test_replaces_a_plain_term() -> None:
    assert apply("Der pH-Wert ist 7.", PronunciationRule("pH", "p H")) == "Der p H-Wert ist 7."


def test_case_insensitive_by_default_and_sensitive_on_request() -> None:
    assert apply("GPT und gpt", PronunciationRule("gpt", "G P T")) == "G P T und G P T"
    assert apply("GPT und gpt", PronunciationRule("GPT", "G P T", case_sensitive=True)) == "G P T und gpt"


def test_whole_word_does_not_match_inside_a_longer_word() -> None:
    rule = PronunciationRule("N", "Newton", case_sensitive=True)
    assert apply("Neu: 5 N", rule) == "Neu: 5 Newton"


def test_whole_word_still_matches_a_unit_glued_to_its_number() -> None:
    assert apply("10kg Mehl", PronunciationRule("kg", "Kilogramm")) == "10 Kilogramm Mehl"


def test_non_whole_word_matches_inside_a_word_and_pads_with_a_space() -> None:
    rule = PronunciationRule("Δ", "Delta", whole_word=False)
    assert apply("Δt = 2 s", rule) == "Delta t = 2 s"


def test_symbol_edges_need_no_word_boundary() -> None:
    assert apply("Es sind 20°C.", PronunciationRule("°C", "Grad Celsius")) == "Es sind 20 Grad Celsius."


def test_longest_term_wins() -> None:
    rules = (PronunciationRule("km", "Kilometer"), PronunciationRule("km/h", "Kilometer pro Stunde"))
    assert apply("50 km/h, 3 km", *rules) == "50 Kilometer pro Stunde, 3 Kilometer"


def test_single_pass_never_rewrites_a_replacement_again() -> None:
    rules = (PronunciationRule("A", "B", case_sensitive=True), PronunciationRule("B", "C", case_sensitive=True))
    assert apply("A B", *rules) == "B C"


def test_number_placeholder_is_carried_over() -> None:
    rule = PronunciationRule("{number} m", "{number} Meter", case_sensitive=True)
    assert apply("Er läuft 1,5 m weit, m bleibt.", rule) == "Er läuft 1,5 Meter weit, m bleibt."


def test_number_placeholder_does_not_start_inside_a_number() -> None:
    rules = (
        PronunciationRule("1 s", "1 Sekunde", case_sensitive=True),
        PronunciationRule("{number} s", "{number} Sekunden", case_sensitive=True),
    )
    assert apply("1 s und 21 s", *rules) == "1 Sekunde und 21 Sekunden"


def test_several_placeholders_fill_in_order() -> None:
    rule = PronunciationRule("{number}x{number}", "{number} mal {number}")
    assert apply("ein 3x4 Raster", rule) == "ein 3 mal 4 Raster"


def test_a_space_in_the_term_matches_any_whitespace() -> None:
    assert apply("z.\nB. so", PronunciationRule("z. B.", "zum Beispiel")) == "zum Beispiel so"


@pytest.mark.parametrize("term", [".*", "(a|b)", "a+", "[x]", "\\d"])
def test_terms_are_never_treated_as_regex(term: str) -> None:
    text = f"vor {term} nach abc 123"
    assert apply(text, PronunciationRule(term, "X", whole_word=False)) == "vor X nach abc 123"


def test_spoken_form_is_inserted_literally() -> None:
    assert apply("pH", PronunciationRule("pH", r"\1 $0")) == r"\1 $0"


def test_collects_the_rules_that_matched() -> None:
    used = PronunciationRule("pH", "p H")
    unused = PronunciationRule("GPT", "G P T")
    applied: list[PronunciationRule] = []
    compile_rules((used, unused)).apply("pH und pH", applied)
    assert applied == [used]


def test_no_rules_leaves_text_alone() -> None:
    assert apply("unverändert") == "unverändert"


def test_spell_out() -> None:
    assert spell_out("GPT-4") == "G P T 4"


def test_runs_after_markdown_and_before_the_builtin_rules() -> None:
    matcher = compile_rules(
        (
            PronunciationRule("pH", "p H", case_sensitive=True),
            PronunciationRule("{number} m/s", "{number} Meter pro Sekunde"),
        )
    )
    # Bold is stripped before the teacher's term can match, and the number the template carried
    # over is still read as a number.
    assert (
        normalize_for_speech("**pH** bei 1,5 m/s", "de", matcher) == "p H bei eins Komma fünf Meter pro Sekunde"
    )


def test_normalize_without_rules_is_unchanged() -> None:
    assert normalize_for_speech("1,5 m/s", "de") == normalize_for_speech("1,5 m/s", "de", compile_rules(()))
