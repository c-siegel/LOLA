"""
Speech Verbalizer (German)

Writes numbers, dates, times, prices, units and common abbreviations out as words before the text
reaches a TTS engine. Some engines read "1990", "12,50 €" or "z. B." correctly on their own, others
don't: Pocket TTS (see docs/pocket-tts-plan.md) turned "Das kostet 12,50 €" into nonsense, while
the same sentence written out in words came out right. Spelling it out here gives every engine the
same, unambiguous input. Like the rest of normalizer.py, this only changes the copy of the text
sent to the TTS engine, never what the chat displays.

Each rule can be switched on and off on its own (settings.tts_verbalizer, env TTS_VERBALIZER: "all",
"none" or a comma-separated list of the names in RULE_NAMES). Languages without rules here pass
through unchanged.

The rules run in two stages around normalizer.py's built-in decimal and symbol rules:
- before them, the rules that need the raw notation ("12,50 €", "3.10.1990", "z. B.");
- after them, the plain number rule — the symbol rules only recognise "/", "*" and "^" between
  digits, so the digits must still be there when those run ("3^2" -> "3 hoch 2" -> "drei hoch zwei").

How to use (normally only through normalize_for_speech):
    from app.features.ai.tts.verbalizer import verbalize

    text = verbalize("Am 3. Oktober 1990", "de", "before_symbols", {"dates"})
"""

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Literal

Stage = Literal["before_symbols", "after_symbols"]

# ==================== NUMBER WORDS ====================

_ONES = (
    "null eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn vierzehn fünfzehn "
    "sechzehn siebzehn achtzehn neunzehn"
).split()
_TENS = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig", 7: "siebzig", 8: "achtzig", 9: "neunzig"}

# Longer than this (or with a leading zero, like a phone number or "007") a digit string is read
# digit by digit — nobody says "zwölf Billionen…" for an ID.
_MAX_CARDINAL_DIGITS = 12


def _below_hundred(n: int, final: bool) -> str:
    """1–99. `final` is False when more follows ("ein" in "einundzwanzig", "hundertein-tausend")."""
    if n < 20:
        return "ein" if n == 1 and not final else _ONES[n]
    tens, ones = divmod(n, 10)
    if not ones:
        return _TENS[tens]
    return ("ein" if ones == 1 else _ONES[ones]) + "und" + _TENS[tens]


def _below_thousand(n: int, final: bool) -> str:
    hundreds, rest = divmod(n, 100)
    word = ""
    if hundreds:
        word = ("" if hundreds == 1 else _below_hundred(hundreds, False)) + "hundert"
    if rest:
        word += _below_hundred(rest, final)
    return word


def cardinal(n: int) -> str:
    """0 -> "null", 21 -> "einundzwanzig", 1001 -> "tausendeins", 2_300_000 -> "zwei Millionen dreihunderttausend"."""
    if n == 0:
        return "null"
    billions, rest = divmod(n, 10**9)
    millions, rest = divmod(rest, 10**6)
    thousands, rest = divmod(rest, 1000)
    words = []
    for count, singular, plural in ((billions, "eine Milliarde", "Milliarden"), (millions, "eine Million", "Millionen")):
        if count:
            words.append(singular if count == 1 else f"{_below_thousand(count, True)} {plural}")
    tail = ""
    if thousands:
        tail = ("" if thousands == 1 else _below_thousand(thousands, False)) + "tausend"
    if rest:
        tail += _below_thousand(rest, True)
    if tail:
        words.append(tail)
    return " ".join(words)


_ORDINAL_STEMS = {1: "erst", 3: "dritt", 7: "siebt", 8: "acht"}


def ordinal(n: int, ending: str) -> str:
    """3, "en" -> "dritten"; 21, "e" -> "einundzwanzigste"; 100, "e" -> "hundertste"."""
    rest = n % 100
    if rest == 0:
        return cardinal(n) + "st" + ending
    head = cardinal(n - rest) if n >= 100 else ""
    stem = _ORDINAL_STEMS.get(rest, _ONES[rest] + "t") if rest < 20 else _below_hundred(rest, True) + "st"
    return head + stem + ending


def year(n: int) -> str:
    """1100–1999 the way a year is said: 1990 -> "neunzehnhundertneunzig". Other years are plain cardinals."""
    if not 1100 <= n <= 1999:
        return cardinal(n)
    centuries, rest = divmod(n, 100)
    return _below_hundred(centuries, False) + "hundert" + (_below_hundred(rest, True) if rest else "")


