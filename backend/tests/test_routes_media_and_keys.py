"""Route-level tests for the avatar/background libraries, the profile picture, and the API-key
management routes (/api-keys)."""

from app.features.media import avatars_router, backgrounds_router
from conftest import browser_url, create_key, create_project, login_as, make_user, publish

GLB = b"glTF" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
IMMUTABLE = "public, max-age=31536000, immutable"


def test_avatar_library(client, anon, engine, teacher):
    bad_ext = client.post("/avatars", files={"file": ("a.obj", GLB, "application/octet-stream")})
    assert bad_ext.status_code == 400
    assert bad_ext.json() == {"detail": "AVATAR_FILE_INVALID_TYPE"}
    bad_magic = client.post("/avatars", files={"file": ("a.glb", b"nope" * 4, "model/gltf-binary")})
    assert bad_magic.status_code == 400
    assert bad_magic.json() == {"detail": "AVATAR_FILE_INVALID_CONTENT"}

    avatar = client.post("/avatars", files={"file": ("Julia.glb", GLB, "model/gltf-binary")}).json()
    assert avatar["name"] == "Julia"
    assert avatar["fileUrl"] == f"/api/v1/avatars/{avatar['id']}/file"
    assert avatar["thumbnailUrl"] is None
    assert [a["id"] for a in client.get("/avatars").json()] == [avatar["id"]]

    owner_file = client.get(browser_url(avatar["fileUrl"]))
    assert owner_file.status_code == 200
    assert owner_file.content == GLB
    assert owner_file.headers["content-type"] == "model/gltf-binary"
    assert owner_file.headers["cache-control"] == IMMUTABLE

    # Anonymous access only once a published project uses it.
    assert anon.get(browser_url(avatar["fileUrl"])).json() == {"detail": "AVATAR_NOT_FOUND"}
    project = create_project(client, avatarModelId=avatar["id"])
    assert project["avatarModelUrl"] == avatar["fileUrl"]
    assert anon.get(browser_url(avatar["fileUrl"])).status_code == 404
    publish(client, project["id"])
    assert anon.get(browser_url(avatar["fileUrl"])).content == GLB

    bad_thumb = client.post(f"/avatars/{avatar['id']}/thumbnail", files={"file": ("t.png", JPEG, "image/png")})
    assert bad_thumb.json() == {"detail": "AVATAR_THUMBNAIL_INVALID"}
    with_thumb = client.post(f"/avatars/{avatar['id']}/thumbnail", files={"file": ("t.png", PNG, "image/png")}).json()
    assert with_thumb["thumbnailUrl"] == f"/api/v1/avatars/{avatar['id']}/thumbnail"
    thumb = client.get(browser_url(with_thumb["thumbnailUrl"]))
    assert thumb.content == PNG
    assert thumb.headers["content-type"] == "image/png"

    stranger = login_as(anon, make_user(engine, email="other@example.com"))
    assert stranger.delete(f"/avatars/{avatar['id']}").json() == {"detail": "AVATAR_NOT_FOUND"}
    assert stranger.get(browser_url(with_thumb["thumbnailUrl"])).json() == {"detail": "AVATAR_THUMBNAIL_NOT_FOUND"}

    assert client.delete(f"/avatars/{avatar['id']}").status_code == 204
    assert client.get(browser_url(avatar["fileUrl"])).status_code == 404
    assert client.get("/avatars").json() == []


def test_background_library(client, anon, teacher):
    bad_ext = client.post("/backgrounds", files={"file": ("b.gif", PNG, "image/gif")})
    assert bad_ext.json() == {"detail": "BACKGROUND_INVALID_TYPE"}
    bad_magic = client.post("/backgrounds", files={"file": ("b.png", b"nope" * 4, "image/png")})
    assert bad_magic.json() == {"detail": "BACKGROUND_INVALID_CONTENT"}

    # The stored extension follows the content, not the uploaded name.
    background = client.post("/backgrounds", files={"file": ("Klasse.png", JPEG, "image/png")}).json()
    assert background["name"] == "Klasse"
    assert background["fileUrl"] == f"/api/v1/backgrounds/{background['id']}/file"
    served = client.get(browser_url(background["fileUrl"]))
    assert served.content == JPEG
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["cache-control"] == IMMUTABLE

    assert anon.get(browser_url(background["fileUrl"])).json() == {"detail": "BACKGROUND_NOT_FOUND"}
    project = create_project(client, avatarBackgroundId=background["id"])
    assert project["avatarBackgroundUrl"] == background["fileUrl"]
    publish(client, project["id"])
    assert anon.get(browser_url(background["fileUrl"])).status_code == 200

    assert client.delete(f"/backgrounds/{background['id']}").status_code == 204
    assert client.get("/backgrounds").json() == []


def test_oversized_library_uploads_are_refused(client, teacher, monkeypatch):
    monkeypatch.setattr(avatars_router, "_MAX_UPLOAD_BYTES", len(GLB) - 1)
    monkeypatch.setattr(avatars_router, "_MAX_THUMBNAIL_BYTES", len(PNG) - 1)
    monkeypatch.setattr(backgrounds_router, "_MAX_UPLOAD_BYTES", len(PNG) - 1)

    avatar = client.post("/avatars", files={"file": ("a.glb", GLB, "model/gltf-binary")})
    assert avatar.status_code == 400
    assert avatar.json() == {"detail": "AVATAR_FILE_TOO_LARGE"}
    background = client.post("/backgrounds", files={"file": ("b.png", PNG, "image/png")})
    assert background.status_code == 400
    assert background.json() == {"detail": "BACKGROUND_FILE_TOO_LARGE"}

    monkeypatch.setattr(avatars_router, "_MAX_UPLOAD_BYTES", len(GLB))
    avatar_id = client.post("/avatars", files={"file": ("a.glb", GLB, "model/gltf-binary")}).json()["id"]
    thumb = client.post(f"/avatars/{avatar_id}/thumbnail", files={"file": ("t.png", PNG, "image/png")})
    assert thumb.status_code == 400
    assert thumb.json() == {"detail": "AVATAR_THUMBNAIL_TOO_LARGE"}


