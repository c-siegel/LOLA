"""Adaptive reasoning is offered only where the knowledge service runs EmbeddingGemma 2
(chat/reasoning_router.py::ADAPTIVE_EMBEDDING_MODELS); elsewhere an adaptive project reasons on
every turn, like "always"."""

import httpx
import pytest

from app.features.chat import reasoning_router
from app.features.knowledge import rag_client
from tests import fake_rag
from tests.conftest import create_key, create_project, publish

THINKING = {"chat_template_kwargs": {"enable_thinking": True}}
NOT_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
SMALL_TALK = "Wie geht es dir heute so"
VLLM = {"provider": "openai_compatible", "apiBase": "http://vllm.local/v1", "modelId": "qwen3"}


def _ask(client, slug, fake_ai) -> dict:
    assert client.post(f"/public/{slug}/messages", json={"message": SMALL_TALK}).status_code == 200
    return fake_ai.completion_calls[-1]["extra_body"]


def test_without_the_knowledge_service_adaptive_is_refused(client, teacher) -> None:
    response = client.post("/projects", json={"title": "x", "reasoningMode": "adaptive"})
    assert response.status_code == 400
    assert response.json() == {"detail": "ADAPTIVE_REASONING_UNAVAILABLE"}

    project = create_project(client)
    assert client.put(f"/projects/{project['id']}", json={"reasoningMode": "adaptive"}).status_code == 400
    assert client.put(f"/projects/{project['id']}", json={"reasoningMode": "always"}).status_code == 200


def test_another_embedding_model_does_not_offer_it(client, teacher, adaptive_available, monkeypatch) -> None:
    monkeypatch.setattr(fake_rag, "LOCAL_MODEL", "jinaai/jina-embeddings-v2-base-de")
    assert client.post("/projects", json={"title": "x", "reasoningMode": "adaptive"}).status_code == 400
    assert client.get("/providers/rag-status").json()["adaptiveReasoning"] is False


def test_with_embeddinggemma_it_is_offered_and_decides_per_turn(client, teacher, fake_ai, adaptive_available) -> None:
    assert client.get("/providers/rag-status").json()["adaptiveReasoning"] is True
    key = create_key(client, **VLLM)
    project = create_project(client, llmApiKeyId=key["id"], reasoningMode="adaptive")
    slug = publish(client, project["id"])

    assert _ask(client, slug, fake_ai) == NOT_THINKING  # small talk stays fast


def test_an_adaptive_project_reasons_on_every_turn_once_the_service_is_gone(
    client, teacher, fake_ai, adaptive_available, monkeypatch
) -> None:
    key = create_key(client, **VLLM)
    project = create_project(client, llmApiKeyId=key["id"], reasoningMode="adaptive")
    slug = publish(client, project["id"])

    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(rag_client._client, "request", unreachable)
    monkeypatch.setattr(rag_client, "_capabilities_cache", None)

    assert _ask(client, slug, fake_ai) == THINKING  # like "always"
    # Saving it unchanged still works; the configurator explains the fallback.
    assert client.put(f"/projects/{project['id']}", json={"reasoningMode": "adaptive", "title": "y"}).status_code == 200


def test_a_stopped_service_is_asked_once_a_minute_not_on_every_turn(adaptive_available, monkeypatch) -> None:
    calls = []

    def unreachable(*args, **kwargs):
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(rag_client._client, "request", unreachable)
    monkeypatch.setattr(rag_client, "_capabilities_cache", None)

    assert [reasoning_router.adaptive_reasoning_available() for _ in range(5)] == [False] * 5
    assert len(calls) == 1


@pytest.mark.parametrize("forced", ["fast", "deep"])
def test_the_latency_lab_can_still_force_a_route_without_the_service(client, teacher, fake_ai, forced) -> None:
    from tests.conftest import parse_sse

    key = create_key(client, **VLLM)
    project = create_project(client, llmApiKeyId=key["id"])
    done = parse_sse(
        client.post(f"/projects/{project['id']}/latency-test/messages", json={"message": SMALL_TALK, "route": forced}).text
    )[-1][1]
    assert done["route"] == forced