def _digits(text: str) -> str:
    return " ".join(_ONES[int(d)] for d in text)


def say_integer(text: str) -> str:
    """A digit string, optionally grouped with "." ("84.000"), as words."""
    plain = text.replace(".", "")
    if (len(plain) > 1 and plain.startswith("0")) or len(plain) > _MAX_CARDINAL_DIGITS:
        return _digits(plain)
    return cardinal(int(plain))


def say_amount(integer: str, fraction: str | None, one: str = "ein") -> str:
    """A quantity in front of a unit: "1" -> `one`, "2,5" -> "zwei Komma fünf" (decimals digit by
    digit, as normalizer.py's decimal rule reads them)."""
    if fraction:
        return f"{say_integer(integer)} Komma {_digits(fraction)}"
    return one if integer == "1" else say_integer(integer)


# ==================== SHARED PATTERNS ====================

# An integer, optionally with German thousands separators ("84.000", "1.000.000").
_INT = r"\d{1,3}(?:\.\d{3})+(?!\d)|\d+"
# Not glued to a preceding word, digit or separator ("ECO2000", "3,14", "1.5").
_START = r"(?<![\w,.])"
# A sign directly attached to the number, at the start or after a space/bracket ("-5 °C"); a spaced
# "5 - 3" stays a subtraction.
_SIGN = r"(?P<sign>(?<![^\s(])[-−])?"
_CAPITALIZED_WORD = re.compile(r"\s+([A-ZÄÖÜ][\w-]*)")

_MONTHS = (
    "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split()
)
_MONTH_PATTERN = "|".join(_MONTHS)

# Feminine nouns that commonly follow a "1": "eine Sekunde", not "ein Sekunde". Every other
# capitalized word gets "ein" — right for masculine/neuter nouns and for the units this module
# writes out itself (all masculine/neuter: der Meter, das Gramm, das Prozent).
_FEMININE_NOUNS = frozenset(
    "Million Milliarde Sekunde Minute Stunde Woche Tonne Seite Person Frage Aufgabe Antwort Zahl "
    "Klasse Stelle Zelle Note Runde Lösung Möglichkeit Kilowattstunde Nummer Folie Übung Stimme".split()
)


def _sign_word(match: re.Match[str]) -> str:
    return "minus " if match.group("sign") else ""


def _previous_word(match: re.Match[str]) -> str:
    found = re.search(r"(\w+)\W*$", match.string[: match.start()])
    return found.group(1).lower() if found else ""


# "der 3. Oktober" / "die 2. Klasse" take "-e", everything else ("am 3. Oktober", "im 19.
# Jahrhundert", "seit dem 1. Mai") takes "-en" — the more common form in running text.
_E_ENDING_ARTICLES = frozenset({"der", "die", "das"})


def _ordinal_ending(match: re.Match[str]) -> str:
    return "e" if _previous_word(match) in _E_ENDING_ARTICLES else "en"


# ==================== RULES ====================


def _abbreviation_pattern(abbreviation: str) -> str:
    """ "z. B." matches "z. B.", "z.B." and "z.  B."."""
    return r"\s*".join(re.escape(part) + r"\." for part in abbreviation.replace(" ", "").split(".") if part)


