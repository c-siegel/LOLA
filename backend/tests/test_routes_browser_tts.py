"""On-device speech output: a project can let the visitor's device speak its replies (Pocket TTS
in the browser, Project.tts_browser_enabled). The server then skips its own TTS and sends the
reply's spoken form instead — but only where the deployment and the project offer it."""

import pytest

from app.core.config import settings
from app.features.api_keys.resolve import browser_tts_model_url_for
from app.features.projects.models import Project
from conftest import parse_sse

MODEL_URL = "/models/pocket-tts-de/v1/"


@pytest.fixture
def offered(monkeypatch):
    monkeypatch.setattr(settings, "browser_tts_enabled", True)
    monkeypatch.setattr(settings, "browser_tts_model_url", MODEL_URL)
    monkeypatch.setattr(settings, "browser_tts_language", "de")


@pytest.fixture
def device_project(client, chat_project, offered):
    client.put(f"/projects/{chat_project['id']}", json={"ttsBrowserEnabled": True})
    return chat_project


def _project(**overrides) -> Project:
    defaults = dict(user_id="user-1", title="Test", tts_enabled=True, tts_browser_enabled=True, spoken_language="de")
    return Project(**{**defaults, **overrides})


def test_model_url_only_where_everything_allows_it(offered, monkeypatch):
    assert browser_tts_model_url_for(_project()) == MODEL_URL
    assert browser_tts_model_url_for(_project(tts_browser_enabled=False)) is None
    assert browser_tts_model_url_for(_project(tts_enabled=False)) is None
    # The model speaks German only; an English project keeps the server's TTS.
    assert browser_tts_model_url_for(_project(spoken_language="en")) is None
    monkeypatch.setattr(settings, "browser_tts_enabled", False)
    assert browser_tts_model_url_for(_project()) is None


def test_off_by_default_and_saved_per_project(client, chat_project):
    url = f"/projects/{chat_project['id']}"
    assert client.get(url).json()["ttsBrowserEnabled"] is False
    client.put(url, json={"ttsBrowserEnabled": True})
    assert client.get(url).json()["ttsBrowserEnabled"] is True


def test_status_endpoint(client, teacher, monkeypatch):
    monkeypatch.setattr(settings, "browser_tts_enabled", False)
    assert client.get("/providers/browser-tts-status").json() == {"available": False, "language": "de"}
    monkeypatch.setattr(settings, "browser_tts_enabled", True)
    assert client.get("/providers/browser-tts-status").json() == {"available": True, "language": "de"}


def test_public_info_carries_the_model_url(anon, device_project):
    assert anon.get(f"/public/{device_project['shareSlug']}").json()["browserTtsModelUrl"] == MODEL_URL


def test_device_speaks_the_plain_reply(anon, device_project, fake_ai):
    fake_ai.llm_reply = "Das kostet **3 €**."

    body = anon.post(
        f"/public/{device_project['shareSlug']}/messages", json={"message": "Hi", "speakOnDevice": True}
    ).json()

    assert fake_ai.speech_calls == []
    assert body["audioBase64"] is None
    assert body["ttsMs"] is None
    assert body["reply"] == "Das kostet **3 €**."
    # The spoken form, as the server's TTS would have read it: no Markdown, numbers as words.
    assert "**" not in body["spoken"] and "3" not in body["spoken"]
    assert "drei" in body["spoken"]


def test_device_speaks_the_streamed_reply(anon, device_project, fake_ai):
    fake_ai.llm_reply = "Hallo! Das sind 2 Sätze."

    response = anon.post(
        f"/public/{device_project['shareSlug']}/messages/stream", json={"message": "Hi", "speakOnDevice": True}
    )
    chunks = [data for name, data in parse_sse(response.text) if name == "chunk"]

    assert fake_ai.speech_calls == []
    assert chunks and all(chunk["audioBase64"] is None for chunk in chunks)
    spoken = " ".join(chunk["spoken"] for chunk in chunks)
    assert "zwei" in spoken and "2" not in spoken


def test_without_the_flag_the_server_still_speaks(anon, device_project, fake_ai):
    body = anon.post(f"/public/{device_project['shareSlug']}/messages", json={"message": "Hi"}).json()

    assert fake_ai.speech_calls
    assert body["audioBase64"] is not None
    assert body["spoken"] is None


def test_flag_is_ignored_where_the_project_does_not_offer_it(anon, chat_project, fake_ai, offered):
    # A request alone can't switch off the server's TTS: the project hasn't opted in.
    body = anon.post(
        f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi", "speakOnDevice": True}
    ).json()

    assert fake_ai.speech_calls
    assert body["audioBase64"] is not None
    assert body["spoken"] is None