def test_profile_picture(client, teacher):
    invalid = client.post("/me/picture", files={"file": ("p.gif", b"GIF89a" + b"\x00" * 8, "image/gif")})
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "PROFILE_PICTURE_INVALID_TYPE"}
    too_big = client.post("/me/picture", files={"file": ("p.png", PNG + b"\x00" * (5 * 1024 * 1024), "image/png")})
    assert too_big.json() == {"detail": "PROFILE_PICTURE_TOO_LARGE"}

    user = client.post("/me/picture", files={"file": ("p.webp", WEBP, "image/webp")}).json()
    assert user["avatarUrl"].startswith("/api/v1/me/picture?v=")
    picture = client.get(browser_url(user["avatarUrl"]))
    assert picture.content == WEBP
    assert picture.headers["content-type"] == "image/webp"

    assert client.delete("/me/picture").json()["avatarUrl"] is None
    assert client.get("/me/picture").json() == {"detail": "PROFILE_PICTURE_NOT_FOUND"}


def test_providers_list(client, teacher):
    providers = client.get("/providers").json()
    openai = next(p for p in providers if p["value"] == "openai")
    assert openai["label"] == "OpenAI"
    assert "llm" in openai["supportedTypes"] and "tts" in openai["supportedTypes"]
    assert openai["ttsModelFixed"] is True
    assert providers[0]["value"] == "anthropic"


def test_api_key_crud_and_usage(client, anon, engine, teacher):
    llm = create_key(client, label="  Mein Key  ")
    assert llm["maskedKey"].endswith("1234") and "sk-test" not in llm["maskedKey"]
    assert llm["status"] == "unverified"
    assert llm["label"] == "Mein Key"
    tts = create_key(client, key_type="tts")
    stt = create_key(client, key_type="stt", provider="gwdg_saia")

    create_project(client, llmApiKeyId=llm["id"], ttsApiKeyId=tts["id"])
    project = create_project(client, llmApiKeyId=llm["id"], sttApiKeyId=stt["id"])
    usage = {k["id"]: k["usedByProjects"] for k in client.get("/api-keys").json()}
    assert usage == {llm["id"]: 2, tts["id"]: 1, stt["id"]: 1}

    # Updating the model re-syncs every project's denormalized llm_model; an empty key keeps the secret.
    updated = client.put(
        f"/api-keys/{llm['id']}",
        json={"provider": "openai", "keyType": "llm", "modelId": "gpt-4o", "apiKey": ""},
    ).json()
    assert updated["maskedKey"] == llm["maskedKey"]
    assert updated["label"] is None
    assert client.get(f"/projects/{project['id']}").json()["llmModel"] == "openai/gpt-4o"

    # Deleting a key clears every project reference to it.
    assert client.delete(f"/api-keys/{llm['id']}").status_code == 200
    assert client.delete(f"/api-keys/{stt['id']}").status_code == 200
    after = client.get(f"/projects/{project['id']}").json()
    assert after["llmApiKeyId"] is None and after["llmModel"] is None and after["sttApiKeyId"] is None

    stranger = login_as(anon, make_user(engine, email="other@example.com"))
    assert stranger.get("/api-keys").json() == []
    for response in (
        stranger.put(f"/api-keys/{tts['id']}", json={"provider": "openai", "keyType": "tts"}),
        stranger.delete(f"/api-keys/{tts['id']}"),
        stranger.post(f"/api-keys/{tts['id']}/test"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "API_KEY_NOT_FOUND"}


def test_api_key_validation(client, teacher):
    unknown = client.post("/api-keys", json={"provider": "nope", "apiKey": "x"})
    assert unknown.status_code == 422
    metadata = client.post(
        "/api-keys",
        json={"provider": "openai_compatible", "apiKey": "x", "modelId": "m", "apiBase": "http://169.254.169.254/v1"},
    )
    assert metadata.status_code == 422


def test_api_key_test_button(client, teacher, fake_ai):
    llm = create_key(client)
    ok = client.post(f"/api-keys/{llm['id']}/test").json()
    assert ok == {"status": "active", "message": None}
    assert fake_ai.completion_calls[-1]["max_tokens"] == 1

    fake_ai.llm_error = RuntimeError("denied for sk-test-secret-1234")
    failed = client.post(f"/api-keys/{llm['id']}/test").json()
    assert failed == {"status": "error", "message": "denied for [REDACTED]"}
    assert client.get("/api-keys").json()[0]["status"] == "error"

    tts = create_key(client, key_type="tts")
    assert client.post(f"/api-keys/{tts['id']}/test").json()["status"] == "active"
    assert fake_ai.speech_calls[-1]["input"] == "Test"

    cartesia = create_key(client, key_type="tts", provider="cartesia", modelId="sonic-2")
    needs_voice = client.post(f"/api-keys/{cartesia['id']}/test").json()
    assert needs_voice == {"status": "unverified", "message": "TTS_KEY_TEST_NEEDS_VOICE"}
