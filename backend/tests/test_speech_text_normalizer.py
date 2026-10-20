"""Tests for LOLA's normt2s setup (features/ai/tts/normalizer.py). How numbers, units and math are
read is normt2s's own job and tested there; these only check what LOLA configures on top."""

import pytest

from app.features.ai.tts.normalizer import normalize_for_speech, speech_stream


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
        # A multiplication dot outside $...$ is read between operands only (normt2s >= 0.5) —
        # not in a gender-inclusive word.
        ("F = m · a", "de", "F gleich m mal a"),
        ("F = m · a", "en", "F equals m times a"),
        ("Liebe Lehrer·innen", "de", "Liebe Lehrer·innen"),
    ],
)
def test_speaks_math(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language) == expected


def stream_pieces(text: str, step: int = 1) -> list:
    """The pieces speech_stream releases for `text`, fed `step` characters at a time."""
    stream = speech_stream("de")
    pieces = [p for i in range(0, len(text), step) for p in stream.feed_pieces(text[i : i + step])]
    tail = stream.flush_piece()
    return pieces + ([tail] if tail else [])


def test_stream_releases_a_short_first_chunk_then_longer_ones() -> None:
    first = "Das ist der erste Satz hier."  # over the 25-character first-chunk minimum
    rest = "Kurz. " * 10 + "Ende."
    pieces = stream_pieces(f"{first} {rest}")
    assert pieces[0].raw.strip() == first
    # Later chunks wait for 80 characters instead of releasing every short sentence.
    assert all(len(p.raw) >= 80 for p in pieces[1:-1])


def test_stream_never_cuts_inside_a_number_unit_or_abbreviation() -> None:
    text = "Ein Auto fährt hier mit 120 km/h. Dr. Müller sagt, z. B. 3,5 kg sind genug für heute."
    pieces = stream_pieces(text)
    assert "".join(p.raw for p in pieces) == text
    assert "".join(p.spoken for p in pieces) == normalize_for_speech(text, "de")
    for piece in pieces:
        assert not piece.raw.rstrip().endswith(("Dr.", "z.", "B.", "120")), piece


def test_stream_cuts_a_long_run_without_sentence_ends() -> None:
    pieces = stream_pieces("wort " * 120)
    assert len(pieces) >= 2
    assert all(len(p.raw) <= 300 for p in pieces)
