"""Reasoning replies cost several times a normal one and the public chat spends the teacher's key,
so they have budgets of their own (core/rate_limit.py::deep_turn_budget). Over a budget a turn is
answered without reasoning; it is never refused."""

import pytest

from app.core.config import settings
from tests.conftest import create_key, create_project, publish

REASONING_ON = {"chat_template_kwargs": {"enable_thinking": True}}
REASONING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.fixture
def always_project(client, teacher, fake_ai):
    llm_key = create_key(client, provider="openai_compatible", apiBase="http://vllm.local/v1", modelId="qwen3")
    project = create_project(client, llmApiKeyId=llm_key["id"], preprompt="Du bist ein Tutor.", reasoningMode="always")
    return project, publish(client, project["id"])


def _ask(visitor, slug, fake_ai, *, stream: bool = False) -> dict:
    path = "messages/stream" if stream else "messages"
    assert visitor.post(f"/public/{slug}/{path}", json={"message": "Hi"}).status_code == 200
    return fake_ai.completion_calls[-1]["extra_body"]


def test_turns_over_the_visitors_budget_are_answered_without_reasoning(anon, always_project, fake_ai, monkeypatch) -> None:
    _, slug = always_project
    monkeypatch.setattr(settings, "deep_max_per_visitor", 2)
    anon.get(f"/public/{slug}")

    assert [_ask(anon, slug, fake_ai) for _ in range(4)] == [REASONING_ON, REASONING_ON, REASONING_OFF, REASONING_OFF]


def test_a_fresh_visitor_cookie_does_not_reset_the_ip_budget(always_project, fake_ai, monkeypatch) -> None:
    from tests.conftest import new_client

    _, slug = always_project
    monkeypatch.setattr(settings, "deep_max_per_ip", 2)
    results = []
    for _ in range(4):
        visitor = new_client()  # no cookie: a new visitor id each time
        visitor.get(f"/public/{slug}")
        results.append(_ask(visitor, slug, fake_ai))

    assert results == [REASONING_ON, REASONING_ON, REASONING_OFF, REASONING_OFF]


def test_the_project_budget_holds_across_visitors(always_project, fake_ai, monkeypatch) -> None:
    from tests.conftest import new_client

    _, slug = always_project
    monkeypatch.setattr(settings, "deep_max_per_project_per_day", 1)
    first, second = new_client(), new_client()
    first.get(f"/public/{slug}")
    second.get(f"/public/{slug}")

    assert _ask(first, slug, fake_ai) == REASONING_ON
    assert _ask(second, slug, fake_ai) == REASONING_OFF


def test_streamed_turns_spend_the_same_budget(anon, always_project, fake_ai, monkeypatch) -> None:
    _, slug = always_project
    monkeypatch.setattr(settings, "deep_max_per_visitor", 1)
    anon.get(f"/public/{slug}")

    assert _ask(anon, slug, fake_ai, stream=True) == REASONING_ON
    assert _ask(anon, slug, fake_ai, stream=True) == REASONING_OFF


def test_projects_that_do_not_reason_spend_nothing(client, teacher, fake_ai, anon, monkeypatch) -> None:
    monkeypatch.setattr(settings, "deep_max_per_visitor", 1)
    llm_key = create_key(client, provider="openai_compatible", apiBase="http://vllm.local/v1", modelId="qwen3")
    project = create_project(client, llmApiKeyId=llm_key["id"], preprompt="Du bist ein Tutor.")
    slug = publish(client, project["id"])
    anon.get(f"/public/{slug}")
    for _ in range(3):
        anon.post(f"/public/{slug}/messages", json={"message": "Hi"})
    assert all("extra_body" not in call for call in fake_ai.completion_calls)

    # The budget is still whole once a reasoning project is asked.
    client.put(f"/projects/{project['id']}", json={"reasoningMode": "always"})
    assert _ask(anon, slug, fake_ai) == REASONING_ON


def test_the_owners_own_tests_are_not_limited(client, always_project, fake_ai, monkeypatch) -> None:
    project, _ = always_project
    monkeypatch.setattr(settings, "deep_max_per_visitor", 0)
    monkeypatch.setattr(settings, "deep_max_per_project_per_day", 0)
    for path in (f"/projects/{project['id']}/latency-test/messages",):
        for _ in range(2):
            assert client.post(path, json={"message": "Hi"}).status_code == 200
            assert fake_ai.completion_calls[-1]["extra_body"] == REASONING_ON
