"""Bridge phrases: a short neutral line the avatar says while a reasoning turn is still thinking."""

import time

import pytest
from sqlmodel import Session

from app.features.chat import bridge
from app.features.chat.pipeline import ChatTurn, _bridge_chunk, prepare_chat, stream_turn
from app.features.projects.models import Project
from tests.conftest import create_key, create_project, parse_sse, publish

ARGUMENT = "Ich behaupte, dass das falsch ist, weil die Zahl negativ wird"
SMALL_TALK = "Wie geht es dir heute so"
VLLM = {"provider": "openai_compatible", "apiBase": "http://vllm.local/v1"}


@pytest.fixture
def project(client, teacher, fake_ai):
    llm = create_key(client, modelId="qwen3", **VLLM)
    tts = create_key(client, key_type="tts")
    created = create_project(
        client,
        llmApiKeyId=llm["id"],
        ttsApiKeyId=tts["id"],
        ttsEnabled=True,
        reasoningMode="adaptive",
        saveConversations=True,
    )
    return created, publish(client, created["id"])


def _stream(client, slug, message):
    return parse_sse(client.post(f"/public/{slug}/messages/stream", json={"message": message}).text)


def _wait_until_rendered(language: str = "de", expected: int | None = None) -> None:
    expected = expected or len(bridge.phrases_for(language))
    for _ in range(100):
        if len(bridge.audio_cache._items) >= expected:
            return
        time.sleep(0.02)
    raise AssertionError("the bridge phrases were not rendered")


def test_an_escalated_turn_starts_with_a_spoken_bridge(client, project, fake_ai) -> None:
    _, slug = project
    _stream(client, slug, SMALL_TALK)  # the first turn renders the phrases in the background
    _wait_until_rendered()

    events = _stream(client, slug, ARGUMENT)

    chunks = [data for name, data in events if name == "chunk"]
    first, rest = chunks[0], chunks[1:]
    assert first["bridge"] is True and first["index"] == -1
    assert first["text"] in bridge.PHRASES["de"] and first["audioBase64"]
    assert [c["index"] for c in rest] == list(range(len(rest)))
    assert all("bridge" not in c for c in rest)
    assert first["text"] not in events[-1][1]["reply"]


def test_the_bridge_is_not_part_of_the_saved_conversation(client, project, fake_ai, anon) -> None:
    _, slug = project
    anon.get(f"/public/{slug}")
    anon.post(f"/public/{slug}/messages/stream", json={"message": SMALL_TALK})
    _wait_until_rendered()
    anon.post(f"/public/{slug}/messages/stream", json={"message": ARGUMENT})

    session_id = client.get("/conversations").json()["items"][0]["id"]
    texts = " ".join(m["content"] for m in client.get(f"/conversations/{session_id}").json()["messages"])
    assert not any(phrase in texts for phrase in bridge.PHRASES["de"])


def test_turns_that_do_not_reason_get_no_bridge(client, project, fake_ai) -> None:
    _, slug = project
    _stream(client, slug, SMALL_TALK)
    _wait_until_rendered()
    assert not any(data.get("bridge") for name, data in _stream(client, slug, SMALL_TALK) if name == "chunk")


def test_the_first_escalation_goes_without_a_bridge_when_nothing_is_rendered_yet(client, project, fake_ai) -> None:
    _, slug = project
    events = _stream(client, slug, ARGUMENT)
    # Not waiting for synthesis is the point: the turn must not be held up by it.
    assert events[-1][0] == "done"
    assert [data["index"] for name, data in events if name == "chunk"][0] in (-1, 0)


def test_it_can_be_switched_off(client, project, fake_ai) -> None:
    created, slug = project
    client.put(f"/projects/{created['id']}", json={"bridgeEnabled": False})
    _stream(client, slug, SMALL_TALK)
    time.sleep(0.2)
    assert len(bridge.audio_cache._items) == 0  # nothing is even rendered
    assert not any(data.get("bridge") for name, data in _stream(client, slug, ARGUMENT) if name == "chunk")


