"""The project's thinking switch (Project.thinking_mode): sent only to providers that honour it,
and only when the teacher chose on/off."""

import json

import httpx

from tests.conftest import create_key, create_project, publish

CHAT_TEMPLATE_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


def _chat_with(client, fake_ai, *, provider: str, thinking_mode: str | None, **key_fields) -> dict:
    llm_key = create_key(client, provider=provider, modelId="qwen3", **key_fields)
    fields = {"thinkingMode": thinking_mode} if thinking_mode else {}
    project = create_project(client, llmApiKeyId=llm_key["id"], preprompt="Du bist ein Tutor.", **fields)
    slug = publish(client, project["id"])
    response = client.post(f"/public/{slug}/messages", json={"message": "Hallo"})
    assert response.status_code == 200, response.text
    return fake_ai.completion_calls[-1]


def test_off_is_sent_to_an_openai_compatible_endpoint(client, teacher, fake_ai) -> None:
    sent = _chat_with(
        client, fake_ai, provider="openai_compatible", thinking_mode="off", apiBase="http://vllm.local/v1"
    )
    assert sent["extra_body"] == CHAT_TEMPLATE_OFF


def test_on_is_sent_as_true(client, teacher, fake_ai) -> None:
    sent = _chat_with(
        client, fake_ai, provider="openai_compatible", thinking_mode="on", apiBase="http://vllm.local/v1"
    )
    assert sent["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


def test_default_sends_nothing(client, teacher, fake_ai) -> None:
    sent = _chat_with(client, fake_ai, provider="openai_compatible", thinking_mode=None, apiBase="http://vllm.local/v1")
    assert "extra_body" not in sent


def test_providers_without_the_switch_never_see_it(client, teacher, fake_ai) -> None:
    # A native OpenAI endpoint would reject or misread chat_template_kwargs.
    project_fields = _chat_with(client, fake_ai, provider="openai", thinking_mode="off")
    assert "extra_body" not in project_fields


def test_invalid_mode_is_rejected(client, teacher) -> None:
    response = client.post("/projects", json={"title": "x", "thinkingMode": "maybe"})
    assert response.status_code == 422


def test_providers_list_marks_the_switchable_ones(client, teacher) -> None:
    specs = {spec["value"]: spec for spec in client.get("/providers").json()}
    assert specs["openai_compatible"]["thinkingSwitch"] is True
    assert specs["gwdg_arcana"]["thinkingSwitch"] is True
    assert specs["openai"]["thinkingSwitch"] is False


def test_arcana_body_carries_the_switch(monkeypatch) -> None:
    from app.features.ai.llm import arcana, complete
    from app.features.ai.llm.base import ChatRequest
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

    complete(key, ChatRequest("System", "Frage", thinking=False))
    assert seen[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    complete(key, ChatRequest("System", "Frage"))
    assert "chat_template_kwargs" not in seen[-1]
