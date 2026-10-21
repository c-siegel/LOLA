"""Tests for the avatar's gesture markers (app/features/chat/motion.py): parsing "::name::" out of
replies, keeping markers whole across streamed chunks, and that TTS, the visitor and the saved
transcript each get what they should — clean text, the gesture names, and the names with their
positions."""

import json
from pathlib import Path

import pytest
from conftest import create_project, parse_sse

from app.features.chat.motion import MOTION_PROMPT, MOTIONS, MotionMark, extract_markers
from app.features.ai.tts.normalizer import speech_stream
from app.features.projects.export import parse_project_yaml

FRONTEND_MOTIONS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "vendor" / "motion-engine" / "motions.json"

STREAMED_REPLY = (
    "::wave_right:: Hallo, schön dass du da bist! Das ist eine richtig gute Frage zu Brüchen, "
    "::thinking_face:: lass mich kurz überlegen. Genau so geht es. ::thumbup_right::"
)
CLEAN_REPLY = (
    "Hallo, schön dass du da bist! Das ist eine richtig gute Frage zu Brüchen, "
    "lass mich kurz überlegen. Genau so geht es."
)


# --- Parsing ---


def extract_motions(text: str) -> tuple[str, list[MotionMark]]:
    """extract_markers without the conversation-plan ticks, which test_checklist.py covers."""
    clean, marks, _ = extract_markers(text)
    return clean, marks


def test_extract_motions_removes_markers_and_records_positions() -> None:
    text, marks = extract_motions("::wave_right:: Hallo! Ja ::nod_yes:: genau so. ::applause::")
    assert text == "Hallo! Ja genau so."
    assert marks == [MotionMark("wave_right", 0), MotionMark("nod_yes", 10), MotionMark("applause", len(text))]
    assert text[10:].startswith("genau")


def test_extract_motions_tolerates_spaces_and_case() -> None:
    assert extract_motions("Gut :: Nod_Yes :: gemacht") == ("Gut gemacht", [MotionMark("nod_yes", 4)])


def test_unknown_markers_are_removed_without_a_gesture() -> None:
    # Students never see or hear a marker, also one the model invented or a teacher's own
    # instructions named — only known names play a gesture.
    assert extract_motions("Lass uns ::dance:: tanzen") == ("Lass uns tanzen", [])
    assert extract_motions("::big smile:: Hallo! ::Look-Around::") == ("Hallo!", [])
    assert extract_motions("Ja ::dance:: ::nod_yes:: genau") == ("Ja genau", [MotionMark("nod_yes", 3)])


def test_code_and_known_markers_glued_to_words() -> None:
    # C++ scope operators are no markers; a known gesture is recognised even without a space.
    assert extract_motions("Nutze std::vector::size() dafür.") == ("Nutze std::vector::size() dafür.", [])
    assert extract_motions("Richtig::nod_yes::") == ("Richtig", [MotionMark("nod_yes", 7)])


def test_text_without_markers_is_unchanged() -> None:
    assert extract_motions("Einfach nur Text.") == ("Einfach nur Text.", [])


# --- Chunking ---


def raw_chunks(text: str, step: int) -> list[str]:
    """The raw text of each piece speech_stream releases, fed `step` characters at a time."""
    stream = speech_stream("de")
    pieces = [p for i in range(0, len(text), step) for p in stream.feed_pieces(text[i : i + step])]
    tail = stream.flush_piece()
    return [p.raw for p in pieces + ([tail] if tail else [])]


def test_stream_keeps_markers_whole_and_does_not_split_at_them() -> None:
    # Character by character: the worst case for a marker arriving in pieces.
    chunks = raw_chunks(STREAMED_REPLY, 1)
    assert "".join(chunks) == STREAMED_REPLY
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        # Cut at a sentence end, never inside a marker.
        assert chunk.rstrip().endswith((".", "!", "?")), chunk
    for chunk in chunks:
        assert chunk.count("::") % 2 == 0, chunk
    # The closing "::" followed by a space is no sentence end.
    sentence = "Schau dir diesen Abschnitt genau an ::point:: das ist der wichtigste Teil."
    assert raw_chunks(sentence, 1) == [sentence]


