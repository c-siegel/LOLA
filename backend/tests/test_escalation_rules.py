"""The adaptive mode's rules (chat/escalation_rules.py): which messages look like an argument."""

import pytest

from app.features.chat.escalation_rules import check


@pytest.mark.parametrize(
    ("message", "language", "deep", "reason"),
    [
        ("Ich behaupte, dass Wasser bei 100 Grad kocht", "de", True, "argument_cue"),
        ("Das stimmt nicht, weil die Sonne früher untergeht", "de", True, "argument_cue"),
        ("I disagree with that point entirely", "en", True, "argument_cue"),
        ("Because the sun is far away it looks small", "en", True, "argument_cue"),
        ("Hallo", "de", False, "short_message"),
        ("Ja genau", "de", False, "short_message"),
        ("Kannst du mir bitte das Wetter erklären", "de", False, "no_cue"),
        ("what is the capital of France please", "en", False, "no_cue"),
        ("Bonjour, je voudrais parler de la Révolution parce que", "fr", False, "language_unsupported"),
        ("Das Wort derweil kommt vor, aber kein Signal", "de", False, "no_cue"),  # "weil" inside "derweil"
        (" ".join(["wort"] * 40), "de", True, "long_message"),
        ("DESHALB ist das so", "de", True, "argument_cue"),
        ("Ich meine das Ergebnis ist zwölf Komma fünf", "de-AT", False, "no_cue"),
    ],
)
def test_message_rules(message, language, deep, reason) -> None:
    result = check(message, [], language)
    assert (result.deep, result.reason) == (deep, reason)


def test_a_longer_answer_to_a_question_escalates() -> None:
    history = [{"role": "assistant", "content": "Was passiert mit dem Eis, wenn es warm wird?"}]
    assert check("Es schmilzt und wird dann langsam zu Wasser um", history, "de").reason == "answer_to_question"
    assert check("Es schmilzt", history, "de").reason == "short_message"
    statement = [{"role": "assistant", "content": "Eis schmilzt bei null Grad."}]
    assert check("Es schmilzt und wird dann langsam zu Wasser um", statement, "de").deep is False


def test_a_question_inside_quotes_still_counts() -> None:
    history = [{"role": "assistant", "content": 'Sag mir: "Warum ist der Himmel blau?"'}]
    assert check("Wegen der Streuung des Lichts in der Luft", history, "de").deep is True
