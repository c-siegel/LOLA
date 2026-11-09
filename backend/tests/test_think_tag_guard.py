"""ThinkTagGuard (app/features/ai/llm/think_guard.py): a model that writes its reasoning into the
reply as <think>...</think> must not have it spoken, whichever provider path the reply takes."""

import json

import httpx
import pytest

from app.features.ai.llm.think_guard import ThinkTagGuard, strip_think_tags
from tests.conftest import create_key, create_project, parse_sse, publish


def _stream(guard: ThinkTagGuard, text: str, size: int) -> str:
    out = "".join(guard.feed(text[i : i + size]) for i in range(0, len(text), size))
    return out + guard.flush()


@pytest.mark.parametrize("size", [1, 2, 3, 7, 1000])
def test_block_is_cut_however_the_deltas_are_split(size) -> None:
    text = "<think>Der Schüler irrt sich.</think>\n\nNein, das stimmt nicht."
    assert _stream(ThinkTagGuard(), text, size) == "Nein, das stimmt nicht."


@pytest.mark.parametrize("size", [1, 4, 1000])
def test_reasoning_tag_and_case_are_handled(size) -> None:
    text = "Vorab. <REASONING>geheim</Reasoning>Antwort."
    assert _stream(ThinkTagGuard(), text, size) == "Vorab. Antwort."


def test_text_without_tags_is_unchanged() -> None:
    text = "Ist 3 < 5 und 5 > 3? Ja, <b> ist hier kein Tag-Block."
    assert _stream(ThinkTagGuard(), text, 1) == text


def test_unterminated_block_drops_the_rest() -> None:
    assert _stream(ThinkTagGuard(), "Antwort. <think>und dann hört es auf", 3) == "Antwort. "


def test_partial_tag_at_the_end_is_released_as_text() -> None:
    assert _stream(ThinkTagGuard(), "a <thi", 2) == "a <thi"


def test_tag_like_text_inside_a_closed_block_does_not_end_it_early() -> None:
    text = "<think>ich schreibe <think> und </thin und </think>Antwort"
    assert strip_think_tags(text) == "Antwort"
    assert _stream(ThinkTagGuard(), text, 1) == "Antwort"


def test_several_blocks_in_one_reply() -> None:
    assert strip_think_tags("<think>a</think>Eins. <think>b</think>Zwei.") == "Eins. Zwei."


def _key_and_project(client, **key_fields):
    llm_key = create_key(client, **key_fields)
    project = create_project(client, llmApiKeyId=llm_key["id"], preprompt="Du bist ein Tutor.")
    return publish(client, project["id"])


def test_litellm_reply_never_carries_thinking(client, teacher, fake_ai) -> None:
    fake_ai.llm_reply = "<think>Erst nachdenken.</think> Hallo Welt."
    slug = _key_and_project(client)

    plain = client.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()
    assert plain["reply"] == "Hallo Welt."

    events = parse_sse(client.post(f"/public/{slug}/messages/stream", json={"message": "Hi"}).text)
    spoken = " ".join(data["text"] for name, data in events if name == "chunk")
    assert "nachdenken" not in spoken and "think" not in spoken
    assert events[-1][1]["reply"] == "Hallo Welt."


def test_arcana_reply_never_carries_thinking(monkeypatch) -> None:
    from app.features.ai.llm import arcana, get_llm_client, stream
    from app.features.ai.llm.base import ChatRequest
    from app.features.api_keys.crypto import store_api_key
    from app.features.api_keys.models import UserApiKey

    reply = "<think>geheim</think>Antwort.\n---\nReferences:\n[RREF1] skript.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream"):
            lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in (reply[:5], reply[5:])]
            return httpx.Response(200, text="\n".join(lines + ["data: [DONE]"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    monkeypatch.setattr(arcana, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    key = UserApiKey(
        user_id="u", provider="gwdg_arcana", encrypted_api_key=store_api_key("secret"), masked_key="x",
        model_id="llama", arcana_id="kb-1",
    )
    request = ChatRequest("System", "Frage", temperature=None, top_p=None, start_prompt=None, history=None)

    assert get_llm_client(key).complete(request) == "Antwort."
    assert "".join(stream(key, request)) == "Antwort."
