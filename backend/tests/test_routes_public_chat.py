"""Route-level tests for the public chat (/public/{slug}...): loading, unlocking, sending
messages (plain and streamed), and voice transcription — the anonymous visitor's whole flow."""

import base64

from conftest import LLM_REPLY, TTS_BYTES, create_project, parse_sse, publish

from app.core.config import settings
from app.features.ai.stt.parakeet_local import LocalParakeetClient
from app.features.chat import pipeline
from app.features.chat.motion import MOTION_PROMPT


def _audio(content_type: str = "audio/webm"):
    return {"audio": ("rec.webm", b"fake-audio", content_type)}


def test_load_published_project_sets_visitor_cookie_and_logs_access(client, anon, chat_project):
    response = anon.get(f"/public/{chat_project['shareSlug']}")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Mathe-Tutor"
    assert body["teacherName"] == "Teacher"
    assert body["startPrompt"] == "Willkommen!"
    assert body["ttsEnabled"] is True
    assert body["unlocked"] is True
    assert body["llmModel"] == "openai/gpt-4o-mini"
    assert "ah_visitor_id" in response.headers["set-cookie"]
    assert client.get("/analytics/stats").json()["sessions"] == 1


def test_unknown_or_unpublished_slug_is_404(client, anon, teacher):
    project = create_project(client)
    slug = publish(client, project["id"])
    client.delete(f"/projects/{project['id']}/publication")

    for path in ("/public/NOPE1", f"/public/{slug}"):
        response = anon.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "PROJECT_NOT_FOUND_OR_UNPUBLISHED"}


def test_password_protected_chat_requires_unlock(client, anon, chat_project):
    client.put(f"/projects/{chat_project['id']}", json={"chatPassword": "geheim"})
    slug = chat_project["shareSlug"]

    locked = anon.get(f"/public/{slug}").json()
    assert locked["passwordProtected"] is True
    assert locked["unlocked"] is False
    assert locked["startPrompt"] is None

    response = anon.post(f"/public/{slug}/messages", json={"message": "Hi"})
    assert response.status_code == 401
    assert response.json() == {"detail": "CHAT_UNLOCK_REQUIRED"}

    wrong = anon.post(f"/public/{slug}/unlock", json={"password": "falsch"})
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "CHAT_PASSWORD_INCORRECT"}

    token = anon.post(f"/public/{slug}/unlock", json={"password": "geheim"}).json()["unlockToken"]
    headers = {"X-Chat-Unlock-Token": token}
    assert anon.get(f"/public/{slug}", headers=headers).json()["unlocked"] is True
    assert anon.post(f"/public/{slug}/messages", json={"message": "Hi"}, headers=headers).status_code == 200


def test_unlock_on_unprotected_project_is_400(anon, chat_project):
    response = anon.post(f"/public/{chat_project['shareSlug']}/unlock", json={"password": "x"})
    assert response.status_code == 400
    assert response.json() == {"detail": "PROJECT_NOT_PASSWORD_PROTECTED"}


