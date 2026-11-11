"""Route tests for the dashboard's latency test (features/chat/latency_router.py): the owner's
own chat with switchable LLM key, TTS path and streaming, timed per module, never saved."""

import base64

from conftest import LLM_REPLY, TTS_BYTES, create_key, create_project, login_as, make_user, parse_sse

from app.core.config import settings


def _audio():
    return {"audio": ("rec.webm", b"fake-audio", "audio/webm")}


def _send(client, project_id, **body):
    return client.post(f"/projects/{project_id}/latency-test/messages", json={"message": "Erklär mal", **body})


def test_streamed_reply_carries_timings_and_is_never_saved(client, chat_project, fake_ai):
    response = _send(client, chat_project["id"])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    chunks = [data for name, data in events if name == "chunk"]
    assert len(chunks) >= 2
    assert " ".join(c["text"] for c in chunks) == LLM_REPLY
    assert all(base64.b64decode(c["audioBase64"]) == TTS_BYTES for c in chunks)
    for chunk in chunks:
        assert chunk["textReadyMs"] <= chunk["sentMs"]
        assert chunk["ttsMs"] >= 0
    done = events[-1]
    assert done[0] == "done"
    assert done[1]["llmFirstTokenMs"] <= done[1]["llmMs"]
    # chat_project saves conversations — a test message still must not become one.
    assert client.get("/conversations").json()["total"] == 0


def test_plain_reply_comes_as_one_chunk(client, chat_project, fake_ai):
    events = parse_sse(_send(client, chat_project["id"], streaming=False).text)

    assert [name for name, _ in events] == ["chunk", "done"]
    chunk = events[0][1]
    assert chunk["text"] == LLM_REPLY
    assert base64.b64decode(chunk["audioBase64"]) == TTS_BYTES
    assert chunk["textReadyMs"] == events[1][1]["llmMs"]
    assert "stream" not in fake_ai.completion_calls[-1] or not fake_ai.completion_calls[-1]["stream"]
    assert client.get("/conversations").json()["total"] == 0


def test_llm_key_override_uses_that_keys_model(client, chat_project, fake_ai):
    other = create_key(client, modelId="gpt-4.1-nano")

    _send(client, chat_project["id"], llmApiKeyId=other["id"], streaming=False)

    assert fake_ai.completion_calls[-1]["model"].endswith("gpt-4.1-nano")


def test_llm_key_override_works_for_a_project_without_its_own_key(client, teacher, fake_ai):
    key = create_key(client)
    project = create_project(client)

    events = parse_sse(_send(client, project["id"], llmApiKeyId=key["id"], streaming=False).text)

    assert events[0][1]["text"] == LLM_REPLY


def test_llm_key_override_must_be_an_own_llm_key(client, engine, teacher, chat_project):
    tts_key = create_key(client, key_type="tts")
    assert _send(client, chat_project["id"], llmApiKeyId=tts_key["id"]).status_code == 404

    login_as(client, make_user(engine, email="other@example.com"))
    foreign = create_key(client)
    login_as(client, teacher)
    response = _send(client, chat_project["id"], llmApiKeyId=foreign["id"])
    assert response.status_code == 404
    assert response.json() == {"detail": "API_KEY_NOT_FOUND"}


def test_tts_mode_none_sends_text_only(client, chat_project, fake_ai):
    events = parse_sse(_send(client, chat_project["id"], ttsMode="none").text)

    chunks = [data for name, data in events if name == "chunk"]
    assert chunks and all(c["audioBase64"] is None and c["ttsMs"] is None for c in chunks)
    assert fake_ai.speech_calls == []


def test_tts_mode_local_needs_the_sidecar(client, chat_project, monkeypatch):
    monkeypatch.setattr(settings, "local_tts_enabled", False)
    response = _send(client, chat_project["id"], ttsMode="local")
    assert response.status_code == 400
    assert response.json() == {"detail": "TTS_NOT_CONFIGURED"}


