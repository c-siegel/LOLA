"""Route-level tests for /projects: CRUD, publishing, YAML export/import, the configurator's
preview chat and transcription, and the cached start-prompt audio."""

import base64

from conftest import LLM_REPLY, TTS_BYTES, browser_url, create_key, create_project, login_as, make_user, publish


def test_crud_and_ownership(client, anon, engine, teacher):
    project = create_project(client, description="Bruchrechnung")
    assert project["status"] == "draft"
    assert project["published"] is False
    assert project["passwordProtected"] is False
    assert project["temperature"] == 0.5
    assert project["startAudioUrl"] is None

    assert [p["id"] for p in client.get("/projects").json()] == [project["id"]]
    assert client.get(f"/projects/{project['id']}").json()["description"] == "Bruchrechnung"

    updated = client.put(f"/projects/{project['id']}", json={"title": "Neu", "gradeLevel": "7", "temperature": 1.2})
    assert updated.status_code == 200
    assert updated.json()["title"] == "Neu"
    assert updated.json()["gradeLevel"] == "7"
    # null clears a clearable field, but is ignored for a non-clearable one.
    cleared = client.put(f"/projects/{project['id']}", json={"gradeLevel": None, "title": None}).json()
    assert cleared["gradeLevel"] is None
    assert cleared["title"] == "Neu"

    stranger = login_as(anon, make_user(engine, email="other@example.com"))
    for method in ("get", "put", "delete"):
        kwargs = {"json": {}} if method == "put" else {}
        response = getattr(stranger, method)(f"/projects/{project['id']}", **kwargs)
        assert response.status_code == 404
        assert response.json() == {"detail": "PROJECT_NOT_FOUND"}

    assert client.delete(f"/projects/{project['id']}").status_code == 204
    assert client.get(f"/projects/{project['id']}").status_code == 404


def test_requires_login(anon):
    response = anon.get("/projects")
    assert response.status_code == 401
    assert response.json() == {"detail": "NOT_AUTHENTICATED"}


def test_validation_errors(client, teacher):
    project = create_project(client)
    assert client.put(f"/projects/{project['id']}", json={"temperature": 3}).status_code == 422
    assert client.put(f"/projects/{project['id']}", json={"chatPassword": "abc"}).status_code == 422


def test_key_references_must_be_own_and_of_matching_type(client, teacher):
    llm_key = create_key(client)
    tts_key = create_key(client, key_type="tts")

    wrong_type = client.post("/projects", json={"title": "x", "llmApiKeyId": tts_key["id"]})
    assert wrong_type.status_code == 400
    assert wrong_type.json() == {"detail": "UNKNOWN_API_KEY"}
    project = create_project(client)
    response = client.put(f"/projects/{project['id']}", json={"ttsApiKeyId": llm_key["id"]})
    assert response.status_code == 400
    assert response.json() == {"detail": "UNKNOWN_API_KEY"}

    ok = client.put(f"/projects/{project['id']}", json={"llmApiKeyId": llm_key["id"]}).json()
    assert ok["llmModel"] == "openai/gpt-4o-mini"
    assert client.put(f"/projects/{project['id']}", json={"llmApiKeyId": None}).json()["llmModel"] is None


def test_chat_password_set_and_clear(client, teacher):
    project = create_project(client, chatPassword="geheim")
    assert project["passwordProtected"] is True
    assert client.put(f"/projects/{project['id']}", json={"title": "x"}).json()["passwordProtected"] is True
    assert client.put(f"/projects/{project['id']}", json={"chatPassword": None}).json()["passwordProtected"] is False


def test_stats_route_is_not_shadowed_by_project_id(client, anon, teacher, chat_project):
    anon.get(f"/public/{chat_project['shareSlug']}")
    anon.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi"})
    create_project(client, title="Entwurf")

    response = client.get("/analytics/overview")
    assert response.status_code == 200
    assert response.json() == {
        "totalProjects": 2,
        "publishedProjects": 1,
        "sessionsLast7Days": 1,
        "messagesLast7Days": 2,
    }


def test_publish_unpublish_republish(client, teacher):
    project = create_project(client)
    slug = publish(client, project["id"])
    assert len(slug) == 5 and slug.isupper()
    unpublished = client.delete(f"/projects/{project['id']}/publication").json()
    assert unpublished["published"] is False
    new_slug = publish(client, project["id"])
    assert new_slug != slug


