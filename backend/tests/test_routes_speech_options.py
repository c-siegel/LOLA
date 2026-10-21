"""Route-level tests for the key-less speech options: the local-TTS sidecar fallback (when a
project has TTS enabled but no cloud TTS key) and browser-side (WebGPU) transcription."""

import base64

import httpx
import pytest

from app.core.config import settings
from app.features.ai.tts import local
from app.features.chat import public_router
from conftest import browser_url, create_key, create_project, parse_sse, publish

WAV = b"RIFF-fake-wav"


VOICES = {
    "languages": ["de", "en"],
    "cloning": False,
    "voices": [
        {"id": "alba", "label": "Alba", "kind": "preset", "licence": "CC BY 4.0"},
        {"id": "estelle", "label": "Estelle", "kind": "preset", "licence": "CC0"},
        {"id": "x", "label": "X", "kind": "something-newer", "licence": "?"},
    ],
}


@pytest.fixture(autouse=True)
def fresh_voices_cache():
    local.clear_voices_cache()
    yield
    local.clear_voices_cache()


@pytest.fixture
def sidecar(monkeypatch):
    """A fake local-TTS sidecar; returns the list of JSON bodies POST /synthesize received."""
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url) == "http://tts-local:8080/voices":
            return httpx.Response(200, json=VOICES)
        assert str(request.url) == "http://tts-local:8080/synthesize"
        import json

        received.append(json.loads(request.content))
        return httpx.Response(200, content=WAV)

    monkeypatch.setattr(local, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return received


@pytest.fixture
def keyless_tts_project(client, teacher, fake_ai):
    """A published project with an LLM key and TTS enabled, but no TTS key."""
    llm_key = create_key(client)
    project = create_project(client, llmApiKeyId=llm_key["id"], ttsEnabled=True, startPrompt="Hallo")
    project["shareSlug"] = publish(client, project["id"])
    return project


NOT_REACHABLE = {"reachable": False, "cloning": False, "languages": [], "voices": []}


def test_status_endpoints(client, teacher, monkeypatch, sidecar):
    monkeypatch.setattr(settings, "local_tts_enabled", False)
    monkeypatch.setattr(settings, "browser_stt_enabled", False)
    assert client.get("/providers/local-tts-status").json() == {"available": False, **NOT_REACHABLE}
    assert client.get("/providers/browser-stt-status").json() == {"available": False}
    monkeypatch.setattr(settings, "local_tts_enabled", True)
    monkeypatch.setattr(settings, "browser_stt_enabled", True)
    assert client.get("/providers/local-tts-status").json() == {
        "available": True,
        "reachable": True,
        "cloning": False,
        "languages": ["de", "en"],
        # A voice kind this backend doesn't know is left out.
        "voices": [{"id": "alba", "label": "Alba", "kind": "preset"}, {"id": "estelle", "label": "Estelle", "kind": "preset"}],
    }
    assert client.get("/providers/browser-stt-status").json() == {"available": True}


def test_status_when_the_sidecar_is_down(client, teacher, monkeypatch):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(local, "_client", httpx.Client(transport=httpx.MockTransport(down)))
    monkeypatch.setattr(settings, "local_tts_enabled", True)
    assert client.get("/providers/local-tts-status").json() == {"available": True, **NOT_REACHABLE}


def test_sidecar_voices_are_cached(client, teacher, monkeypatch, sidecar):
    calls = []
    handler = local._client._transport.handler
    monkeypatch.setattr(local, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or handler(r))))
    monkeypatch.setattr(settings, "local_tts_enabled", True)
    for _ in range(3):
        client.get("/providers/local-tts-status")
    assert len(calls) == 1


def test_project_voice_is_sent_to_the_sidecar(client, anon, keyless_tts_project, sidecar, monkeypatch):
    monkeypatch.setattr(settings, "local_tts_enabled", True)
    client.put(f"/projects/{keyless_tts_project['id']}", json={"ttsVoice": "estelle"})

    anon.post(f"/public/{keyless_tts_project['shareSlug']}/messages", json={"message": "Hi"})

    assert sidecar[-1]["voice"] == "estelle"


def test_server_stt_status(client, teacher, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "stt_engine", "whisper")
    monkeypatch.setattr(settings, "stt_parakeet_model_dir", str(tmp_path))
    assert client.get("/providers/server-stt-status").json() == {"defaultEngine": "whisper", "parakeetAvailable": False}
    monkeypatch.setattr(settings, "stt_engine", "parakeet")
    (tmp_path / "manifest.json").write_text("{}")
    assert client.get("/providers/server-stt-status").json() == {"defaultEngine": "parakeet", "parakeetAvailable": True}


