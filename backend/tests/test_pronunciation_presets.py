"""Tests for the pronunciation preset packs (features/pronunciation/presets.py): every shipped pack
is valid, applying/removing one respects the teacher's own entries, and the physics pack reads
typical sentences correctly together with the built-in rules."""

import pytest

from app.features.ai.tts.normalizer import normalize_for_speech
from app.features.ai.tts.pronunciation import compile_rules
from app.features.pronunciation import presets, service
from conftest import login_as, make_user, new_client


def test_every_pack_loads_and_has_unique_terms():
    packs = presets.all_packs()
    assert {(p.id, p.language) for p in packs} >= {("physics", "de"), ("physics", "en")}
    for pack in packs:
        terms = [rule.term for rule in pack.entries]
        assert len(terms) == len(set(terms)), pack
        assert len(terms) <= service.MAX_ENTRIES_PER_LANGUAGE


@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [
        (
            "de",
            "Ein Auto fährt mit 50 km/h, also etwa 13,9 m/s.",
            "Ein Auto fährt mit fünfzig Kilometer pro Stunde, also etwa dreizehn Komma neun Meter pro Sekunde.",
        ),
        ("de", "g ≈ 9,81 m/s²", "g ungefähr gleich neun Komma acht eins Meter pro Quadratsekunde"),
        ("de", "Bei 20°C und 1013 hPa", "Bei zwanzig Grad Celsius und eintausenddreizehn Hektopascal"),
        # The pack's "1 s" -> "1 Sekunde" entries predate normt2s, which never rewrites a rule's
        # output again — so their "1" stays a digit, as the TTS got it before.
        ("de", "m = 5 kg, F = 1 N in 1 s", "m gleich fünf Kilogramm, F = eins Newton in 1 Sekunde"),
        ("de", "Für Δt = 2 s gilt v₀ = 3 m/s", "Für Delta t = zwei Sekunden gilt v null = drei Meter pro Sekunde"),
        ("de", "λ = 500 nm, 6 · 10^14 Hz", "Lambda = fünfhundert Nanometer, sechs mal zehn hoch vierzehn Hertz"),
        ("de", "Nach E = mc² und α-Strahlung", "Nach E gleich m c Quadrat und Alpha-Strahlung"),
        ("de", "220 Ω bei 5 V; die LED", "zweihundertzwanzig Ohm bei fünf Volt; die L E D"),
        ("de", "Newton war Physiker. Neu: 10kg", "Newton war Physiker. Neu: zehn Kilogramm"),
        ("en", "It weighs 1 kg and moves 1 m in 1 s.", "It weighs 1 kilogram and moves 1 meter in 1 second."),
        ("en", "Then 2 kg at 13.9 m/s.", "Then two kilograms at thirteen point nine meters per second."),
        ("en", "In the 1990s at 20 °C.", "In the nineteen nineties at twenty degrees Celsius."),
    ],
)
def test_physics_pack_reads_typical_sentences(language: str, text: str, expected: str):
    rules = compile_rules(presets.get_pack("physics", language).entries)
    assert normalize_for_speech(text, language, rules) == expected


def test_list_presets(client, teacher):
    packs = client.get("/pronunciation/presets", params={"language": "de"}).json()
    assert [(p["id"], p["language"], p["appliedCount"]) for p in packs] == [("physics", "de", 0)]
    assert any(e["term"] == "km/h" for e in packs[0]["entries"])


def test_apply_keeps_the_teachers_own_terms_and_is_idempotent(client, teacher):
    client.post("/pronunciation/entries", json={"language": "de", "term": "km/h", "spoken": "Stundenkilometer"})
    pack_size = len(presets.get_pack("physics", "de").entries)

    first = client.post("/pronunciation/presets/physics/apply", params={"language": "de"}).json()
    assert first == {"added": pack_size - 1, "skipped": 1}
    entries = {e["term"]: e for e in client.get("/pronunciation/entries", params={"language": "de"}).json()}
    assert entries["km/h"]["spoken"] == "Stundenkilometer"
    assert entries["km/h"]["sourcePack"] is None
    assert entries["m/s"]["sourcePack"] == "physics"

    assert client.post("/pronunciation/presets/physics/apply", params={"language": "de"}).json() == {
        "added": 0,
        "skipped": pack_size,
    }
    assert client.get("/pronunciation/entries", params={"language": "en"}).json() == []


def test_remove_keeps_entries_the_teacher_edited(client, teacher):
    client.post("/pronunciation/presets/physics/apply", params={"language": "de"})
    entries = {e["term"]: e for e in client.get("/pronunciation/entries").json()}
    client.put(f"/pronunciation/entries/{entries['m/s']['id']}", json={"term": "m/s", "spoken": "Meter je Sekunde"})

    removed = client.delete("/pronunciation/presets/physics", params={"language": "de"}).json()["removed"]

    assert removed == len(entries) - 1
    assert [e["term"] for e in client.get("/pronunciation/entries").json()] == ["m/s"]


def test_presets_only_touch_the_callers_list(client, teacher, engine):
    client.post("/pronunciation/presets/physics/apply", params={"language": "de"})
    other = login_as(new_client(), make_user(engine, email="other@example.com"))
    assert other.get("/pronunciation/presets", params={"language": "de"}).json()[0]["appliedCount"] == 0
    other.delete("/pronunciation/presets/physics", params={"language": "de"})
    assert len(client.get("/pronunciation/entries").json()) == len(presets.get_pack("physics", "de").entries)


def test_unknown_pack_is_404(client, teacher):
    response = client.post("/pronunciation/presets/chemistry/apply", params={"language": "de"})
    assert response.status_code == 404
    assert response.json()["detail"] == "PRONUNCIATION_PRESET_NOT_FOUND"


def test_apply_over_the_limit_adds_nothing(client, teacher, monkeypatch):
    monkeypatch.setattr(service, "MAX_ENTRIES_PER_LANGUAGE", 10)
    response = client.post("/pronunciation/presets/physics/apply", params={"language": "de"})
    assert response.status_code == 400
    assert client.get("/pronunciation/entries").json() == []