def test_without_any_llm_key_is_400(client, teacher):
    project = create_project(client)
    response = _send(client, project["id"])
    assert response.status_code == 400
    assert response.json() == {"detail": "NO_LLM_MODEL_SELECTED"}


def test_llm_failure_is_reported_with_its_message(client, chat_project, fake_ai):
    fake_ai.llm_error = RuntimeError("model overloaded")
    events = parse_sse(_send(client, chat_project["id"], streaming=False).text)
    assert events == [("error", {"detail": "LLM_REQUEST_FAILED", "message": "model overloaded"})]


def test_transcription_reports_engine_and_server_time(client, chat_project, fake_ai):
    response = client.post(
        f"/projects/{chat_project['id']}/latency-test/transcriptions", files=_audio(), data={"engine": "parakeet"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "hallo welt"
    assert body["engine"] == "parakeet"
    assert body["sttMs"] >= 0


def test_the_removed_whisper_engine_is_rejected(client, chat_project, fake_ai):
    response = client.post(
        f"/projects/{chat_project['id']}/latency-test/transcriptions", files=_audio(), data={"engine": "whisper"}
    )
    assert response.status_code == 422


def test_transcription_without_model_files_is_503(client, chat_project, missing_stt_model):
    response = client.post(f"/projects/{chat_project['id']}/latency-test/transcriptions", files=_audio())
    assert response.status_code == 503
    assert response.json() == {"detail": "SERVER_STT_MODEL_MISSING"}


def test_only_the_owner_can_run_tests(client, anon, engine, chat_project):
    assert anon.post(f"/projects/{chat_project['id']}/latency-test/messages", json={"message": "Hi"}).status_code == 401

    login_as(client, make_user(engine, email="other@example.com"))
    for response in (
        _send(client, chat_project["id"]),
        client.post(f"/projects/{chat_project['id']}/latency-test/transcriptions", files=_audio()),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "PROJECT_NOT_FOUND"}


USAGE = {"promptTokens": 1200, "completionTokens": 40, "cachedTokens": 1024, "reasoningTokens": 0}


def test_streamed_done_reports_token_usage(client, chat_project, fake_ai):
    events = parse_sse(_send(client, chat_project["id"]).text)

    assert events[-1][1]["usage"] == USAGE
    assert fake_ai.completion_calls[-1]["stream_options"] == {"include_usage": True}
    # The usage chunk has no choices; it must not end up in (or break) the reply text.
    assert " ".join(data["text"] for name, data in events if name == "chunk") == LLM_REPLY


def test_plain_done_reports_token_usage(client, chat_project, fake_ai):
    events = parse_sse(_send(client, chat_project["id"], streaming=False).text)

    assert events[-1][1]["usage"] == USAGE


def test_usage_fields_stay_null_when_the_provider_reports_none(client, chat_project, fake_ai):
    fake_ai.llm_usage = None

    done = parse_sse(_send(client, chat_project["id"]).text)[-1][1]

    assert done["usage"] == {"promptTokens": None, "completionTokens": None, "cachedTokens": None, "reasoningTokens": None}


def test_public_chat_never_asks_for_or_sends_token_usage(anon, chat_project, fake_ai):
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    done = parse_sse(anon.post(f"/public/{slug}/messages/stream", json={"message": "Hi"}).text)[-1][1]

    assert "usage" not in done
    assert "stream_options" not in fake_ai.completion_calls[-1]
    assert "usage" not in anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()


def test_done_reports_the_reasoning_route_only_to_the_owner(client, chat_project, fake_ai, anon) -> None:
    for streaming in (True, False):
        assert parse_sse(_send(client, chat_project["id"], streaming=streaming).text)[-1][1]["route"] == "default"

    client.put(f"/projects/{chat_project['id']}", json={"reasoningMode": "off"})
    assert parse_sse(_send(client, chat_project["id"]).text)[-1][1]["route"] == "fast"

    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    assert "route" not in parse_sse(anon.post(f"/public/{slug}/messages/stream", json={"message": "Hi"}).text)[-1][1]
    assert "route" not in anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()
