"""Tests for LOLA's normt2s setup (features/ai/tts/normalizer.py). How numbers, units and math are
read is normt2s's own job and tested there; these only check what LOLA configures on top."""

import pytest

from app.features.ai.tts.normalizer import normalize_for_speech


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Das ist **wichtig** für dich.", "Das ist wichtig für dich."),
        ("## Übung\n- Erster Punkt\n- Zweiter Punkt", "Übung\nErster Punkt\nZweiter Punkt"),
        ("Mehr dazu: [hier](https://example.com).", "Mehr dazu: hier."),
        ("Nutze die Variable my_variable_name.", "Nutze die Variable my_variable_name."),
    ],
)
def test_strips_markdown(text: str, expected: str) -> None:
    assert normalize_for_speech(text, "de") == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("::wave_right:: Hallo! ::done_2:: Weiter ::undone_10:: so.", "de", "Hallo! Weiter so."),
        (":: nod_yes :: Yes.", "en", "Yes."),
        # Code with "::" is no marker (same rule as features/chat/motion.py).
        ("Nutze std::vector::size.", "de", "Nutze std::vector::size."),
    ],
)
def test_markers_are_silent(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Es sind 1,5 m/s.", "de", "Es sind eins Komma fünf Meter pro Sekunde."),
        ("It is 1.5 m/s.", "en", "It is one point five meters per second."),
        # Unknown languages fall back to German.
        ("Es sind 1,5 m/s.", "fr", "Es sind eins Komma fünf Meter pro Sekunde."),
    ],
)
def test_speaks_each_language(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        # A German decimal comma inside math is a decimal, not a list (normt2s >= 0.4.1).
        ("Die Formel ist $$1,23 \\cdot x$$.", "de", "Die Formel ist eins Komma zwei drei mal x."),
        ("The formula is $1.23 \\cdot x$.", "en", "The formula is one point two three times x."),
        # LLMs often write LaTeX without $...$ — it is read, not spelled out.
        ("\\frac{1}{2} und 3 \\cdot 4", "de", "ein halb und drei mal vier"),
        # A multiplication dot outside $...$ is LOLA's own addition (see _MULTIPLICATION_DOT).
        ("F = m · a", "de", "F gleich m mal a"),
        ("F = m · a", "en", "F equals m times a"),
    ],
)
def test_speaks_math(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language) == expected