def test_a_language_without_phrases_gets_none(client, teacher, fake_ai) -> None:
    llm = create_key(client, modelId="qwen3", **VLLM)
    tts = create_key(client, key_type="tts")
    created = create_project(
        client, llmApiKeyId=llm["id"], ttsApiKeyId=tts["id"], ttsEnabled=True, reasoningMode="always", spokenLanguage="fr"
    )
    slug = publish(client, created["id"])
    _stream(client, slug, ARGUMENT)
    events = _stream(client, slug, ARGUMENT)
    assert not any(data.get("bridge") for name, data in events if name == "chunk")


def test_an_always_project_gets_the_bridge_too(client, teacher, fake_ai) -> None:
    llm = create_key(client, modelId="qwen3", **VLLM)
    tts = create_key(client, key_type="tts")
    created = create_project(client, llmApiKeyId=llm["id"], ttsApiKeyId=tts["id"], ttsEnabled=True, reasoningMode="always")
    slug = publish(client, created["id"])
    _stream(client, slug, SMALL_TALK)
    _wait_until_rendered()
    assert _stream(client, slug, SMALL_TALK)[0][1]["bridge"] is True


def test_the_device_speaks_the_bridge_when_it_does_the_speech(client, engine, project, fake_ai) -> None:
    import dataclasses

    created, _ = project
    with Session(engine) as session:
        context = prepare_chat(session, session.get(Project, created["id"]))
    context = dataclasses.replace(context, speak_on_device=True)

    event = _bridge_chunk(context, time.perf_counter())

    assert event["bridge"] is True and event["audioBase64"] is None
    assert event["spoken"] and event["text"] in bridge.PHRASES["de"]


def test_rendered_audio_belongs_to_the_voice_it_was_made_with(client, engine, project, fake_ai) -> None:
    import dataclasses

    from app.features.chat.pipeline import _bridge_audio_key

    created, _ = project
    with Session(engine) as session:
        context = prepare_chat(session, session.get(Project, created["id"]))
    other_voice = dataclasses.replace(context, tts_voice="another")

    assert _bridge_audio_key(context, "Hallo") != _bridge_audio_key(other_voice, "Hallo")
    assert _bridge_audio_key(context, "Hallo") != _bridge_audio_key(dataclasses.replace(context, project_id="x"), "Hallo")


def test_rendered_audio_is_dropped_with_its_project_voice_clip_and_account() -> None:
    def key(project: str, clip: str | None) -> tuple:
        return (project, "Hallo", "de", "voice", clip, "local")

    cache = bridge.AudioCache()
    for project, clip in (("p1", "sha-a"), ("p2", "sha-a"), ("p3", "sha-b"), ("p4", None)):
        cache.put(key(project, clip), ("audio", "audio/mpeg"))

    cache.forget_project("p1")
    assert cache.get(key("p1", "sha-a")) is None and cache.get(key("p2", "sha-a")) is not None

    cache.forget_voice_clip("sha-a")
    assert cache.get(key("p2", "sha-a")) is None
    assert cache.get(key("p3", "sha-b")) is not None and cache.get(key("p4", None)) is not None


def test_deleting_a_project_voice_clip_or_account_forgets_its_bridge_audio(client, engine, project, fake_ai) -> None:
    created, slug = project
    _stream(client, slug, SMALL_TALK)
    _wait_until_rendered()
    assert bridge.audio_cache._items

    assert client.delete(f"/projects/{created['id']}").status_code in (200, 204)

    assert not bridge.audio_cache._items


def test_the_least_recently_used_rendering_is_evicted_first(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_MAX_CACHED", 2)
    cache = bridge.AudioCache()
    cache.put(("a",), ("1", "x"))
    cache.put(("b",), ("2", "x"))
    cache.get(("a",))  # a is in use, b is not
    cache.put(("c",), ("3", "x"))

    assert cache.get(("a",)) is not None and cache.get(("b",)) is None


def test_a_failed_rendering_is_not_retried_every_turn(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(bridge.time, "monotonic", lambda: clock[0])
    cache = bridge.AudioCache()
    key = ("p", "Hallo")

    assert cache.claim(key) is True
    cache.release(key, failed=True)
    assert cache.claim(key) is False  # backing off

    clock[0] += bridge._RETRY_AFTER_FAILURE_SECONDS + 1
    assert cache.claim(key) is True