# (abbreviation, spoken). Case-sensitive on purpose: "S." is "Seite" only in front of a number.
_ABBREVIATIONS: tuple[tuple[str, str], ...] = (
    ("z. B.", "zum Beispiel"),
    ("d. h.", "das heißt"),
    ("u. a.", "unter anderem"),
    ("u. U.", "unter Umständen"),
    ("o. Ä.", "oder Ähnliches"),
    ("u. Ä.", "und Ähnliches"),
    ("v. a.", "vor allem"),
    ("z. T.", "zum Teil"),
    ("i. d. R.", "in der Regel"),
    ("s. o.", "siehe oben"),
    ("s. u.", "siehe unten"),
    ("v. Chr.", "vor Christus"),
    ("n. Chr.", "nach Christus"),
    ("bzw.", "beziehungsweise"),
    ("usw.", "und so weiter"),
    ("etc.", "et cetera"),
    ("ca.", "circa"),
    ("vgl.", "vergleiche"),
    ("ggf.", "gegebenenfalls"),
    ("evtl.", "eventuell"),
    ("inkl.", "inklusive"),
    ("exkl.", "exklusive"),
    ("bspw.", "beispielsweise"),
    ("max.", "maximal"),
    ("Dr.", "Doktor"),
    ("Prof.", "Professor"),
    ("Jh.", "Jahrhundert"),
    ("Jhd.", "Jahrhundert"),
    ("Tsd.", "Tausend"),
)
# Only an abbreviation for a word in front of a number: "S. 47", but "S." alone may be an initial.
_NUMBER_ABBREVIATIONS: tuple[tuple[str, str], ...] = (
    ("S.", "Seite"),
    ("Nr.", "Nummer"),
    ("Abb.", "Abbildung"),
    ("Kap.", "Kapitel"),
    ("Tab.", "Tabelle"),
    ("Bd.", "Band"),
)
# After a number, singular for exactly "1": "1 Mio." -> "1 Million", "84 Mio." -> "84 Millionen".
_COUNTED_ABBREVIATIONS: tuple[tuple[str, str, str], ...] = (
    ("Mio.", "Million", "Millionen"),
    ("Mrd.", "Milliarde", "Milliarden"),
    ("Std.", "Stunde", "Stunden"),
    ("Min.", "Minute", "Minuten"),
    ("Sek.", "Sekunde", "Sekunden"),
)
# These often end a sentence. Their period is kept when a new sentence follows (a capitalized word)
# so the TTS still pauses there; for all others a following capital is just a German noun.
_SENTENCE_FINAL = frozenset({"usw.", "etc.", "o. Ä.", "u. Ä.", "s. o.", "s. u.", "v. Chr.", "n. Chr.", "Jh.", "Jhd."})

_ABBREVIATION_RULES = [
    (re.compile(r"(?<![\w.])" + _abbreviation_pattern(abbr) + r"(?P<after>\s*$|\s+(?=[A-ZÄÖÜ]))?", re.M), spoken, abbr)
    for abbr, spoken in _ABBREVIATIONS
] + [
    (re.compile(r"(?<![\w.])" + _abbreviation_pattern(abbr) + r"(?=\s*\d)"), spoken, abbr)
    for abbr, spoken in _NUMBER_ABBREVIATIONS
]
_COUNTED_RULES = [
    (
        re.compile(_START + rf"(?P<n>{_INT})(?:,(?P<frac>\d+))?\s*" + _abbreviation_pattern(abbr) + r"(?P<end>(?=[ \t]*$))?", re.M),
        singular,
        plural,
    )
    for abbr, singular, plural in _COUNTED_ABBREVIATIONS
]


def _counted(match: re.Match[str], singular: str, plural: str) -> str:
    """Keeps the number as written (the number rule reads it later) and picks the noun's number.
    At the end of the text/line the abbreviation's period also ended the sentence, so it stays."""
    number = match.group("n") + (f",{match.group('frac')}" if match.group("frac") else "")
    return f"{number} {singular if number == '1' else plural}" + ("." if match.group("end") is not None else "")


def _abbreviations(text: str) -> str:
    for pattern, singular, plural in _COUNTED_RULES:
        text = pattern.sub(lambda m, s=singular, p=plural: _counted(m, s, p), text)
    for pattern, spoken, abbr in _ABBREVIATION_RULES:

        def _replace(match: re.Match[str], spoken: str = spoken, abbr: str = abbr) -> str:
            after = match.groupdict().get("after")
            if after is None:
                return spoken
            # End of the text/line, or (for typically sentence-final ones) a new sentence: keep a stop.
            keep_period = not after.strip(" \t") or abbr in _SENTENCE_FINAL
            return spoken + ("." if keep_period else "") + after

        text = pattern.sub(_replace, text)
    return text


_DAY_MONTH = re.compile(rf"(?<![\w.])(?P<day>[12]?\d|3[01])\.\s*(?P<month>{_MONTH_PATTERN})\b")
_NUMERIC_DATE = re.compile(r"(?<![\w.,])(?P<day>0?[1-9]|[12]\d|3[01])\.(?P<month>0?[1-9]|1[0-2])\.(?P<year>\d{4}|\d{2})?(?![\d.]\d)")


def _dates(text: str) -> str:
    """ "am 3. Oktober" -> "am dritten Oktober"; "03.10.1990" -> "dritten Oktober 1990" (the year
    is left as digits for the number rule, which reads it as a year after a month name)."""
    text = _DAY_MONTH.sub(
        lambda m: f"{ordinal(int(m.group('day')), _ordinal_ending(m))} {m.group('month')}", text
    )

    def _numeric(match: re.Match[str]) -> str:
        spoken = f"{ordinal(int(match.group('day')), _ordinal_ending(match))} {_MONTHS[int(match.group('month')) - 1]}"
        return spoken + (f" {match.group('year')}" if match.group("year") else "")

    return _NUMERIC_DATE.sub(_numeric, text)


