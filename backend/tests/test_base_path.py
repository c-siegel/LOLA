"""BASE_PATH: a site served under a sub-path (e.g. https://host.example/lola/) hands out browser
URLs that include the prefix, while the routes themselves stay at /api/v1."""

from types import SimpleNamespace

import pytest
from fastapi import Response

from app.core.config import Settings, settings
from app.core.cookies import clear_auth_cookie, get_or_set_visitor_id, set_auth_cookie
from conftest import browser_url, create_key, create_project, publish


@pytest.mark.parametrize("raw", ["lola", "/lola", "/lola/", " lola/ "])
def test_base_path_is_normalized(monkeypatch, raw):
    monkeypatch.setenv("BASE_PATH", raw)
    assert Settings().base_path == "/lola"


@pytest.mark.parametrize("raw", ["", "/"])
def test_root_base_path_is_empty(monkeypatch, raw):
    monkeypatch.setenv("BASE_PATH", raw)
    assert Settings().base_path == ""


def test_handed_out_urls_carry_the_base_path(client, anon, teacher, fake_ai, monkeypatch):
    monkeypatch.setattr(settings, "base_path", "/lola")
    monkeypatch.setattr(settings, "browser_stt_enabled", True)
    llm_key = create_key(client)
    project = create_project(client, llmApiKeyId=llm_key["id"], builtinAvatar="julia", sttEnabled=True)
    slug = publish(client, project["id"])

    public = anon.get(f"/public/{slug}").json()
    assert public["avatarModelUrl"] == "/lola/avatars/julia.glb"
    assert public["browserSttModelUrl"] == "/lola" + settings.browser_stt_model_url


def test_root_site_urls_are_unchanged(client, anon, teacher, fake_ai, monkeypatch):
    monkeypatch.setattr(settings, "base_path", "")
    llm_key = create_key(client)
    project = create_project(client, llmApiKeyId=llm_key["id"], builtinAvatar="julia")
    slug = publish(client, project["id"])

    assert anon.get(f"/public/{slug}").json()["avatarModelUrl"] == "/avatars/julia.glb"


@pytest.mark.parametrize(("base_path", "cookie_path"), [("", "/"), ("/lola", "/lola/")])
def test_cookies_are_scoped_to_the_base_path(monkeypatch, base_path, cookie_path):
    """On a host shared with other apps, our cookies must not be sent to them."""
    monkeypatch.setattr(settings, "base_path", base_path)
    user = SimpleNamespace(id="user-1", token_version=0)

    login = Response()
    set_auth_cookie(login, user)
    logout = Response()
    clear_auth_cookie(logout)
    visitor = Response()
    get_or_set_visitor_id(SimpleNamespace(cookies={}), visitor)

    for response in (login, logout, visitor):
        assert f"Path={cookie_path};" in response.headers["set-cookie"]


def test_robots_txt_carries_the_base_path(anon, monkeypatch):
    monkeypatch.setattr(settings, "base_path", "/lola")
    lines = anon.get(browser_url("/robots.txt")).text.splitlines()
    assert "Disallow: /lola/c/" in lines
    assert "Disallow: /lola/dashboard" in lines
    assert "Disallow: /c/" not in lines
