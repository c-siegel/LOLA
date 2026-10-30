"""A reasoning call that fails or times out must not cost the visitor the answer: the turn is
repeated once without reasoning (chat/pipeline.py), and the SSE stream stays alive while a model
is silent (chat/streaming.py::sse_stream)."""

import time

import litellm
import pytest

from tests.conftest import LLM_REPLY, create_key, create_project, parse_sse, publish

THINKING = {"chat_template_kwargs": {"enable_thinking": True}}


@pytest.fixture
def slug(client, teacher, fake_ai, monkeypatch):
    llm_key = create_key(client, provider="openai_compatible", apiBase="http://vllm.local/v1", modelId="qwen3")
    project = create_project(client, llmApiKeyId=llm_key["id"], preprompt="Du bist ein Tutor.", reasoningMode="always")
    return publish(client, project["id"])


def _fail_while_thinking(fake_ai, monkeypatch, error: Exception) -> None:
    original = fake_ai.completion

    def completion(**kwargs):
        if kwargs.get("extra_body") == THINKING:
            fake_ai.completion_calls.append(kwargs)
            raise error
        return original(**kwargs)

    monkeypatch.setattr(litellm, "completion", completion)


@pytest.mark.parametrize("path", ["messages", "messages/stream"])
def test_a_failed_reasoning_call_is_answered_without_reasoning(client, slug, fake_ai, monkeypatch, path) -> None:
    _fail_while_thinking(fake_ai, monkeypatch, RuntimeError("model crashed"))

    response = client.post(f"/public/{slug}/{path}", json={"message": "Hi"})

    assert response.status_code == 200
    if path == "messages":
        assert response.json()["reply"] == LLM_REPLY
    else:
        assert parse_sse(response.text)[-1][1]["reply"] == LLM_REPLY
    first, second = fake_ai.completion_calls[-2:]
    assert first["extra_body"] == THINKING and second["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_reasoning_calls_carry_a_timeout_and_plain_ones_do_not(client, slug, fake_ai) -> None:
    client.post(f"/public/{slug}/messages", json={"message": "Hi"})
    assert fake_ai.completion_calls[-1]["timeout"] > 0


def test_a_timed_out_reasoning_call_is_not_retried_before_falling_back(client, slug, fake_ai, monkeypatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _: None)
    _fail_while_thinking(fake_ai, monkeypatch, litellm.Timeout("slow", model="qwen3", llm_provider="openai"))

    assert client.post(f"/public/{slug}/messages/stream", json={"message": "Hi"}).status_code == 200

    thinking_calls = [call for call in fake_ai.completion_calls if call.get("extra_body") == THINKING]
    assert len(thinking_calls) == 1


def test_a_failure_without_reasoning_still_reports_an_error(client, slug, fake_ai) -> None:
    fake_ai.llm_error = RuntimeError("provider down")

    events = parse_sse(client.post(f"/public/{slug}/messages/stream", json={"message": "Hi"}).text)

    assert events[-1][0] == "error"


def test_a_project_without_reasoning_sends_no_timeout(client, teacher, fake_ai) -> None:
    llm_key = create_key(client)
    project = create_project(client, llmApiKeyId=llm_key["id"], preprompt="Du bist ein Tutor.")
    slug = publish(client, project["id"])
    client.post(f"/public/{slug}/messages", json={"message": "Hi"})
    assert "timeout" not in fake_ai.completion_calls[-1]


def test_keepalive_frames_fill_the_silence_and_events_still_arrive() -> None:
    from app.features.chat.streaming import sse_stream

    def slow_events():
        time.sleep(0.25)
        yield "chunk", {"index": 0}
        yield "done", {}

    frames = list(sse_stream(slow_events(), keepalive_seconds=0.05))

    assert frames[0] == ": keep-alive\n\n"
    assert frames[-2:] == ['event: chunk\ndata: {"index": 0}\n\n', "event: done\ndata: {}\n\n"]
    assert sum(frame.startswith(":") for frame in frames) >= 2


def test_the_producer_stops_when_the_client_goes_away() -> None:
    from app.features.chat.streaming import sse_stream

    produced = []
    closed = []

    def endless():
        try:
            for index in range(1000):
                produced.append(index)
                time.sleep(0.01)
                yield "chunk", {"index": index}
        finally:
            closed.append(True)

    frames = sse_stream(endless(), keepalive_seconds=1)
    next(frames)
    frames.close()
    time.sleep(0.2)

    assert closed and len(produced) < 100


def test_an_error_inside_the_events_reaches_the_caller() -> None:
    from app.features.chat.streaming import sse_stream

    def broken():
        yield "chunk", {}
        raise RuntimeError("boom")

    frames = sse_stream(broken())
    next(frames)
    with pytest.raises(RuntimeError, match="boom"):
        next(frames)


def test_the_latency_test_shows_deep_and_the_fallback_to_fast(client, slug, fake_ai, monkeypatch) -> None:
    project_id = client.get("/projects").json()[0]["id"]
    path = f"/projects/{project_id}/latency-test/messages"

    assert parse_sse(client.post(path, json={"message": "Hi"}).text)[-1][1]["route"] == "deep"

    _fail_while_thinking(fake_ai, monkeypatch, RuntimeError("model crashed"))
    for streaming in (True, False):
        assert parse_sse(client.post(path, json={"message": "Hi", "streaming": streaming}).text)[-1][1]["route"] == "fast"


def test_a_repeat_after_a_failed_stream_answers_fast_and_spends_nothing(client, slug, fake_ai, monkeypatch) -> None:
    from app.core import rate_limit

    spent = []
    monkeypatch.setattr(rate_limit, "_hit_all", lambda checks: spent.append(checks) or True)

    response = client.post(f"/public/{slug}/messages", json={"message": "Hi", "retryAfterFailure": True})

    assert response.status_code == 200
    assert fake_ai.completion_calls[-1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert not [key for checks in spent for key, _, _ in checks if key.startswith("deep-")]
    assert "timeout" not in fake_ai.completion_calls[-1]
