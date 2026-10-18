"""Routes of the admin-editable public pages (features/pages): the landing page's texts, the
imprint and privacy policy, custom pages like an accessibility statement, and their history."""

from sqlmodel import Session, select

from app.features.pages.models import SitePage, SitePageRevision
from tests.conftest import login_as, make_user


def _admin(client, engine, email="admin@example.com", name="Admin"):
    user = make_user(engine, email=email, name=name, is_admin=True)
    login_as(client, user)
    return user


def _page(client, kind: str) -> dict:
    return next(page for page in client.get("/admin/pages").json() if page["kind"] == kind)


def test_admin_list_has_the_builtin_pages_first(client, engine):
    _admin(client, engine)
    pages = client.get("/admin/pages").json()
    assert [(p["kind"], p["slug"]) for p in pages] == [
        ("landing", "landing"),
        ("imprint", "impressum"),
        ("privacy", "datenschutz"),
    ]
    assert all(p["translations"] == [] and p["lastChange"] is None for p in pages)


def test_page_routes_require_an_admin(client, teacher, anon):
    assert client.get("/admin/pages").status_code == 403
    assert client.post("/admin/pages", json={"slug": "x"}).status_code == 403
    assert anon.get("/admin/pages").status_code == 401


def test_untouched_builtin_page_is_public_without_creating_rows(client, engine, anon):
    response = anon.get("/pages/impressum")
    assert response.status_code == 200
    assert response.json() == {"slug": "impressum", "kind": "imprint", "translations": {}}
    with Session(engine) as session:
        assert session.exec(select(SitePage)).first() is None
    assert anon.get("/pages/does-not-exist").status_code == 404


