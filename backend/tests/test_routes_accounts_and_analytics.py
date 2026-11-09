"""Route-level tests for auth, profile, admin, site settings, analytics/conversations, and the
app-level bits (health check, security headers)."""

import io
import zipfile

from conftest import PASSWORD, browser_url, login_as, make_user, new_client

NEW_PASSWORD = "another-pass-2"


def test_health_and_security_headers(anon):
    assert anon.get("/health").json() == {"status": "ok"}  # also under the API prefix
    response = anon.get(browser_url("/health"))
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["strict-transport-security"] == "max-age=15552000"


def test_register_login_me_logout(client, anon):
    assert anon.get("/auth/registration-status").json() == {"enabled": True}

    registered = anon.post("/auth/register", json={"name": "Neu", "email": "neu@example.com", "password": PASSWORD})
    assert registered.status_code == 200
    assert registered.json()["email"] == "neu@example.com"
    assert "access_token" in registered.headers["set-cookie"]

    duplicate = new_client().post("/auth/register", json={"name": "X", "email": "neu@example.com", "password": PASSWORD})
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "EMAIL_ALREADY_REGISTERED"}
    weak = new_client().post("/auth/register", json={"name": "X", "email": "w@example.com", "password": "short"})
    assert weak.status_code == 422

    fresh = new_client()
    wrong = fresh.post("/auth/login", json={"email": "neu@example.com", "password": "wrong-pass-1"})
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "INVALID_CREDENTIALS"}
    assert fresh.post("/auth/login", json={"email": "neu@example.com", "password": PASSWORD}).status_code == 200
    assert fresh.get("/me").json()["name"] == "Neu"

    logout = fresh.post("/auth/logout")
    assert logout.status_code == 200
    assert 'access_token=""' in logout.headers["set-cookie"]


def test_session_errors(anon, engine):
    anon.cookies.set("access_token", "garbage")
    assert anon.get("/me").json() == {"detail": "SESSION_EXPIRED"}


def test_password_reset_flow_endpoints(anon, engine):
    make_user(engine, email="reset@example.com")
    response = anon.post("/auth/forgot-password", json={"email": "reset@example.com"})
    assert response.status_code == 200
    assert response.json() == {"detail": "Falls ein Konto mit dieser E-Mail existiert, wurde eine Nachricht verschickt."}
    unknown = anon.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert unknown.json() == response.json()

    invalid = anon.post("/auth/reset-password", json={"token": "nope", "newPassword": NEW_PASSWORD})
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "RESET_LINK_INVALID"}


