"""Adaptive reasoning end to end: the rules pick the route per turn, the second key answers deep
turns, and the budget and the project's keys are respected."""

import pytest

from app.core.config import settings
from tests.conftest import create_key, create_project, parse_sse, publish

pytestmark = pytest.mark.usefixtures("adaptive_available")

THINKING = {"chat_template_kwargs": {"enable_thinking": True}}
NOT_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
ARGUMENT = "Ich behaupte, dass das falsch ist, weil die Zahl negativ wird"
SMALL_TALK = "Wie geht es dir heute so"
VLLM = {"provider": "openai_compatible", "apiBase": "http://vllm.local/v1"}


def _ask(client, slug, message, fake_ai, path="messages") -> dict:
    assert client.post(f"/public/{slug}/{path}", json={"message": message}).status_code == 200
    return fake_ai.completion_calls[-1]


@pytest.fixture
def two_models(client, teacher, fake_ai):
    fast = create_key(client, modelId="small", **VLLM)
    deep = create_key(client, modelId="big", **VLLM)
    project = create_project(
        client, llmApiKeyId=fast["id"], deepLlmApiKeyId=deep["id"], reasoningMode="adaptive", preprompt="Du bist ein Tutor."
    )
    return project, publish(client, project["id"]), fast, deep


def test_arguments_go_to_the_second_model_with_reasoning(client, two_models, fake_ai) -> None:
    _, slug, _, _ = two_models

    sent = _ask(client, slug, ARGUMENT, fake_ai)
    assert sent["model"].endswith("big") and sent["extra_body"] == THINKING

    sent = _ask(client, slug, SMALL_TALK, fake_ai)
    assert sent["model"].endswith("small") and sent["extra_body"] == NOT_THINKING


def test_streamed_turns_are_routed_the_same_way(client, two_models, fake_ai) -> None:
    _, slug, _, _ = two_models
    assert _ask(client, slug, ARGUMENT, fake_ai, "messages/stream")["model"].endswith("big")
    assert _ask(client, slug, SMALL_TALK, fake_ai, "messages/stream")["model"].endswith("small")


def test_without_a_second_key_the_same_model_reasons_only_for_arguments(client, teacher, fake_ai) -> None:
    key = create_key(client, modelId="qwen3", **VLLM)
    project = create_project(client, llmApiKeyId=key["id"], reasoningMode="adaptive")
    slug = publish(client, project["id"])

    assert _ask(client, slug, ARGUMENT, fake_ai)["extra_body"] == THINKING
    assert _ask(client, slug, SMALL_TALK, fake_ai)["extra_body"] == NOT_THINKING


def test_the_second_model_alone_is_enough_when_the_first_cannot_reason(client, teacher, fake_ai) -> None:
    fast = create_key(client, modelId="gpt-4o-mini")
    deep = create_key(client, modelId="gpt-4o")
    project = create_project(client, llmApiKeyId=fast["id"], deepLlmApiKeyId=deep["id"], reasoningMode="adaptive")
    slug = publish(client, project["id"])

    sent = _ask(client, slug, ARGUMENT, fake_ai)
    assert sent["model"].endswith("gpt-4o") and "reasoning_effort" not in sent
    assert _ask(client, slug, SMALL_TALK, fake_ai)["model"].endswith("gpt-4o-mini")


def test_a_language_without_rules_never_escalates(client, teacher, fake_ai) -> None:
    key = create_key(client, modelId="qwen3", **VLLM)
    project = create_project(client, llmApiKeyId=key["id"], reasoningMode="adaptive", spokenLanguage="fr")
    slug = publish(client, project["id"])
    assert _ask(client, slug, ARGUMENT, fake_ai)["extra_body"] == NOT_THINKING


def test_only_escalated_turns_spend_the_budget(client, two_models, fake_ai, monkeypatch) -> None:
    _, slug, _, _ = two_models
    monkeypatch.setattr(settings, "deep_max_per_visitor", 1)
    client.get(f"/public/{slug}")

    for _ in range(3):
        assert _ask(client, slug, SMALL_TALK, fake_ai)["model"].endswith("small")
    assert _ask(client, slug, ARGUMENT, fake_ai)["model"].endswith("big")
    assert _ask(client, slug, ARGUMENT, fake_ai)["model"].endswith("small")  # budget used up


def test_a_failing_second_model_falls_back_to_the_first(client, two_models, fake_ai, monkeypatch) -> None:
    import litellm

    original = fake_ai.completion

    def completion(**kwargs):
        if kwargs["model"].endswith("big"):
            fake_ai.completion_calls.append(kwargs)
            raise RuntimeError("deep model down")
        return original(**kwargs)

    monkeypatch.setattr(litellm, "completion", completion)
    _, slug, _, _ = two_models

    assert client.post(f"/public/{slug}/messages/stream", json={"message": ARGUMENT}).status_code == 200
    assert fake_ai.completion_calls[-1]["model"].endswith("small")