def test_export_and_import_roundtrip(client, teacher):
    project = create_project(client, preprompt="Sei nett", startPrompt="Hallo", chatPassword="geheim", temperature=0.9)
    publish(client, project["id"])

    exported = client.get(f"/projects/{project['id']}/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/yaml")
    assert exported.headers["content-disposition"] == 'attachment; filename="mathe-tutor.yml"'
    assert "geheim" not in exported.text

    imported = client.post("/projects/import", files={"file": ("p.yml", exported.content, "application/yaml")})
    assert imported.status_code == 200
    body = imported.json()
    assert body["id"] != project["id"]
    assert body["preprompt"] == "Sei nett"
    assert body["temperature"] == 0.9
    assert body["published"] is False
    assert body["passwordProtected"] is False


def test_import_rejections(client, teacher):
    invalid = client.post("/projects/import", files={"file": ("p.yml", b"not: [valid", "application/yaml")})
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "PROJECT_IMPORT_INVALID"}
    too_big = client.post("/projects/import", files={"file": ("p.yml", b"a" * (256 * 1024 + 1), "application/yaml")})
    assert too_big.status_code == 400
    assert too_big.json() == {"detail": "PROJECT_IMPORT_FILE_TOO_LARGE"}


def test_delete_removes_conversations(client, anon, chat_project):
    anon.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi"})
    assert client.get("/conversations").json()["total"] == 1
    client.delete(f"/projects/{chat_project['id']}")
    assert client.get("/conversations").json()["total"] == 0


