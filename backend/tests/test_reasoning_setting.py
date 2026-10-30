"""The project's reasoning setting (Project.reasoning_mode / reasoning_effort): what each
provider path receives, and that unset really means "send nothing"."""

import json

import httpx
import litellm
import pytest

from app.features.ai.llm.base import ChatRequest
from app.features.ai.llm.reasoning import reasoning_kwargs, reasoning_support, sampling_allowed
from tests.conftest import create_key, create_project, publish

CHAT_TEMPLATE_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


def _chat_with(client, fake_ai, *, provider: str, model: str = "qwen3", project_fields: dict | None = None, **key_fields) -> dict:
    llm_key = create_key(client, provider=provider, modelId=model, **key_fields)
    project = create_project(client, llmApiKeyId=llm_key["id"], preprompt="Du bist ein Tutor.", **(project_fields or {}))
    slug = publish(client, project["id"])
    response = client.post(f"/public/{slug}/messages", json={"message": "Hallo"})
    assert response.status_code == 200, response.text
    return fake_ai.completion_calls[-1]


VLLM = {"provider": "openai_compatible", "apiBase": "http://vllm.local/v1"}


def test_off_is_sent_to_an_openai_compatible_endpoint(client, teacher, fake_ai) -> None:
    sent = _chat_with(client, fake_ai, project_fields={"reasoningMode": "off"}, **VLLM)
    assert sent["extra_body"] == CHAT_TEMPLATE_OFF


def test_always_is_sent_as_enable_thinking(client, teacher, fake_ai) -> None:
    sent = _chat_with(client, fake_ai, project_fields={"reasoningMode": "always"}, **VLLM)
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


def test_default_sends_nothing(client, teacher, fake_ai) -> None:
    sent = _chat_with(client, fake_ai, **VLLM)
    assert "extra_body" not in sent
    assert "reasoning_effort" not in sent


def test_a_model_that_cannot_reason_never_sees_the_setting(client, teacher, fake_ai) -> None:
    sent = _chat_with(
        client, fake_ai, provider="openai", model="gpt-4o", project_fields={"reasoningMode": "always", "reasoningEffort": "high"}
    )
    assert "reasoning_effort" not in sent and "extra_body" not in sent
    assert sent["temperature"] == 0.5


def test_effort_reaches_a_reasoning_model_and_sampling_is_dropped(client, teacher, fake_ai) -> None:
    sent = _chat_with(
        client, fake_ai, provider="openai", model="o3", project_fields={"reasoningMode": "always", "reasoningEffort": "high"}
    )
    assert sent["reasoning_effort"] == "high"
    assert "temperature" not in sent and "top_p" not in sent


def test_invalid_values_are_rejected(client, teacher) -> None:
    assert client.post("/projects", json={"title": "x", "reasoningMode": "maybe"}).status_code == 422
    assert client.post("/projects", json={"title": "x", "reasoningEffort": "extreme"}).status_code == 422


def test_the_old_thinking_mode_field_is_gone(client, teacher) -> None:
    project = create_project(client)
    assert "thinkingMode" not in project
    assert project["reasoningMode"] == "default" and project["reasoningEffort"] is None


@pytest.mark.parametrize(
    ("provider", "model", "support"),
    [
        ("openai", "o3", "effort"),
        ("openai", "gpt-4o", "none"),
        ("anthropic", "claude-sonnet-4-5", "effort"),
        ("gemini", "gemini-2.5-flash", "effort"),
        ("openai_compatible", "qwen3", "switch"),
        ("gwdg_arcana", "qwen3", "switch"),
        ("mistral", "mistral-large-latest", "none"),
    ],
)
def test_support_is_decided_per_model(provider, model, support) -> None:
    assert reasoning_support(provider, model) == support


def test_key_listing_reports_the_support(client, teacher) -> None:
    create_key(client, provider="openai", modelId="o3")
    create_key(client, provider="openai", modelId="gpt-4o")
    keys = {key["modelId"]: key["reasoningSupport"] for key in client.get("/api-keys").json()}
    assert keys == {"o3": "effort", "gpt-4o": "none"}


@pytest.mark.parametrize(
    ("provider", "model", "reasoning", "expected"),
    [
        ("openai", "gpt-5", "off", {"reasoning_effort": "minimal"}),
        ("openai", "o3", "off", {"reasoning_effort": "low"}),
        ("openai", "o3", "on", {}),
        ("anthropic", "claude-sonnet-4-5", "off", {}),
        ("anthropic", "claude-sonnet-4-5", "on", {"reasoning_effort": "medium"}),
        ("anthropic", "claude-sonnet-4-5", "high", {"reasoning_effort": "high"}),
        ("gemini", "gemini-2.5-flash", "off", {"reasoning_effort": "disable"}),
        ("openai", "o3", None, {}),
    ],
)
def test_litellm_mapping(provider, model, reasoning, expected) -> None:
    assert reasoning_kwargs(provider, model, reasoning) == expected


def test_sampling_is_only_dropped_when_a_level_is_sent_to_openai_or_anthropic() -> None:
    assert sampling_allowed("openai", {}) is True
    assert sampling_allowed("openai", {"reasoning_effort": "low"}) is False
    assert sampling_allowed("anthropic", {"reasoning_effort": "low"}) is False
    assert sampling_allowed("gemini", {"reasoning_effort": "low"}) is True


def test_a_rejected_reasoning_setting_is_retried_without_it(client, teacher, fake_ai, monkeypatch) -> None:
    llm_key = create_key(client, provider="openai", modelId="o3")
    project = create_project(client, llmApiKeyId=llm_key["id"], reasoningMode="always", reasoningEffort="low")
    slug = publish(client, project["id"])
    original = fake_ai.completion

    def reject_once(**kwargs):
        if "reasoning_effort" in kwargs:
            fake_ai.completion_calls.append(kwargs)
            raise litellm.BadRequestError("nope", model="o3", llm_provider="openai")
        return original(**kwargs)

    monkeypatch.setattr(litellm, "completion", reject_once)
    for path in ("messages", "messages/stream"):
        assert client.post(f"/public/{slug}/{path}", json={"message": "Hi"}).status_code == 200
    assert "reasoning_effort" not in fake_ai.completion_calls[-1]


def test_arcana_body_carries_the_switch(monkeypatch) -> None:
    from app.features.ai.llm import arcana, complete
    from app.features.api_keys.crypto import store_api_key
    from app.features.api_keys.models import UserApiKey

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(arcana, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    key = UserApiKey(
        user_id="u", provider="gwdg_arcana", encrypted_api_key=store_api_key("secret"), masked_key="x",
        model_id="qwen", arcana_id="kb-1",
    )

    complete(key, ChatRequest("System", "Frage", reasoning="off"))
    assert seen[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    complete(key, ChatRequest("System", "Frage"))
    assert "chat_template_kwargs" not in seen[-1]
