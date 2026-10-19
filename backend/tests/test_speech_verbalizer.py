"""The German verbalizer (features/ai/tts/verbalizer.py) as it runs inside normalize_for_speech:
numbers, dates, prices, units and abbreviations written out as words, each rule switchable."""

import pytest
from pydantic import ValidationError

from app.core.config import TTS_VERBALIZER_RULE_NAMES, Settings, settings
from app.features.ai.tts.normalizer import normalize_for_speech
from app.features.ai.tts.verbalizer import RULE_NAMES, cardinal, ordinal, year
from app.features.pronunciation import presets
from app.features.pronunciation.service import compile_rules

ALL = TTS_VERBALIZER_RULE_NAMES


def speak(text: str, rules=ALL) -> str:
    return normalize_for_speech(text, "de", verbalizer_rules=rules)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "null"),
        (1, "eins"),
        (16, "sechzehn"),
        (17, "siebzehn"),
        (21, "einundzwanzig"),
        (30, "dreißig"),
        (99, "neunundneunzig"),
        (100, "hundert"),
        (101, "hunderteins"),
        (111, "hundertelf"),
        (1000, "tausend"),
        (1001, "tausendeins"),
        (21000, "einundzwanzigtausend"),
        (101000, "hunderteintausend"),
        (1_000_000, "eine Million"),
        (2_300_005, "zwei Millionen dreihunderttausendfünf"),
        (1_000_000_000, "eine Milliarde"),
    ],
)
def test_cardinal(n: int, expected: str) -> None:
    assert cardinal(n) == expected


@pytest.mark.parametrize(
    ("n", "ending", "expected"),
    [
        (1, "e", "erste"),
        (3, "en", "dritten"),
        (7, "e", "siebte"),
        (8, "en", "achten"),
        (19, "en", "neunzehnten"),
        (20, "e", "zwanzigste"),
        (21, "en", "einundzwanzigsten"),
        (100, "e", "hundertste"),
        (101, "e", "hunderterste"),
    ],
)
def test_ordinal(n: int, ending: str, expected: str) -> None:
    assert ordinal(n, ending) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1789, "siebzehnhundertneunundachtzig"), (1900, "neunzehnhundert"), (1901, "neunzehnhunderteins"), (2024, "zweitausendvierundzwanzig")],
)
def test_year(n: int, expected: str) -> None:
    assert year(n) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The sentences Pocket TTS garbled in the phase 0 test (docs/pocket-tts-plan.md).
        ("Am 3. Oktober 1990 wurde Deutschland wiedervereinigt.", "Am dritten Oktober neunzehnhundertneunzig wurde Deutschland wiedervereinigt."),
        ("Das kostet 12,50 € – also etwa 13 Euro.", "Das kostet zwölf Euro fünfzig, also etwa dreizehn Euro."),
        (
            "Bitte lies S. 47 bis 52, z. B. den Abschnitt über die Weimarer Republik.",
            "Bitte lies Seite siebenundvierzig bis zweiundfünfzig, zum Beispiel den Abschnitt über die Weimarer Republik.",
        ),
        ("Die Wurzel aus 144 ist 12, und 3² ergibt 9.", "Die Wurzel aus hundertvierundvierzig ist zwölf, und drei hoch zwei ergibt neun."),
        (
            "Im Jahr 2024 lebten rund 84 Mio. Menschen in Deutschland.",
            "Im Jahr zweitausendvierundzwanzig lebten rund vierundachtzig Millionen Menschen in Deutschland.",
        ),
        ("Die Französische Revolution begann 1789 mit dem Sturm.", "Die Französische Revolution begann siebzehnhundertneunundachtzig mit dem Sturm."),
        ("Eine Zelle besteht u. a. aus Zellkern.", "Eine Zelle besteht unter anderem aus Zellkern."),
    ],
)
def test_phase0_sentences(text: str, expected: str) -> None:
    assert speak(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Die Zahlen 1, 2, 3.", "Die Zahlen eins, zwei, drei."),
        ("Bitte Kapitel 1. Nächstes Thema.", "Bitte Kapitel eins. Nächstes Thema."),
        ("Es waren 84.000 Leute.", "Es waren vierundachtzigtausend Leute."),
        ("Das ergibt 1,23.", "Das ergibt eins Komma zwei drei."),
        ("1,5 Liter", "eins Komma fünf Liter"),
        ("Das ist -7.", "Das ist minus sieben."),
        ("5 - 3 ist 2.", "fünf - drei ist zwei."),
        ("3^2 und 1/2 und 3 * 4", "drei hoch zwei und eins geteilt durch zwei und drei mal vier"),
        ("1 Apfel und 1 Sekunde", "ein Apfel und eine Sekunde"),
        ("1500 Menschen kamen im Jahr 1500 an.", "tausendfünfhundert Menschen kamen im Jahr fünfzehnhundert an."),
        ("in den 1990er Jahren und den 20er Jahren", "in den neunzehnhundertneunziger Jahren und den zwanziger Jahren"),
        ("Vorwahl 0049, Nummer 123456789012345", "Vorwahl null null vier neun, Nummer eins zwei drei vier fünf sechs sieben acht neun null eins zwei drei vier fünf"),
        # Glued to a word: a name or formula, not a number to read out.
        ("ECO2000 und CO23 und 2m", "ECO2000 und CO23 und 2m"),
    ],
)
def test_numbers(text: str, expected: str) -> None:
    assert speak(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("der 1. Mai", "der erste Mai"),
        ("Heute ist der 03.10.1990.", "Heute ist der dritte Oktober neunzehnhundertneunzig."),
        ("am 24.12.", "am vierundzwanzigsten Dezember"),
        ("der 2. Weltkrieg", "der zweite Weltkrieg"),
        ("im 19. Jh. Danach kam", "im neunzehnten Jahrhundert. Danach kam"),
        # Not a date and not a number to read as one: a version number stays as written.
        ("Version 1.2 ist neu.", "Version 1.2 ist neu."),
    ],
)
def test_dates_and_ordinals(text: str, expected: str) -> None:
    assert speak(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("um 14:30 Uhr", "um vierzehn Uhr dreißig"),
        ("um 1.00 Uhr", "um ein Uhr"),
        ("um 8 Uhr", "um acht Uhr"),
        ("10–20 Minuten", "zehn bis zwanzig Minuten"),
        ("0,99 €", "neunundneunzig Cent"),
        ("1 €", "ein Euro"),
        ("€ 1.200,-", "tausendzweihundert Euro"),
        ("10–20 €", "zehn bis zwanzig Euro"),
        ("5 km und 1 km/h", "fünf Kilometer und ein Kilometer pro Stunde"),
        ("-5 °C und 20°C", "minus fünf Grad Celsius und zwanzig Grad Celsius"),
        ("2,5 % von 25 m²", "zwei Komma fünf Prozent von fünfundzwanzig Quadratmeter"),
        ("1 Std. bzw. 90 Min.", "eine Stunde beziehungsweise neunzig Minuten."),
        ("1 Mio. oder 2,5 Mrd.", "eine Million oder zwei Komma fünf Milliarden."),
        ("Das ist usw. Danach etc.", "Das ist und so weiter. Danach et cetera."),
        ("Regel Nr. 5, Abb. 3, Kap. 2", "Regel Nummer fünf, Abbildung drei, Kapitel zwei"),
        # "S." is only "Seite" in front of a number.
        ("Peter S. kam.", "Peter S. kam."),
    ],
)
def test_times_ranges_currency_units_abbreviations(text: str, expected: str) -> None:
    assert speak(text) == expected