def test_preview_message(client, chat_project, fake_ai):
    response = client.post(
        f"/projects/{chat_project['id']}/chat/messages",
        json={"message": "Test", "history": [{"role": "assistant", "content": "Hallo"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "reply": LLM_REPLY,
        "audioBase64": base64.b64encode(TTS_BYTES).decode(),
        "contentType": "audio/mpeg",
        "motions": [],
        "checks": [],
    }
    assert fake_ai.completion_calls[-1]["messages"][-2:] == [
        {"role": "assistant", "content": "Hallo"},
        {"role": "user", "content": "Test"},
    ]
    # Preview never saves conversations.
    assert client.get("/conversations").json()["total"] == 0


def test_preview_message_errors(client, teacher, chat_project, fake_ai):
    no_key = create_project(client)
    response = client.post(f"/projects/{no_key['id']}/chat/messages", json={"message": "x"})
    assert response.status_code == 400
    assert response.json() == {"detail": "NO_LLM_MODEL_SELECTED"}

    fake_ai.llm_error = RuntimeError("bad key sk-test-secret-1234 rejected")
    response = client.post(f"/projects/{chat_project['id']}/chat/messages", json={"message": "x"})
    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "LLM_REQUEST_FAILED", "message": "bad key [REDACTED] rejected"}}

    fake_ai.llm_error = None
    fake_ai.tts_error = RuntimeError("tts down")
    body = client.post(f"/projects/{chat_project['id']}/chat/messages", json={"message": "x"}).json()
    assert body["reply"] == LLM_REPLY
    assert body["audioBase64"] is None


def test_preview_transcribe_without_model_files_tells_the_teacher(client, chat_project, missing_stt_model):
    files = {"audio": ("rec.webm", b"fake-audio", "audio/webm")}
    response = client.post(f"/projects/{chat_project['id']}/chat/transcriptions", files=files)
    assert response.status_code == 503
    assert response.json() == {"detail": "SERVER_STT_MODEL_MISSING"}


def test_preview_transcribe(client, chat_project, fake_ai):
    files = {"audio": ("rec.webm", b"fake-audio", "audio/webm")}
    response = client.post(f"/projects/{chat_project['id']}/chat/transcriptions", files=files)
    assert response.status_code == 200
    assert response.json() == {"text": "hallo welt", "sttMs": None}

    bad = client.post(f"/projects/{chat_project['id']}/chat/transcriptions", files={"audio": ("a.txt", b"x", "text/plain")})
    assert bad.status_code == 400
    assert bad.json() == {"detail": "UNSUPPORTED_AUDIO_FORMAT"}

    fake_ai.stt_error = RuntimeError("model crashed")
    failing = client.post(f"/projects/{chat_project['id']}/chat/transcriptions", files=files)
    assert failing.status_code == 502
    assert failing.json()["detail"]["code"] == "STT_REQUEST_FAILED"

    client.put(f"/projects/{chat_project['id']}", json={"sttEnabled": False})
    disabled = client.post(f"/projects/{chat_project['id']}/chat/transcriptions", files=files)
    assert disabled.status_code == 400
    assert disabled.json() == {"detail": "VOICE_INPUT_DISABLED"}


def test_start_audio_lifecycle(client, anon, teacher, fake_ai):
    tts_key = create_key(client, key_type="tts")
    project = create_project(client)
    pid = project["id"]

    response = client.post(f"/projects/{pid}/start-audio")
    assert response.status_code == 400
    assert response.json() == {"detail": "START_PROMPT_REQUIRED"}
    client.put(f"/projects/{pid}", json={"startPrompt": "Willkommen"})
    response = client.post(f"/projects/{pid}/start-audio")
    assert response.status_code == 400
    assert response.json() == {"detail": "TTS_NOT_CONFIGURED"}

    client.put(f"/projects/{pid}", json={"ttsEnabled": True, "ttsApiKeyId": tts_key["id"]})
    fake_ai.tts_error = RuntimeError("fail sk-test-secret-1234")
    failed = client.post(f"/projects/{pid}/start-audio")
    assert failed.status_code == 502
    assert failed.json() == {"detail": {"code": "START_AUDIO_GENERATION_FAILED", "message": "fail [REDACTED]"}}
    fake_ai.tts_error = None

    generated = client.post(f"/projects/{pid}/start-audio").json()
    # The version query makes a regenerated file load fresh instead of from the browser cache.
    assert generated["startAudioUrl"].startswith(f"/api/v1/projects/{pid}/start-audio?v=")

    owner_get = client.get(browser_url(generated["startAudioUrl"]))
    assert owner_get.status_code == 200
    assert owner_get.content == TTS_BYTES
    assert owner_get.headers["content-type"] == "audio/mpeg"
    assert owner_get.headers["cache-control"] == "no-cache"

    hidden = anon.get(f"/projects/{pid}/start-audio")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "START_AUDIO_NOT_FOUND"}
    slug = publish(client, pid)
    assert anon.get(f"/projects/{pid}/start-audio").content == TTS_BYTES
    assert anon.get(f"/public/{slug}").json()["startAudioUrl"] == generated["startAudioUrl"]

    # Changing the start prompt invalidates the cached file.
    assert client.put(f"/projects/{pid}", json={"startPrompt": "Neu"}).json()["startAudioUrl"] is None
    assert client.get(f"/projects/{pid}/start-audio").status_code == 404


def test_changing_the_spoken_language_invalidates_the_start_audio(client, teacher, fake_ai):
    # The language decides how numbers are read, which pronunciation list applies and the local
    # TTS voice, so German greeting audio must not survive a switch to English.
    tts_key = create_key(client, key_type="tts")
    pid = create_project(client, startPrompt="Willkommen", ttsEnabled=True, ttsApiKeyId=tts_key["id"])["id"]
    assert client.post(f"/projects/{pid}/start-audio").json()["startAudioUrl"] is not None

    assert client.put(f"/projects/{pid}", json={"spokenLanguage": "de"}).json()["startAudioUrl"] is not None
    assert client.put(f"/projects/{pid}", json={"spokenLanguage": "en"}).json()["startAudioUrl"] is None


def test_avatar_references(client, teacher):
    project = create_project(client, builtinAvatar="julia")
    assert project["builtinAvatar"] == "julia"
    assert project["avatarModelUrl"] == "/avatars/julia.glb"
    assert project["avatarBackgroundUrl"] is None
    cleared = client.put(f"/projects/{project['id']}", json={"builtinAvatar": None}).json()
    assert cleared["avatarModelUrl"] is None

    invalid = client.post("/projects", json={"title": "x", "builtinAvatar": "../secret"})
    assert invalid.status_code == 422
    assert "INVALID_BUILTIN_AVATAR" in invalid.text


def test_routes_live_under_the_api_prefix_only(client, teacher):
    assert client.get(browser_url("/api/v1/projects")).status_code == 200
    assert client.get(browser_url("/projects")).status_code == 404