def test_backend_and_frontend_offer_the_same_gestures() -> None:
    if not FRONTEND_MOTIONS.exists():
        pytest.skip("frontend not checked out next to the backend")
    assert set(json.loads(FRONTEND_MOTIONS.read_text(encoding="utf-8"))) == set(MOTIONS)


# --- Chat routes ---


def test_stream_strips_markers_sends_motions_and_saves_their_positions(client, anon, chat_project, fake_ai):
    fake_ai.llm_reply = STREAMED_REPLY
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    events = parse_sse(anon.post(f"/public/{slug}/messages/stream", json={"message": "Wie geht das?"}).text)

    assert fake_ai.completion_calls[-1]["messages"][0]["content"].endswith(MOTION_PROMPT)
    chunks = [data for name, data in events if name == "chunk"]
    assert [c["motions"] for c in chunks] == [["wave_right"], ["thinking_face"], ["thumbup_right"]]
    assert all("::" not in c["text"] for c in chunks)
    assert all("::" not in call["input"] for call in fake_ai.speech_calls)
    assert events[-1][1]["reply"] == CLEAN_REPLY

    conversation_id = client.get("/conversations/ids").json()[0]
    reply = client.get(f"/conversations/{conversation_id}").json()["messages"][1]
    assert reply["content"] == CLEAN_REPLY
    assert reply["motions"] == [
        {"name": "wave_right", "offset": 0},
        {"name": "thinking_face", "offset": CLEAN_REPLY.index("lass")},
        {"name": "thumbup_right", "offset": len(CLEAN_REPLY)},
    ]
    csv = client.post("/conversations/export", json={"conversationIds": [conversation_id]}).text
    assert "Zeitpunkt,Avatar,Schüler:in,Gesten" in csv
    assert f"wave_right@0; thinking_face@{CLEAN_REPLY.index('lass')}; thumbup_right@{len(CLEAN_REPLY)}" in csv


def test_stream_speaks_normalized_text_without_glued_markers(anon, chat_project, fake_ai):
    # The normalizer only silences a marker that stands apart; one glued to a word must still be
    # cut out of the spoken chunk by motion.py, and the chunk is spoken in its normalized form.
    fake_ai.llm_reply = "Richtig::nod_yes:: das sind 3,5 kg. Gut::done_1:: gemacht, weiter so mit der Übung!"
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    events = parse_sse(anon.post(f"/public/{slug}/messages/stream", json={"message": "Wie viel?"}).text)

    spoken = " ".join(call["input"] for call in fake_ai.speech_calls)
    assert "::" not in spoken and "nod_yes" not in spoken
    assert "drei Komma fünf Kilogramm" in spoken
    chunks = [data for name, data in events if name == "chunk"]
    assert "3,5 kg" in " ".join(c["text"] for c in chunks)


def test_plain_reply_returns_motions_and_speaks_clean_text(anon, chat_project, fake_ai):
    fake_ai.llm_reply = "::nod_yes:: Richtig, das Ergebnis ist vier."
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    body = anon.post(f"/public/{slug}/messages", json={"message": "2+2?"}).json()

    assert body["reply"] == "Richtig, das Ergebnis ist vier."
    assert body["motions"] == ["nod_yes"]
    assert fake_ai.speech_calls[-1]["input"] == "Richtig, das Ergebnis ist vier."


