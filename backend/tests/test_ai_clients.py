"""Tests for the provider dispatch in app/features/ai: each factory must hand back the right
integration for a stored key's provider, so callers never branch on the provider themselves."""

from app.features.ai.llm import get_llm_client
from app.features.ai.llm.arcana import ArcanaClient
from app.features.ai.llm.litellm_provider import LiteLLMClient
from app.features.ai.stt import get_stt_client
from app.features.ai.stt.parakeet_local import LocalParakeetClient
from app.features.ai.stt.saia import SaiaClient
from app.features.ai.tts import get_tts_client
from app.features.ai.tts.cartesia import CartesiaClient
from app.features.ai.tts.google import GoogleCloudTTSClient
from app.features.ai.tts.litellm_provider import LiteLLMSpeechClient
from app.features.api_keys.models import UserApiKey


def _key(provider: str) -> UserApiKey:
    return UserApiKey(user_id="u", provider=provider, encrypted_api_key="x", masked_key="x")


def test_llm_dispatch() -> None:
    assert isinstance(get_llm_client(_key("gwdg_arcana")), ArcanaClient)
    for provider in ("openai", "anthropic", "ollama", "openai_compatible", "gwdg_saia"):
        assert isinstance(get_llm_client(_key(provider)), LiteLLMClient)


def test_tts_dispatch() -> None:
    assert isinstance(get_tts_client(_key("cartesia")), CartesiaClient)
    assert isinstance(get_tts_client(_key("google_cloud_tts")), GoogleCloudTTSClient)
    for provider in ("openai", "gemini", "openai_compatible"):
        assert isinstance(get_tts_client(_key(provider)), LiteLLMSpeechClient)


def test_stt_dispatch() -> None:
    assert isinstance(get_stt_client(_key("gwdg_saia")), SaiaClient)
    assert isinstance(get_stt_client(None), LocalParakeetClient)
    assert isinstance(get_stt_client(_key("openai")), LocalParakeetClient)


def test_arcana_request_shape_and_reference_stripping(monkeypatch) -> None:
    import json

    import httpx

    from app.features.ai.llm import arcana, stream
    from app.features.ai.llm.base import ChatRequest
    from app.features.api_keys.crypto import store_api_key

    seen: list[httpx.Request] = []
    reply = "Antwort.\n---\nReferences:\n[RREF1] skript.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in ("Ant", "wort.", reply[8:])]
            return httpx.Response(200, text="\n".join(lines + ["data: [DONE]"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    monkeypatch.setattr(arcana, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    key = UserApiKey(
        user_id="u", provider="gwdg_arcana", encrypted_api_key=store_api_key("secret"), masked_key="x",
        model_id="llama", arcana_id="kb-1",
    )
    request = ChatRequest("System", "Frage", temperature=0.3, top_p=None, start_prompt="Hallo", history=None)

    assert get_llm_client(key).complete(request) == "Antwort."
    sent = seen[-1]
    assert str(sent.url) == "https://chat-ai.academiccloud.de/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer secret"
    assert sent.headers["inference-service"] == "saia-openai-gateway"
    assert json.loads(sent.content) == {
        "model": "llama",
        "messages": [
            {"role": "system", "content": "System"},
            {"role": "assistant", "content": "Hallo"},
            {"role": "user", "content": "Frage"},
        ],
        "enable-tools": True,
        "arcana": {"id": "kb-1"},
        "temperature": 0.3,
    }

    assert "".join(stream(key, request)) == "Antwort."
    assert json.loads(seen[-1].content)["stream"] is True