_TIME = re.compile(r"(?<![\w.,:])(?P<h>[01]?\d|2[0-3])[:.](?P<m>[0-5]\d)\s*Uhr\b")


def _times(text: str) -> str:
    """ "14:30 Uhr" -> "vierzehn Uhr dreißig", "1.00 Uhr" -> "ein Uhr"."""

    def _replace(match: re.Match[str]) -> str:
        hour = int(match.group("h"))
        minutes = int(match.group("m"))
        spoken = f"{'ein' if hour == 1 else cardinal(hour)} Uhr"
        return spoken + (f" {cardinal(minutes)}" if minutes else "")

    return _TIME.sub(_replace, text)


# An en dash between two numbers is a range ("10–20 Minuten"); normalizer.py deliberately leaves the
# en dash alone so it isn't mistaken for a pause, and this says what it means.
_RANGE = re.compile(r"(?<=\d)\s*–\s*(?=[-−]?\d)")


def _ranges(text: str) -> str:
    return _RANGE.sub(" bis ", text)


_EURO_AFTER = re.compile(_START + rf"(?P<n>{_INT})(?:,(?P<cents>\d{{1,2}}|--?|–))?\s*(?:€|EUR\b|Euro\b)")
_EURO_BEFORE = re.compile(rf"€\s*(?P<n>{_INT})(?:,(?P<cents>\d{{1,2}}|--?|–))?(?!\d)")


def _say_euro(match: re.Match[str]) -> str:
    euros = int(match.group("n").replace(".", ""))
    cents_text = match.group("cents") or ""
    cents = int(cents_text.ljust(2, "0")) if cents_text.isdigit() else 0
    if euros == 0 and cents:
        return f"{cardinal(cents)} Cent"
    spoken = f"{say_amount(match.group('n'), None)} Euro"
    return spoken + (f" {cardinal(cents)}" if cents else "")


def _currency(text: str) -> str:
    """ "12,50 €" -> "zwölf Euro fünfzig", "0,99 €" -> "neunundneunzig Cent", "€ 5" -> "fünf Euro"."""
    return _EURO_BEFORE.sub(_say_euro, _EURO_AFTER.sub(_say_euro, text))


# (written, spoken), longest first so "km/h" wins over "km" and "°C" over "°". All spoken forms
# stay the same in the plural, so only "1" needs care ("ein Meter").
_UNITS: tuple[tuple[str, str], ...] = (
    ("km/h", "Kilometer pro Stunde"),
    ("m/s", "Meter pro Sekunde"),
    ("km²", "Quadratkilometer"),
    ("cm²", "Quadratzentimeter"),
    ("m²", "Quadratmeter"),
    ("cm³", "Kubikzentimeter"),
    ("m³", "Kubikmeter"),
    ("°C", "Grad Celsius"),
    ("°F", "Grad Fahrenheit"),
    ("km", "Kilometer"),
    ("cm", "Zentimeter"),
    ("mm", "Millimeter"),
    ("kg", "Kilogramm"),
    ("mg", "Milligramm"),
    ("ml", "Milliliter"),
    ("%", "Prozent"),
    ("‰", "Promille"),
    ("°", "Grad"),
    ("m", "Meter"),
    ("g", "Gramm"),
    ("l", "Liter"),
)
_UNIT_WORDS = dict(_UNITS)
# Single letters are only units after a space ("5 m"): "2m" in a formula is more often 2·m.
_NEEDS_SPACE = frozenset({"m", "g", "l"})
_UNIT = re.compile(
    _START
    + _SIGN
    + rf"(?P<n>{_INT})(?:,(?P<frac>\d+))?(?P<space>\s*)(?P<unit>"
    + "|".join(re.escape(w).replace("°", r"°\s?") for w, _ in _UNITS)
    + r")(?![\w²³/])"
)


def _units(text: str) -> str:
    """ "5 km" -> "fünf Kilometer", "-5 °C" -> "minus fünf Grad Celsius", "2,5 %" -> "zwei Komma fünf Prozent"."""

    def _replace(match: re.Match[str]) -> str:
        unit = re.sub(r"\s", "", match.group("unit"))
        if unit in _NEEDS_SPACE and not match.group("space"):
            return match.group(0)
        amount = say_amount(match.group("n"), match.group("frac"))
        return f"{_sign_word(match)}{amount} {_UNIT_WORDS[unit]}"

    return _UNIT.sub(_replace, text)