def test_the_second_key_must_be_an_llm_key_of_the_owner(client, teacher) -> None:
    tts_key = create_key(client, key_type="tts")
    response = client.post("/projects", json={"title": "x", "deepLlmApiKeyId": tts_key["id"]})
    assert response.status_code == 400
    assert client.post("/projects", json={"title": "x", "deepLlmApiKeyId": "not-a-key"}).status_code == 400


def test_the_second_model_is_stored_next_to_the_first_and_follows_key_changes(client, two_models) -> None:
    project, _, _, deep = two_models
    body = client.get(f"/projects/{project['id']}").json()
    assert body["deepLlmModel"].endswith("big") and body["deepLlmApiKeyId"] == deep["id"]

    client.put(f"/api-keys/{deep['id']}", json={"provider": "openai_compatible", "keyType": "llm", "apiBase": VLLM["apiBase"], "modelId": "bigger"})
    assert client.get(f"/projects/{project['id']}").json()["deepLlmModel"].endswith("bigger")

    assert client.delete(f"/api-keys/{deep['id']}").status_code in (200, 204)
    body = client.get(f"/projects/{project['id']}").json()
    assert body["deepLlmApiKeyId"] is None and body["deepLlmModel"] is None


def test_the_second_key_can_be_removed_again(client, two_models) -> None:
    project, *_ = two_models
    body = client.put(f"/projects/{project['id']}", json={"deepLlmApiKeyId": None}).json()
    assert body["deepLlmApiKeyId"] is None and body["deepLlmModel"] is None


def test_the_second_key_is_not_part_of_an_export(client, two_models) -> None:
    project, *_ = two_models
    client.put(f"/projects/{project['id']}", json={"bridgeEnabled": False, "reasoningEffort": "high"})
    exported = client.get(f"/projects/{project['id']}/export").text
    assert "deep_llm" not in exported and "api_key" not in exported

    imported = client.post("/projects/import", files={"file": ("p.yml", exported.encode(), "application/yaml")}).json()
    assert imported["deepLlmApiKeyId"] is None
    assert imported["reasoningMode"] == "adaptive" and imported["bridgeEnabled"] is False
    assert imported["reasoningEffort"] == "high"


def test_the_latency_test_can_force_either_route(client, two_models, fake_ai) -> None:
    project, *_ = two_models
    path = f"/projects/{project['id']}/latency-test/messages"

    deep = parse_sse(client.post(path, json={"message": SMALL_TALK, "route": "deep"}).text)[-1][1]
    assert deep["route"] == "deep" and fake_ai.completion_calls[-1]["model"].endswith("big")

    fast = parse_sse(client.post(path, json={"message": ARGUMENT, "route": "fast"}).text)[-1][1]
    assert fast["route"] == "fast" and fake_ai.completion_calls[-1]["model"].endswith("small")

    auto = parse_sse(client.post(path, json={"message": ARGUMENT}).text)[-1][1]
    assert auto["route"] == "deep"


def test_routes_and_models_are_saved_per_reply_and_exported(client, two_models, fake_ai, anon) -> None:
    project, slug, _, _ = two_models
    client.put(f"/projects/{project['id']}", json={"saveConversations": True})
    anon.get(f"/public/{slug}")
    anon.post(f"/public/{slug}/messages", json={"message": SMALL_TALK})
    anon.post(f"/public/{slug}/messages", json={"message": ARGUMENT})

    session_id = client.get("/conversations").json()["items"][0]["id"]
    detail = client.get(f"/conversations/{session_id}").json()
    replies = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert [(m["route"], m["model"].rsplit("/", 1)[-1]) for m in replies] == [("fast", "small"), ("deep", "big")]
    assert all(m["route"] is None for m in detail["messages"] if m["role"] == "user")

    csv_text = client.post("/conversations/export", json={"conversationIds": [session_id]}).text
    assert "Antwortweg" in csv_text and "deep (openai/big)" in csv_text

    stats = client.get("/analytics/stats").json()
    assert (stats["fastReplies"], stats["deepReplies"]) == (1, 1)


def test_projects_without_reasoning_store_no_route(client, chat_project, anon, fake_ai) -> None:
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    anon.post(f"/public/{slug}/messages", json={"message": "Hi"})
    session_id = client.get("/conversations").json()["items"][0]["id"]
    messages = client.get(f"/conversations/{session_id}").json()["messages"]
    assert all(m.get("route") is None and m.get("model") is None for m in messages)
    assert "Antwortweg" not in client.post("/conversations/export", json={"conversationIds": [session_id]}).text