@pytest.mark.parametrize(
    ("rule", "text", "without_rule"),
    [
        ("abbreviations", "z. B. hier", "z. B. hier"),
        # "bis" isn't one of the ordinals rule's articles ("am 3. Oktober" would still be an ordinal).
        ("dates", "bis 3. Oktober", "bis drei. Oktober"),
        ("times", "um 14:30 Uhr", "um vierzehn:dreißig Uhr"),
        ("ranges", "10–20 Minuten", "zehn–zwanzig Minuten"),
        ("currency", "13 €", "dreizehn €"),
        ("units", "5 km", "fünf km"),
        ("ordinals", "der 2. Weltkrieg", "der zwei. Weltkrieg"),
        ("powers", "3²", "3²"),
        ("numbers", "84 Leute", "84 Leute"),
    ],
)
def test_each_rule_switches_off_on_its_own(rule: str, text: str, without_rule: str) -> None:
    assert speak(text, ALL - {rule}) == without_rule
    assert speak(text) != without_rule


def test_none_is_the_old_behaviour() -> None:
    text = "Am 3. Oktober kostet es 12,50 €, z. B. 3² mal."
    assert speak(text, frozenset()) == "Am 3. Oktober kostet es 12 Komma 5 0 €, z. B. 3² mal."


def test_other_languages_are_untouched() -> None:
    assert normalize_for_speech("It costs 12 euros in 1990.", "en", verbalizer_rules=ALL) == "It costs 12 euros in 1990."


def test_teacher_rules_run_first() -> None:
    matcher = compile_rules(presets.get_pack("physics", "de").entries)
    assert (
        normalize_for_speech("m = 5 kg, F = 1 N in 1 s, bei 1013 hPa", "de", matcher, verbalizer_rules=ALL)
        == "m = fünf Kilogramm, F = ein Newton in eine Sekunde, bei tausenddreizehn Hektopascal"
    )


def test_default_comes_from_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tts_verbalizer", "none")
    assert normalize_for_speech("84 Leute", "de") == "84 Leute"
    monkeypatch.setattr(settings, "tts_verbalizer", "numbers")
    assert normalize_for_speech("84 Leute, 5 km", "de") == "vierundachtzig Leute, fünf km"
    monkeypatch.setattr(settings, "tts_verbalizer", "all")
    assert normalize_for_speech("84 Leute, 5 km", "de") == "vierundachtzig Leute, fünf Kilometer"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("all", ALL), ("ALL", ALL), ("none", frozenset()), ("", frozenset()), ("numbers, currency", {"numbers", "currency"})],
)
def test_setting_values(value: str, expected) -> None:
    assert Settings(tts_verbalizer=value).tts_verbalizer_rules == expected


def test_unknown_rule_fails_at_startup() -> None:
    with pytest.raises(ValidationError, match="numbres"):
        Settings(tts_verbalizer="numbres")


def test_config_lists_exactly_the_verbalizer_rules() -> None:
    assert TTS_VERBALIZER_RULE_NAMES == RULE_NAMES