def test_profile_routes(client, anon, engine, teacher):
    assert client.get("/me").json()["email"] == "teacher@example.com"
    assert client.put("/me", json={"school": "Gymnasium"}).json()["school"] == "Gymnasium"
    make_user(engine, email="taken@example.com")
    conflict = client.put("/me", json={"email": "taken@example.com"})
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "EMAIL_ALREADY_REGISTERED"}

    wrong = client.put("/me/password", json={"currentPassword": "nope-nope-1", "newPassword": NEW_PASSWORD})
    assert wrong.status_code == 400
    assert wrong.json() == {"detail": "CURRENT_PASSWORD_INCORRECT"}

    other_session = login_as(anon, teacher)
    changed = client.put("/me/password", json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
    assert changed.status_code == 200
    assert "access_token" in changed.headers["set-cookie"]
    assert client.get("/me").status_code == 200
    assert other_session.get("/me").json() == {"detail": "SESSION_EXPIRED"}

    assert client.post("/me/logout-everywhere").status_code == 200
    assert client.get("/me").status_code == 200


def test_delete_account(client, anon, chat_project):
    anon.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": "Hi"})
    response = client.delete("/me")
    assert response.status_code == 200
    assert 'access_token=""' in response.headers["set-cookie"]
    assert anon.get(f"/public/{chat_project['shareSlug']}").status_code == 404


def test_admin_routes(client, anon, engine):
    teacher = make_user(engine)
    login_as(anon, teacher)
    assert anon.get("/admin/users").json() == {"detail": "ADMIN_REQUIRED"}

    admin = make_user(engine, email="admin@example.com", name="Admin", is_admin=True)
    login_as(client, admin)
    assert {u["email"] for u in client.get("/admin/users").json()} == {"teacher@example.com", "admin@example.com"}

    created = client.post("/admin/users", json={"name": "Lehrkraft", "email": "lk@example.com", "password": PASSWORD})
    assert created.status_code == 200
    assert created.json()["mustChangePassword"] is True
    assert client.post("/admin/users", json={"name": "x", "email": "lk@example.com", "password": PASSWORD}).status_code == 409

    self_disable = client.put(f"/admin/users/{admin.id}", json={"enabled": False})
    assert self_disable.status_code == 400
    assert self_disable.json() == {"detail": "CANNOT_DISABLE_SELF"}
    demote_last = client.put(f"/admin/users/{admin.id}", json={"isAdmin": False})
    assert demote_last.status_code == 400
    assert demote_last.json() == {"detail": "LAST_ADMIN_PROTECTED"}
    disabled = client.put(f"/admin/users/{teacher.id}", json={"enabled": False}).json()
    assert disabled["enabled"] is False
    assert anon.get("/me").json() == {"detail": "NOT_AUTHENTICATED"}
    assert client.put("/admin/users/missing", json={"enabled": True}).json() == {"detail": "USER_NOT_FOUND"}

    reset = client.post(f"/admin/users/{teacher.id}/reset-password", json={"newPassword": NEW_PASSWORD})
    assert reset.status_code == 200
    assert client.post("/admin/users/missing/reset-password", json={"newPassword": NEW_PASSWORD}).status_code == 404


def test_site_settings(client, anon, engine):
    login_as(client, make_user(engine, is_admin=True))
    assert client.get("/admin/settings").json()["conversationRetentionDays"] == 0
    updated = client.put("/admin/settings", json={"contactEmail": "info@schule.de", "registrationEnabled": False}).json()
    assert updated["contactEmail"] == "info@schule.de"
    assert client.put("/admin/settings", json={"conversationRetentionDays": -1}).status_code == 422

    public = anon.get("/settings/public").json()
    assert public["contactEmail"] == "info@schule.de"
    assert "conversationRetentionDays" not in public
    assert anon.get("/auth/registration-status").json() == {"enabled": False}
    blocked = anon.post("/auth/register", json={"name": "x", "email": "x@example.com", "password": PASSWORD})
    assert blocked.status_code == 403
    assert blocked.json() == {"detail": "REGISTRATION_DISABLED"}


def _two_conversations(client, chat_project) -> list[str]:
    for name in ("Anna", "Ben"):
        visitor = new_client()
        visitor.get(f"/public/{chat_project['shareSlug']}")
        visitor.post(f"/public/{chat_project['shareSlug']}/messages", json={"message": f"Frage von {name}"}, headers={"X-Visitor-Name": name})
    return client.get("/conversations/ids").json()


def test_analytics_and_conversations(client, anon, engine, chat_project):
    ids = _two_conversations(client, chat_project)
    assert len(ids) == 2

    stats = client.get("/analytics/stats", params={"project_id": chat_project["id"], "period_days": 30}).json()
    assert stats["sessions"] == 2 and stats["messages"] == 4
    assert client.get("/analytics/stats", params={"model": "other/model"}).json()["sessions"] == 0

    page = client.get("/conversations", params={"page": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 2
    assert {row["visitorName"] for row in page["items"]} == {"Anna", "Ben"}
    assert client.get("/conversations", params={"page": 2}).json()["items"] == []

    detail = client.get(f"/conversations/{ids[0]}").json()
    assert detail["projectTitle"] == "Mathe-Tutor"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert client.get("/conversations/missing").json() == {"detail": "CONVERSATION_NOT_FOUND"}

    timeseries = client.get("/analytics/timeseries", params={"granularity": "day"}).json()
    assert sum(point["value"] for point in timeseries) == 2

    single = client.post("/conversations/export", json={"conversationIds": [ids[0]]})
    assert single.headers["content-type"].startswith("text/csv")
    assert single.headers["content-disposition"].startswith('attachment; filename="Mathe-Tutor_')
    assert "Zeitpunkt,Avatar,Schüler:in" in single.text

    bundle = client.post("/conversations/export", json={"conversationIds": ids})
    assert bundle.headers["content-type"] == "application/zip"
    assert bundle.headers["content-disposition"] == 'attachment; filename="lola-gespraeche.zip"'
    assert len(zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()) == 2

    assert client.post("/conversations/export", json={"conversationIds": ["missing"]}).json() == {"detail": "CONVERSATION_NOT_FOUND"}

    stranger = login_as(anon, make_user(engine, email="other@example.com"))
    assert stranger.post("/conversations/batch-delete", json={"conversationIds": ids}).status_code == 204
    assert client.get("/conversations").json()["total"] == 2
    assert client.post("/conversations/batch-delete", json={"conversationIds": ids}).status_code == 204
    assert client.get("/conversations").json()["total"] == 0


def test_robots_txt_keeps_crawlers_out_of_chat_dashboard_and_auth(anon):
    response = anon.get(browser_url("/robots.txt"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    lines = response.text.splitlines()
    assert lines[0] == "User-agent: *"
    for path in ("/c/", "/dashboard", "/login", "/register", "/forgot-password", "/reset-password", "/api/v1/"):
        assert f"Disallow: {path}" in lines
    # The landing page and the legal pages stay crawlable.
    assert "Disallow: /" not in lines
