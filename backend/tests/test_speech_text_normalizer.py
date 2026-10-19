import pytest

from app.features.ai.tts.normalizer import normalize_for_speech

# These test the built-in rules on their own, so the verbalizer (numbers as words, see
# test_speech_verbalizer.py) is switched off here and digits stay digits.


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Das Ergebnis ist 1,23.", "de", "Das Ergebnis ist 1 Komma 2 3."),
        ("The result is 1.23.", "en", "The result is 1 point 2 3."),
        ("Pi ist ungefähr 3,14159.", "de", "Pi ist ungefähr 3 Komma 1 4 1 5 9."),
    ],
)
def test_spells_out_decimal_digits(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language, verbalizer_rules=()) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("a \\cdot b", "de", "a mal b"),
        ("a \\cdot b", "en", "a times b"),
        ("a \\times b", "de", "a mal b"),
        ("a \\div b", "de", "a geteilt durch b"),
        ("a \\div b", "en", "a divided by b"),
        ("\\frac{1}{2}", "de", "1 geteilt durch 2"),
        ("\\frac{1}{2}", "en", "1 divided by 2"),
        ("\\sqrt{9}", "de", "Wurzel aus 9"),
        ("3 / 4", "de", "3 geteilt durch 4"),
        ("3 / 4", "en", "3 divided by 4"),
        ("2^3", "de", "2 hoch 3"),
        ("3 × 4", "de", "3 mal 4"),
        ("3 ÷ 4", "de", "3 geteilt durch 4"),
        # A chained multiplication never pairs up as emphasis (see _ITALIC's own comment on
        # _strip_markdown) — spaced or not, it reaches here untouched for this rule to rewrite.
        ("3 * 4 * 5", "de", "3 mal 4 mal 5"),
        ("3*4*5", "de", "3 mal 4 mal 5"),
    ],
)
def test_rewrites_math_symbols(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language, verbalizer_rules=()) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Die Zahlen 1, 2, 3 sind gerade.",
        "Bitte Kapitel 1. Nächstes Thema.",
        "Das machen wir und/oder das.",
        # Underscore italics require a non-word character on both outsides, so this must not be
        # mistaken for emphasis and lose its underscores.
        "Nutze die Variable my_variable_name.",
    ],
)
def test_leaves_unrelated_text_untouched(text: str) -> None:
    assert normalize_for_speech(text, "de", verbalizer_rules=()) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Das ist **wichtig** für dich.", "Das ist wichtig für dich."),
        ("Das ist __wichtig__ für dich.", "Das ist wichtig für dich."),
        ("Das ist *wichtig* für dich.", "Das ist wichtig für dich."),
        ("Das ist _wichtig_ für dich.", "Das ist wichtig für dich."),
        ("**3 * 4** ist 12.", "3 mal 4 ist 12."),
        ("Das ist ~~falsch~~ richtig.", "Das ist falsch richtig."),
        ("Nutze die `print()`-Funktion.", "Nutze die print()-Funktion."),
        ("## Übung 3\nRechne das aus.", "Übung 3\nRechne das aus."),
        ("> Ein Zitat.", "Ein Zitat."),
        ("- Erster Punkt\n- Zweiter Punkt", "Erster Punkt\nZweiter Punkt"),
        ("1. Erster Schritt\n2. Zweiter Schritt", "Erster Schritt\nZweiter Schritt"),
        ("Mehr dazu: [hier](https://example.com).", "Mehr dazu: hier."),
        ("Text davor\n---\nText danach", "Text davor\n\nText danach"),
        ("```python\nprint(1)\n```", "print(1)\n"),
    ],
)
def test_strips_markdown(text: str, expected: str) -> None:
    assert normalize_for_speech(text, "de", verbalizer_rules=()) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Das Ergebnis — ein Erfolg — war klar.", "de", "Das Ergebnis, ein Erfolg, war klar."),
        ("5 - 3 = 2", "de", "5 - 3 = 2"),
        # En dash is left alone — see _despeak_em_dash's docstring comment: it's normally a range
        # ("10 to 20"), and rewriting it to a comma would silently turn that into a list.
        ("Von 10–20 Minuten.", "de", "Von 10–20 Minuten."),
    ],
)
def test_despeaks_em_dash_without_touching_minus(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language, verbalizer_rules=()) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Die Formel ist $$a \\cdot b$$.", "de", "Die Formel ist a mal b."),
        ("Die Formel ist $a \\cdot b$.", "de", "Die Formel ist a mal b."),
        ("The formula is $$1.23$$.", "en", "The formula is 1 point 2 3."),
        ("$$\\frac{1}{2}$$ ist ein halb.", "de", "1 geteilt durch 2 ist ein halb."),
    ],
)
def test_strips_math_delimiters(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language, verbalizer_rules=()) == expected


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        ("Pflanzen nehmen CO2 auf.", "de", "Pflanzen nehmen C O Zwei auf."),
        ("Pflanzen nehmen CO_2 auf.", "de", "Pflanzen nehmen C O Zwei auf."),
        ("Plants absorb CO2.", "en", "Plants absorb C O Two."),
        ("Plants absorb CO_2.", "en", "Plants absorb C O Two."),
        # Must not fire inside a larger token or on a different number.
        ("ECO2000 ist ein Gerät.", "de", "ECO2000 ist ein Gerät."),
        ("CO23 ist keine Formel.", "de", "CO23 ist keine Formel."),
    ],
)
def test_spells_out_co2(text: str, language: str, expected: str) -> None:
    assert normalize_for_speech(text, language, verbalizer_rules=()) == expected
