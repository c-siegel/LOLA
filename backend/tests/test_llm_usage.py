"""LLMUsage (app/features/ai/llm/base.py): token counts read from whatever shape a provider uses."""

from types import SimpleNamespace

from app.features.ai.llm.base import LLMUsage


def test_reads_an_openai_style_object() -> None:
    usage = LLMUsage()
    usage.update_from(
        SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=64),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
        )
    )
    assert usage.to_client() == {
        "promptTokens": 100,
        "completionTokens": 20,
        "cachedTokens": 64,
        "reasoningTokens": 12,
    }


def test_reads_a_json_dict_and_tolerates_missing_details() -> None:
    usage = LLMUsage()
    usage.update_from({"prompt_tokens": 7, "completion_tokens": 3, "prompt_tokens_details": None})
    assert usage.prompt_tokens == 7
    assert usage.completion_tokens == 3
    assert usage.cached_tokens is None
    assert usage.reasoning_tokens is None


def test_ignores_non_numeric_values_and_none() -> None:
    usage = LLMUsage()
    usage.update_from(None)
    usage.update_from({"prompt_tokens": "many", "completion_tokens": True})
    assert usage == LLMUsage()


def test_arcana_reports_usage_when_the_gateway_sends_it(monkeypatch) -> None:
    import json

    import httpx

    from app.features.ai.llm import arcana, get_llm_client
    from app.features.ai.llm.base import ChatRequest
    from app.features.api_keys.crypto import store_api_key
    from app.features.api_keys.models import UserApiKey

    sent_usage = {"prompt_tokens": 50, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 32}}

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream"):
            events = [{"choices": [{"delta": {"content": "Hallo."}}]}, {"choices": [], "usage": sent_usage}]
            lines = [f"data: {json.dumps(e)}" for e in events]
            return httpx.Response(200, text="\n".join(lines + ["data: [DONE]"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hallo."}}], "usage": sent_usage})

    monkeypatch.setattr(arcana, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    key = UserApiKey(
        user_id="u", provider="gwdg_arcana", encrypted_api_key=store_api_key("secret"), masked_key="x",
        model_id="llama", arcana_id="kb-1",
    )
    request = ChatRequest("System", "Frage")
    client = get_llm_client(key)

    for call in (client.complete, lambda r, u: "".join(client.stream(r, u))):
        usage = LLMUsage()
        assert call(request, usage) == "Hallo."
        assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens) == (50, 5, 32)