_ORDINAL = re.compile(
    r"(?<!\w)(?i:der|die|das|den|dem|des|am|im|zum|zur|vom|beim)\s+(?P<n>\d{1,4})\.(?=\s+[^\W\d])"
)


def _ordinals(text: str) -> str:
    """ "der 2. Weltkrieg" -> "der zweite Weltkrieg", "im 19. Jahrhundert" -> "im neunzehnten…".
    Only after an article/preposition: a bare "Kapitel 1. Nächstes" is a sentence end, not an ordinal."""

    def _replace(match: re.Match[str]) -> str:
        prefix = match.group(0)[: match.start("n") - match.start()]
        ending = "e" if prefix.split()[0].lower() in _E_ENDING_ARTICLES else "en"
        return prefix + ordinal(int(match.group("n")), ending)

    return _ORDINAL.sub(_replace, text)


_SUPERSCRIPT = re.compile(r"(?<=[\w)])([⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _powers(text: str) -> str:
    """ "3²" -> "3^2", which normalizer.py's symbol rule reads as "3 hoch 2"."""
    return _SUPERSCRIPT.sub(lambda m: "^" + m.group(1).translate(_SUPERSCRIPT_DIGITS), text)


_NUMBER = re.compile(_START + _SIGN + rf"(?P<n>{_INT})(?P<suffix>er)?(?![\w]|[.,]\d)")
_YEAR_CONTEXT = frozenset({"jahr", "jahre", "jahres", "jahrs", *(month.lower() for month in _MONTHS)})


def _numbers(text: str) -> str:
    """Every remaining integer: "84" -> "vierundachtzig", "1990" -> "neunzehnhundertneunzig" (as a
    year, unless a noun follows: "1500 Menschen"), "1 Apfel" -> "ein Apfel", "1990er" ->
    "neunzehnhundertneunziger". Decimals already reach here as "1 Komma 2 3" (normalizer.py)."""

    def _replace(match: re.Match[str]) -> str:
        digits = match.group("n")
        following = _CAPITALIZED_WORD.match(match.string, match.end())
        # "Komma" is capitalized but no noun: the decimal rule turned "1,5" into "1 Komma 5".
        if digits == "1" and following and following.group(1) != "Komma" and not match.group("suffix"):
            word = "eine" if following.group(1) in _FEMININE_NOUNS else "ein"
        elif "." not in digits and 1100 <= int(digits) <= 1999 and (
            not following or match.group("suffix") or _previous_word(match) in _YEAR_CONTEXT
        ):
            word = year(int(digits))
        else:
            word = say_integer(digits)
        return _sign_word(match) + word + (match.group("suffix") or "")

    return _NUMBER.sub(_replace, text)


@dataclass(frozen=True)
class Rule:
    name: str
    stage: Stage
    apply: Callable[[str], str]


# In order. Abbreviations first ("S. 47" must become "Seite 47" before 47 is read), ranges before
# currency/units ("10–20 €"), units before powers ("m²" is a unit, "3²" a power).
GERMAN_RULES: tuple[Rule, ...] = (
    Rule("abbreviations", "before_symbols", _abbreviations),
    Rule("dates", "before_symbols", _dates),
    Rule("times", "before_symbols", _times),
    Rule("ranges", "before_symbols", _ranges),
    Rule("currency", "before_symbols", _currency),
    Rule("units", "before_symbols", _units),
    Rule("ordinals", "before_symbols", _ordinals),
    Rule("powers", "before_symbols", _powers),
    Rule("numbers", "after_symbols", _numbers),
)

_RULES_BY_LANGUAGE: dict[str, tuple[Rule, ...]] = {"de": GERMAN_RULES}

RULE_NAMES: frozenset[str] = frozenset(rule.name for rules in _RULES_BY_LANGUAGE.values() for rule in rules)


def verbalize(text: str, language: str, stage: Stage, enabled: Collection[str]) -> str:
    """Apply the `enabled` rules of `stage` for `language`; other languages pass through unchanged."""
    for rule in _RULES_BY_LANGUAGE.get(language, ()):
        if rule.stage == stage and rule.name in enabled:
            text = rule.apply(text)
    return text