def test_send_message_returns_reply_audio_and_saves_conversation(client, anon, chat_project, fake_ai):
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    history = [{"role": "user", "content": "Vorher"}, {"role": "assistant", "content": "Antwort"}]

    response = anon.post(
        f"/public/{slug}/messages",
        json={"message": "Was ist 2+2?", "history": history},
        headers={"X-Visitor-Name": "J%C3%BCrgen"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == LLM_REPLY
    assert base64.b64decode(body["audioBase64"]) == TTS_BYTES
    assert body["contentType"] == "audio/mpeg"
    assert isinstance(body["llmMs"], float)
    assert isinstance(body["ttsMs"], float)

    sent = fake_ai.completion_calls[-1]
    assert sent["model"] == "openai/gpt-4o-mini"
    assert sent["messages"] == [
        # Motion is on by default, so the gesture instructions follow the teacher's prompt.
        {"role": "system", "content": f"Du bist ein Tutor.\n\n{MOTION_PROMPT}"},
        {"role": "assistant", "content": "Willkommen!"},
        *history,
        {"role": "user", "content": "Was ist 2+2?"},
    ]
    assert sent["temperature"] == 0.5 and sent["top_p"] == 1.0

    sessions = client.get("/conversations").json()
    assert sessions["total"] == 1
    row = sessions["items"][0]
    assert row["messageCount"] == 2
    assert row["visitorName"] == "Jürgen"
    assert row["lastQuestion"] == "Was ist 2+2?"

    # A second message from the same visitor appends to the same conversation.
    anon.post(f"/public/{slug}/messages", json={"message": "Und 3+3?"})
    sessions = client.get("/conversations").json()
    assert sessions["total"] == 1
    assert sessions["items"][0]["messageCount"] == 4
    assert sessions["items"][0]["visitorName"] == "Jürgen"


def test_send_message_without_tts_has_no_audio(client, anon, chat_project):
    client.put(f"/projects/{chat_project['id']}", json={"ttsEnabled": False})
    body = anon.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi"}).json()
    assert body["audioBase64"] is None
    assert body["contentType"] is None
    assert body["ttsMs"] is None


def test_tts_failure_still_returns_text(anon, chat_project, fake_ai):
    fake_ai.tts_error = RuntimeError("tts down")
    response = anon.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi"})
    assert response.status_code == 200
    assert response.json()["reply"] == LLM_REPLY
    assert response.json()["audioBase64"] is None


def test_save_failure_still_returns_the_reply(anon, chat_project, fake_ai, monkeypatch):
    # E.g. "database is locked" when a whole class writes at once: the reply already exists and
    # was paid for, so the visitor still gets it — like the streamed path (see pipeline.py).
    def locked(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pipeline, "save_turn", locked)
    response = anon.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi"})
    assert response.status_code == 200
    assert response.json()["reply"] == LLM_REPLY
    assert response.json()["audioBase64"] is not None


def test_send_message_without_llm_key_is_503(client, anon, teacher):
    project = create_project(client)
    slug = publish(client, project["id"])
    response = anon.post(f"/public/{slug}/messages", json={"message": "Hi"})
    assert response.status_code == 503
    assert response.json() == {"detail": "CHAT_UNAVAILABLE"}


def test_llm_failure_is_generic_503(anon, chat_project, fake_ai):
    fake_ai.llm_error = RuntimeError("provider exploded with sk-test-secret-1234")
    response = anon.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi"})
    assert response.status_code == 503
    assert response.json() == {"detail": "CHAT_UNAVAILABLE"}


def test_required_visitor_name_is_enforced(client, anon, chat_project):
    client.put(f"/projects/{chat_project['id']}", json={"requireVisitorName": True})
    slug = chat_project["shareSlug"]
    for path in (f"/public/{slug}/messages", f"/public/{slug}/messages/stream"):
        response = anon.post(path, json={"message": "Hi"})
        assert response.status_code == 400
        assert response.json() == {"detail": "VISITOR_NAME_REQUIRED"}
    assert anon.post(f"/public/{slug}/transcriptions", files=_audio()).json() == {"detail": "VISITOR_NAME_REQUIRED"}


def test_message_validation_errors(anon, chat_project):
    slug = chat_project["shareSlug"]
    assert anon.post(f"/public/{slug}/messages", json={"message": "   "}).status_code == 422
    assert anon.post(f"/public/{slug}/messages", json={"message": "x" * 8001}).status_code == 422


def test_chat_rate_limit_per_visitor(anon, chat_project, monkeypatch):
    monkeypatch.setattr(settings, "chat_max_per_visitor", 1)
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    assert anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).status_code == 200
    response = anon.post(f"/public/{slug}/messages", json={"message": "Hi"})
    assert response.status_code == 429
    assert response.json() == {"detail": "RATE_LIMIT_CHAT"}


def test_stream_sends_chunks_then_done_and_saves_conversation(client, anon, chat_project, fake_ai):
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    response = anon.post(f"/public/{slug}/messages/stream", json={"message": "Erklär mal"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert set(names[:-1]) == {"chunk"}
    chunks = [data for name, data in events if name == "chunk"]
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    assert len(chunks) >= 2
    assert " ".join(c["text"] for c in chunks) == LLM_REPLY
    assert all(base64.b64decode(c["audioBase64"]) == TTS_BYTES for c in chunks)
    assert all(c["contentType"] == "audio/mpeg" for c in chunks)
    done = events[-1][1]
    assert done["reply"] == LLM_REPLY
    assert set(done) == {"reply", "checks", "llmMs", "llmFirstTokenMs", "firstChunkMs", "firstChunkTextReadyMs", "ttsMs", "retrievalMs"}
    assert done["retrievalMs"] is None  # no knowledge base attached
    assert fake_ai.completion_calls[-1]["stream"] is True

    sessions = client.get("/conversations").json()
    assert sessions["total"] == 1
    assert sessions["items"][0]["messageCount"] == 2


def test_stream_without_tts_sends_chunks_without_audio(client, anon, chat_project):
    client.put(f"/projects/{chat_project['id']}", json={"ttsEnabled": False})
    events = parse_sse(anon.post(f"/public/{chat_project['shareSlug']}/messages/stream", json={"message": "Hi"}).text)
    chunks = [data for name, data in events if name == "chunk"]
    assert chunks and all(c["audioBase64"] is None for c in chunks)
    assert events[-1][1]["ttsMs"] is None


def test_stream_llm_failure_emits_error_event(client, anon, chat_project, fake_ai):
    fake_ai.llm_error = RuntimeError("boom")
    response = anon.post(f"/public/{chat_project['shareSlug']}/messages/stream", json={"message": "Hi"})
    assert response.status_code == 200
    assert parse_sse(response.text) == [("error", {"detail": "CHAT_UNAVAILABLE"})]
    assert client.get("/conversations").json()["total"] == 0


def test_stream_without_llm_key_is_503(client, anon, teacher):
    project = create_project(client)
    slug = publish(client, project["id"])
    response = anon.post(f"/public/{slug}/messages/stream", json={"message": "Hi"})
    assert response.status_code == 503
    assert response.json() == {"detail": "CHAT_UNAVAILABLE"}


def test_transcribe(anon, chat_project, fake_ai, monkeypatch):
    slug = chat_project["shareSlug"]
    # The local model ignores language and prompt, so they're checked where the route hands
    # them to the STT client (a cloud provider would use both).
    sent = {}
    original = LocalParakeetClient.transcribe

    def spy(self, audio_bytes, language, initial_prompt=None):
        sent.update(language=language, initial_prompt=initial_prompt)
        return original(self, audio_bytes, language, initial_prompt)

    monkeypatch.setattr(LocalParakeetClient, "transcribe", spy)
    response = anon.post(f"/public/{slug}/transcriptions", files=_audio(), data={"initial_prompt": "a" * 600 + "END"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "hallo welt"
    assert isinstance(body["sttMs"], float)
    assert fake_ai.transcribe_calls
    assert sent["language"] == "de"
    assert len(sent["initial_prompt"]) == 500 and sent["initial_prompt"].endswith("END")


def test_transcribe_without_model_files_is_503(anon, chat_project, missing_stt_model):
    # Regression: missing model files used to fall back to Whisper silently; now there is no
    # fallback engine, and visitors get the generic "voice input unavailable".
    response = anon.post(f"/public/{chat_project['shareSlug']}/transcriptions", files=_audio())
    assert response.status_code == 503
    assert response.json() == {"detail": "VOICE_INPUT_UNAVAILABLE"}


def test_transcribe_rejections(client, anon, chat_project, fake_ai):
    slug = chat_project["shareSlug"]
    bad_type = anon.post(f"/public/{slug}/transcriptions", files=_audio("text/plain"))
    assert bad_type.status_code == 400
    assert bad_type.json() == {"detail": "UNSUPPORTED_AUDIO_FORMAT"}

    too_big = anon.post(f"/public/{slug}/transcriptions", files={"audio": ("a.webm", b"x" * (10 * 1024 * 1024 + 1), "audio/webm")})
    assert too_big.status_code == 400
    assert too_big.json() == {"detail": "AUDIO_FILE_TOO_LARGE"}

    fake_ai.stt_error = RuntimeError("model crashed")
    failing = anon.post(f"/public/{slug}/transcriptions", files=_audio())
    assert failing.status_code == 503
    assert failing.json() == {"detail": "VOICE_INPUT_UNAVAILABLE"}

    client.put(f"/projects/{chat_project['id']}", json={"sttEnabled": False})
    disabled = anon.post(f"/public/{slug}/transcriptions", files=_audio())
    assert disabled.status_code == 503
    assert disabled.json() == {"detail": "VOICE_INPUT_UNAVAILABLE"}