def test_project_picks_its_own_server_stt_engine(client, anon, keyless_tts_project, monkeypatch):
    project_id = keyless_tts_project["id"]
    assert client.get(f"/projects/{project_id}").json()["sttServerEngine"] is None
    assert client.put(f"/projects/{project_id}", json={"sttServerEngine": "nonsense"}).status_code == 422
    client.put(f"/projects/{project_id}", json={"sttServerEngine": "parakeet"})
    assert client.get(f"/projects/{project_id}").json()["sttServerEngine"] == "parakeet"

    engines = []
    monkeypatch.setattr(
        public_router,
        "transcribe_audio",
        lambda content, language, prompt=None, api_key_record=None, engine=None: engines.append(engine) or "Hallo",
    )
    slug = keyless_tts_project["shareSlug"]
    response = anon.post(f"/public/{slug}/transcriptions", files={"audio": ("a.webm", b"audio", "audio/webm")})
    assert response.status_code == 200, response.text
    assert engines == ["parakeet"]

    # null resets it to the deployment default.
    client.put(f"/projects/{project_id}", json={"sttServerEngine": None})
    assert client.get(f"/projects/{project_id}").json()["sttServerEngine"] is None


def test_chat_layout_is_saved_validated_and_public(client, anon, keyless_tts_project):
    project_id = keyless_tts_project["id"]
    slug = keyless_tts_project["shareSlug"]
    assert client.get(f"/projects/{project_id}").json()["chatLayout"] == "avatar_chat"
    assert anon.get(f"/public/{slug}").json()["chatLayout"] == "avatar_chat"
    assert client.put(f"/projects/{project_id}", json={"chatLayout": "sideways"}).status_code == 422
    client.put(f"/projects/{project_id}", json={"chatLayout": "chat_only"})
    assert client.get(f"/projects/{project_id}").json()["chatLayout"] == "chat_only"
    assert anon.get(f"/public/{slug}").json()["chatLayout"] == "chat_only"


def test_without_sidecar_a_keyless_tts_project_replies_text_only(anon, keyless_tts_project):
    body = anon.post(f"/public/{keyless_tts_project['shareSlug']}/messages", json={"message": "Hi"}).json()
    assert body["audioBase64"] is None
    assert body["ttsMs"] is None


def test_sidecar_synthesizes_for_a_keyless_tts_project(anon, keyless_tts_project, sidecar, monkeypatch):
    monkeypatch.setattr(settings, "local_tts_enabled", True)
    slug = keyless_tts_project["shareSlug"]

    body = anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()
    assert base64.b64decode(body["audioBase64"]) == WAV
    assert body["contentType"] == "audio/wav"
    assert sidecar[-1]["language"] == "de"
    assert "voice" not in sidecar[-1]  # no voice picked: the sidecar's default for the language

    chunks = [data for name, data in parse_sse(anon.post(f"/public/{slug}/messages/stream", json={"message": "Hi"}).text) if name == "chunk"]
    assert chunks and all(c["contentType"] == "audio/wav" for c in chunks)


def test_start_audio_via_sidecar_is_stored_and_served_as_wav(client, keyless_tts_project, sidecar, monkeypatch):
    pid = keyless_tts_project["id"]
    not_configured = client.post(f"/projects/{pid}/start-audio")
    assert not_configured.status_code == 400
    assert not_configured.json() == {"detail": "TTS_NOT_CONFIGURED"}

    monkeypatch.setattr(settings, "local_tts_enabled", True)
    generated = client.post(f"/projects/{pid}/start-audio").json()
    served = client.get(browser_url(generated["startAudioUrl"]))
    assert served.content == WAV
    assert served.headers["content-type"] == "audio/wav"


def test_browser_stt_is_on_by_default_and_each_side_can_opt_out(client, anon, keyless_tts_project, monkeypatch):
    slug = keyless_tts_project["shareSlug"]
    monkeypatch.setattr(settings, "browser_stt_enabled", True)
    assert client.get(f"/projects/{keyless_tts_project['id']}").json()["sttBrowserEnabled"] is True
    assert anon.get(f"/public/{slug}").json()["browserSttModelUrl"] == settings.browser_stt_model_url

    client.put(f"/projects/{keyless_tts_project['id']}", json={"sttBrowserEnabled": False})
    assert anon.get(f"/public/{slug}").json()["browserSttModelUrl"] is None

    client.put(f"/projects/{keyless_tts_project['id']}", json={"sttBrowserEnabled": True})
    monkeypatch.setattr(settings, "browser_stt_enabled", False)
    assert anon.get(f"/public/{slug}").json()["browserSttModelUrl"] is None