def test_imprint_override_is_served_per_language_and_resettable(client, engine, anon):
    _admin(client, engine)
    imprint = _page(client, "imprint")
    saved = client.put(
        f"/admin/pages/{imprint['id']}/translations/en",
        json={"title": "Legal notice", "contentMd": "# Operator\n\nExample University"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["translations"][0]["locale"] == "en"

    public = anon.get("/pages/impressum").json()
    assert set(public["translations"]) == {"en"}
    assert public["translations"]["en"]["contentMd"].startswith("# Operator")

    reset = client.delete(f"/admin/pages/{imprint['id']}/translations/en")
    assert reset.json()["translations"] == []
    assert anon.get("/pages/impressum").json()["translations"] == {}


def test_saving_an_empty_text_resets_that_language(client, engine):
    _admin(client, engine)
    privacy = _page(client, "privacy")
    client.put(f"/admin/pages/{privacy['id']}/translations/de", json={"contentMd": "Text"})
    response = client.put(f"/admin/pages/{privacy['id']}/translations/de", json={"contentMd": "   ", "title": ""})
    assert response.json()["translations"] == []


def test_landing_fields_are_validated_and_trimmed(client, engine, anon):
    _admin(client, engine)
    landing = _page(client, "landing")
    url = f"/admin/pages/{landing['id']}/translations/de"
    assert client.put(url, json={"fields": {"nope": "x"}}).json()["detail"] == "PAGE_FIELDS_NOT_ALLOWED"
    assert client.put(url, json={"fields": {"badge": "x" * 2001}}).status_code == 422

    saved = client.put(url, json={"title": "ignored", "fields": {"headingFull": "  Hallo  ", "badge": " "}})
    assert saved.status_code == 200, saved.text
    translation = anon.get("/pages/landing").json()["translations"]["de"]
    assert translation["fields"] == {"headingFull": "Hallo"}
    assert translation["title"] == ""

    imprint = _page(client, "imprint")
    response = client.put(f"/admin/pages/{imprint['id']}/translations/de", json={"fields": {"badge": "x"}})
    assert response.json()["detail"] == "PAGE_FIELDS_NOT_ALLOWED"


def test_unknown_locale_is_rejected(client, engine):
    _admin(client, engine)
    imprint = _page(client, "imprint")
    assert client.put(f"/admin/pages/{imprint['id']}/translations/fr", json={"contentMd": "x"}).status_code == 422


def test_custom_page_lifecycle(client, engine, anon):
    _admin(client, engine)
    created = client.post("/admin/pages", json={"slug": "Barrierefreiheit"})
    assert created.status_code == 201, created.text
    page = created.json()
    assert page["slug"] == "barrierefreiheit" and page["isPublished"] is False

    url = f"/admin/pages/{page['id']}/translations/de"
    assert client.put(url, json={"contentMd": "Text"}).json()["detail"] == "PAGE_TITLE_REQUIRED"
    assert client.put(url, json={"title": "Barrierefreiheit", "contentMd": "Text"}).status_code == 200

    # Not published yet: neither the page nor its footer link exist publicly.
    assert anon.get("/pages/barrierefreiheit").status_code == 404
    assert anon.get("/pages").json() == []

    client.patch(f"/admin/pages/{page['id']}", json={"isPublished": True})
    assert anon.get("/pages/barrierefreiheit").json()["translations"]["de"]["title"] == "Barrierefreiheit"
    assert anon.get("/pages").json() == [{"slug": "barrierefreiheit", "titles": {"de": "Barrierefreiheit"}}]

    client.patch(f"/admin/pages/{page['id']}", json={"showInFooter": False})
    assert anon.get("/pages").json() == []

    assert client.delete(f"/admin/pages/{page['id']}").status_code == 204
    assert anon.get("/pages/barrierefreiheit").status_code == 404
    with Session(engine) as session:
        assert session.exec(select(SitePageRevision)).first() is None


def test_published_custom_page_without_text_stays_hidden(client, engine, anon):
    _admin(client, engine)
    client.post("/admin/pages", json={"slug": "leer", "isPublished": True})
    assert anon.get("/pages/leer").status_code == 404


def test_slug_rules(client, engine):
    _admin(client, engine)
    assert client.post("/admin/pages", json={"slug": "no spaces"}).status_code == 422
    assert client.post("/admin/pages", json={"slug": "impressum"}).json()["detail"] == "PAGE_SLUG_TAKEN"
    first = client.post("/admin/pages", json={"slug": "faq"}).json()
    assert client.post("/admin/pages", json={"slug": "faq"}).status_code == 409
    second = client.post("/admin/pages", json={"slug": "hilfe"}).json()
    assert client.patch(f"/admin/pages/{second['id']}", json={"slug": "faq"}).status_code == 409
    assert client.patch(f"/admin/pages/{first['id']}", json={"slug": "faq"}).status_code == 200


def test_builtin_pages_cannot_be_moved_or_deleted(client, engine):
    _admin(client, engine)
    imprint = _page(client, "imprint")
    assert client.patch(f"/admin/pages/{imprint['id']}", json={"slug": "x"}).json()["detail"] == "PAGE_BUILTIN_PROTECTED"
    assert client.patch(f"/admin/pages/{imprint['id']}", json={"isPublished": False}).status_code == 400
    assert client.delete(f"/admin/pages/{imprint['id']}").json()["detail"] == "PAGE_BUILTIN_PROTECTED"


def test_history_records_who_changed_what(client, engine):
    _admin(client, engine, name="Ada")
    privacy = _page(client, "privacy")
    client.put(f"/admin/pages/{privacy['id']}/translations/de", json={"contentMd": "Version 1"})
    client.put(f"/admin/pages/{privacy['id']}/translations/de", json={"contentMd": "Version 2"})
    client.delete(f"/admin/pages/{privacy['id']}/translations/de")
    # Resetting a language that has no text is not a change.
    client.delete(f"/admin/pages/{privacy['id']}/translations/en")

    history = client.get(f"/admin/pages/{privacy['id']}/history").json()
    assert [(h["action"], h["locale"]) for h in history] == [
        ("content_reset", "de"),
        ("content_saved", "de"),
        ("content_saved", "de"),
    ]
    assert history[0]["author"] == {"name": "Ada", "email": "admin@example.com"}
    # Sent as UTC: a zone-less timestamp would be read as local time by the browser.
    assert history[0]["createdAt"].endswith("Z")
    assert _page(client, "privacy")["lastChange"]["at"].endswith("Z")
    assert _page(client, "privacy")["lastChange"]["author"]["name"] == "Ada"


def test_deleting_an_account_keeps_the_history_without_the_author(client, engine):
    ada = _admin(client, engine, email="ada@example.com", name="Ada")
    privacy = _page(client, "privacy")
    client.put(f"/admin/pages/{privacy['id']}/translations/de", json={"contentMd": "Text"})
    login_as(client, ada)
    assert client.delete("/me").status_code == 200

    _admin(client, engine, email="bob@example.com", name="Bob")
    history = client.get(f"/admin/pages/{privacy['id']}/history").json()
    assert len(history) == 1 and history[0]["author"] is None