def test_motion_off_or_chat_only_sends_no_instructions_but_still_hides_markers(client, anon, chat_project, fake_ai):
    fake_ai.llm_reply = "::nod_yes:: Richtig."
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    for change in ({"motionEnabled": False}, {"motionEnabled": True, "chatLayout": "chat_only"}):
        client.put(f"/projects/{chat_project['id']}", json=change)

        body = anon.post(f"/public/{slug}/messages", json={"message": "2+2?"}).json()

        assert MOTION_PROMPT not in fake_ai.completion_calls[-1]["messages"][0]["content"]
        assert body["motions"] == []
        # The model may still write one (e.g. prompted by the teacher's own instructions).
        assert body["reply"] == "Richtig."


def test_motion_setting_defaults_on_and_reaches_the_public_page(client, anon, teacher):
    project = create_project(client)
    assert project["motionEnabled"] is True
    updated = client.put(f"/projects/{project['id']}", json={"motionEnabled": False}).json()
    assert updated["motionEnabled"] is False

    client.put(f"/projects/{project['id']}", json={"motionEnabled": True})
    slug = client.put(f"/projects/{project['id']}/publication").json()["shareSlug"]
    assert anon.get(f"/public/{slug}").json()["motionEnabled"] is True


def test_thinking_motions_default_off_and_only_reach_the_public_page_together_with_motion(client, anon, teacher):
    project = create_project(client)
    pid = project["id"]
    assert project["thinkingMotionsEnabled"] is False
    slug = client.put(f"/projects/{pid}/publication").json()["shareSlug"]
    assert anon.get(f"/public/{slug}").json()["thinkingMotionsEnabled"] is False

    assert client.put(f"/projects/{pid}", json={"thinkingMotionsEnabled": True}).json()["thinkingMotionsEnabled"] is True
    assert anon.get(f"/public/{slug}").json()["thinkingMotionsEnabled"] is True

    # Without gestures there is nothing to play: the page must not be told to.
    client.put(f"/projects/{pid}", json={"motionEnabled": False})
    assert anon.get(f"/public/{slug}").json()["thinkingMotionsEnabled"] is False


def test_exports_without_the_thinking_setting_import_with_it_off() -> None:
    assert parse_project_yaml("lola_export: 2\nproject:\n  title: x\n").thinking_motions_enabled is False


def test_exports_without_the_setting_import_with_motion_on() -> None:
    assert parse_project_yaml("lola_export: 2\nproject:\n  title: x\n").motion_enabled is True
    assert parse_project_yaml("lola_export: 2\nproject:\n  title: x\n  motion_enabled: false\n").motion_enabled is False


# --- Editable motion prompt ---


def test_system_prompt_route_shows_the_assembled_prompt(client, chat_project) -> None:
    body = client.get(f"/projects/{chat_project['id']}/system-prompt").json()

    assert body["motionActive"] is True
    assert body["motionCustomized"] is False
    assert body["motionPrompt"] == body["defaultMotionPrompt"] == MOTION_PROMPT
    assert body["full"] == f"{body['preprompt']}\n\n{MOTION_PROMPT}"


def test_custom_motion_prompt_is_sent_and_blank_restores_the_default(client, anon, chat_project, fake_ai) -> None:
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    updated = client.put(f"/projects/{chat_project['id']}", json={"motionPrompt": "Only wave at the start."}).json()
    assert updated["motionPrompt"] == "Only wave at the start."
    anon.post(f"/public/{slug}/messages", json={"message": "Hallo"})
    system = fake_ai.completion_calls[-1]["messages"][0]["content"]
    assert system.endswith("Only wave at the start.") and MOTION_PROMPT not in system
    assert client.get(f"/projects/{chat_project['id']}/system-prompt").json()["full"] == system

    assert client.put(f"/projects/{chat_project['id']}", json={"motionPrompt": "  "}).json()["motionPrompt"] is None
    anon.post(f"/public/{slug}/messages", json={"message": "Hallo"})
    assert fake_ai.completion_calls[-1]["messages"][0]["content"].endswith(MOTION_PROMPT)


def test_system_prompt_route_needs_an_owned_project(client, chat_project) -> None:
    assert client.get("/projects/does-not-exist/system-prompt").status_code == 404
