"""Tests for the conversation plan (app/features/chat/checklist.py): the steps a teacher sets up,
the "::done_N::" / "::undone_N::" markers the avatar ticks them off with, the progress replayed
from the visitor's history into the system prompt, and what students and teachers get to see."""

from conftest import create_project, parse_sse

from app.features.chat.checklist import Checklist, ChecklistItem, checklist_prompt, replay
from app.features.chat.motion import ChecklistMark, MotionMark, extract_markers
from app.features.projects.export import parse_project_yaml

STEPS = [
    {"title": "Begrüßung"},
    {"title": "Fotosynthese erklären", "note": "Chlorophyll muss vorkommen"},
    {"title": "Quiz", "note": "Antwort: Licht, Wasser, CO2"},
]


# --- Configuration ---


def test_checklist_defaults_off_and_steps_get_stable_ids(client, teacher) -> None:
    project = create_project(client)
    assert project["checklistEnabled"] is False
    assert project["checklistVisible"] is False
    assert project["checklistOrdered"] is True
    assert project["checklistItems"] == []

    updated = client.put(f"/projects/{project['id']}", json={"checklistEnabled": True, "checklistItems": STEPS}).json()
    ids = [item["id"] for item in updated["checklistItems"]]
    assert len(set(ids)) == 3 and all(ids)
    assert updated["checklistItems"][0] == {"id": ids[0], "title": "Begrüßung", "note": None}

    # Reordering keeps each step's ID, and an existing ID is never replaced.
    reordered = [updated["checklistItems"][2], updated["checklistItems"][0]]
    again = client.put(f"/projects/{project['id']}", json={"checklistItems": reordered}).json()
    assert [item["id"] for item in again["checklistItems"]] == [ids[2], ids[0]]


def test_invalid_checklists_are_rejected(client, teacher) -> None:
    project = create_project(client)
    url = f"/projects/{project['id']}"
    assert client.put(url, json={"checklistItems": [{"title": "  "}]}).status_code == 422
    duplicate = [{"id": "abcdef12", "title": "A"}, {"id": "abcdef12", "title": "B"}]
    assert client.put(url, json={"checklistItems": duplicate}).status_code == 422
    assert client.put(url, json={"checklistItems": [{"id": "../x", "title": "A"}]}).status_code == 422
    assert client.put(url, json={"checklistItems": [{"title": str(i)} for i in range(21)]}).status_code == 422


def test_checklist_survives_export_and_import(client, teacher) -> None:
    project = create_project(client, checklistEnabled=True, checklistOrdered=False, checklistItems=STEPS)
    with_ids = project["checklistItems"]

    exported = client.get(f"/projects/{project['id']}/export")
    assert exported.status_code == 200, exported.text
    data = parse_project_yaml(exported.text)

    assert data.checklist_enabled is True and data.checklist_ordered is False
    assert [item.model_dump() for item in data.checklist_items] == with_ids
    # Exports written before the plan existed import without one.
    assert parse_project_yaml("lola_export: 2\nproject:\n  title: x\n").checklist_items == []



# --- Parsing ---


def test_extract_markers_returns_ticks_with_positions() -> None:
    text, motions, checks = extract_markers("Super! ::done_1:: Jetzt ::nod_yes:: weiter. ::undone_2::")
    assert text == "Super! Jetzt weiter."
    assert motions == [MotionMark("nod_yes", 13)]
    assert checks == [ChecklistMark(1, True, 7), ChecklistMark(2, False, len(text))]


def test_tick_markers_tolerate_spacing_and_case() -> None:
    for marker in (":: done_3 ::", "::DONE_3::", "::done 3::", "::done-3::"):
        assert extract_markers(f"Gut {marker} gemacht") == ("Gut gemacht", [], [ChecklistMark(3, True, 4)])


def test_replay_applies_ticks_in_order() -> None:
    history = [
        [{"id": "aaaaaa", "done": True}],
        [],
        [{"id": "bbbbbb", "done": True}, {"id": "aaaaaa", "done": False}],
    ]
    assert replay(history) == {"bbbbbb"}
    assert replay([]) == set()


# --- Prompt ---

PLAN = Checklist(
    (
        ChecklistItem("aaaaaa", "Begrüßung"),
        ChecklistItem("bbbbbb", "Fotosynthese erklären", "Chlorophyll\nmuss vorkommen"),
        ChecklistItem("cccccc", "Quiz", "Antwort: Licht"),
    )
)


def test_prompt_lists_steps_and_status_in_order() -> None:
    prompt = checklist_prompt(PLAN, set())
    assert "in this order" in prompt
    assert "2. Fotosynthese erklären (note: Chlorophyll muss vorkommen)" in prompt
    assert prompt.endswith("Status: done none · next 1")


def test_done_steps_are_ticked_and_lose_their_note() -> None:
    prompt = checklist_prompt(PLAN, {"bbbbbb"})
    assert "2. ✓ Fotosynthese erklären\n" in prompt
    assert "Chlorophyll" not in prompt
    assert prompt.endswith("Status: done 2 · next 1")


def test_any_order_status_lists_open_steps_and_all_done() -> None:
    any_order = Checklist(PLAN.items, ordered=False)
    assert "any order" in checklist_prompt(any_order, set())
    assert checklist_prompt(any_order, {"aaaaaa"}).endswith("Status: done 1 · open 2, 3")
    assert checklist_prompt(any_order, {"aaaaaa", "bbbbbb", "cccccc"}).endswith("Status: all steps done.")


# --- Chat routes ---


def _plan_project(client, chat_project, **settings) -> list[str]:
    """Turn the plan on for `chat_project`; returns the steps' IDs."""
    body = {"checklistEnabled": True, "checklistItems": STEPS, **settings}
    updated = client.put(f"/projects/{chat_project['id']}", json=body).json()
    return [item["id"] for item in updated["checklistItems"]]


def _system_prompt(fake_ai) -> str:
    return fake_ai.completion_calls[-1]["messages"][0]["content"]


def test_plain_reply_returns_ticks_and_hides_markers(client, anon, chat_project, fake_ai) -> None:
    ids = _plan_project(client, chat_project)
    fake_ai.llm_reply = "Hallo! ::done_1:: Was weißt du schon? ::done_9::"
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    body = anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()

    assert body["reply"] == "Hallo! Was weißt du schon?"
    # Step 9 doesn't exist: cut out, no tick.
    assert body["checks"] == [{"id": ids[0], "done": True}]
    assert fake_ai.speech_calls[-1]["input"] == "Hallo! Was weißt du schon?"
    assert _system_prompt(fake_ai).endswith("Status: done none · next 1")


def test_progress_from_history_reaches_the_system_prompt(client, anon, chat_project, fake_ai) -> None:
    ids = _plan_project(client, chat_project)
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    history = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hallo!", "checks": [{"id": ids[0], "done": True}]},
        {"role": "user", "content": "Erklär mal"},
        {"role": "assistant", "content": "Also...", "checks": [{"id": ids[1], "done": True}]},
        {"role": "user", "content": "Hä?"},
        # Taken back: the student didn't understand after all. A deleted step's ID is ignored.
        {"role": "assistant", "content": "Nochmal.", "checks": [{"id": ids[1], "done": False}, {"id": "gone1234", "done": True}]},
    ]

    anon.post(f"/public/{slug}/messages", json={"message": "Ok", "history": history})

    system = _system_prompt(fake_ai)
    assert "1. ✓ Begrüßung" in system
    assert "2. Fotosynthese erklären (note: Chlorophyll muss vorkommen)" in system
    assert system.endswith("Status: done 1 · next 2")
    # The ticks stay out of what the model sees as the conversation.
    assert all(set(m) == {"role", "content"} for m in fake_ai.completion_calls[-1]["messages"])


def test_stream_sends_ticks_per_chunk_and_in_done(client, anon, chat_project, fake_ai) -> None:
    ids = _plan_project(client, chat_project, checklistOrdered=False)
    fake_ai.llm_reply = "Schön, dass du da bist! ::done_1:: Weißt du, was Fotosynthese ist? ::done_2::"
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    events = parse_sse(anon.post(f"/public/{slug}/messages/stream", json={"message": "Hi"}).text)

    chunks = [data for name, data in events if name == "chunk"]
    assert [c["checks"] for c in chunks] == [[{"id": ids[0], "done": True}], [{"id": ids[1], "done": True}]]
    assert events[-1][0] == "done"
    assert events[-1][1]["checks"] == [{"id": ids[0], "done": True}, {"id": ids[1], "done": True}]
    assert "::" not in events[-1][1]["reply"]
    assert "any order" in _system_prompt(fake_ai)


def test_plan_also_works_without_avatar_and_off_means_no_ticks(client, anon, chat_project, fake_ai) -> None:
    ids = _plan_project(client, chat_project, chatLayout="chat_only")
    fake_ai.llm_reply = "Hallo! ::done_1::"
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    assert anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()["checks"] == [{"id": ids[0], "done": True}]
    assert "::done_N::" in _system_prompt(fake_ai)

    client.put(f"/projects/{chat_project['id']}", json={"checklistEnabled": False})
    body = anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()
    assert body == {**body, "reply": "Hallo!", "checks": []}
    assert "::done_N::" not in _system_prompt(fake_ai)


def test_preview_replays_history_and_returns_ticks(client, chat_project, fake_ai) -> None:
    ids = _plan_project(client, chat_project)
    fake_ai.llm_reply = "Prima. ::done_2::"
    history = [{"role": "assistant", "content": "Hallo!", "checks": [{"id": ids[0], "done": True}]}]

    body = client.post(f"/projects/{chat_project['id']}/chat/messages", json={"message": "Ok", "history": history}).json()

    assert body["checks"] == [{"id": ids[1], "done": True}]
    assert _system_prompt(fake_ai).endswith("Status: done 1 · next 2")


def test_public_page_shows_the_plan_only_when_visible_and_never_the_notes(client, anon, chat_project) -> None:
    ids = _plan_project(client, chat_project)
    slug = chat_project["shareSlug"]
    assert anon.get(f"/public/{slug}").json()["checklist"] is None

    client.put(f"/projects/{chat_project['id']}", json={"checklistVisible": True})
    page = anon.get(f"/public/{slug}")
    assert page.json()["checklist"] == {
        "ordered": True,
        "items": [{"id": ids[0], "title": "Begrüßung"}, {"id": ids[1], "title": "Fotosynthese erklären"}, {"id": ids[2], "title": "Quiz"}],
    }
    assert "Chlorophyll" not in page.text and "Antwort" not in page.text


def test_history_ticks_are_validated(anon, chat_project) -> None:
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    url = f"/public/{slug}/messages"
    bad_id = [{"role": "assistant", "content": "x", "checks": [{"id": "<script>", "done": True}]}]
    assert anon.post(url, json={"message": "Hi", "history": bad_id}).status_code == 422
    padded = [{"role": "assistant", "content": "x", "checks": [{"id": "aaaaaa", "done": True}] * 21}]
    assert anon.post(url, json={"message": "Hi", "history": padded}).status_code == 422


def test_system_prompt_route_includes_the_plan(client, chat_project) -> None:
    _plan_project(client, chat_project)
    body = client.get(f"/projects/{chat_project['id']}/system-prompt").json()
    assert body["checklistPrompt"].endswith("Status: done none · next 1")
    assert body["full"].endswith(body["checklistPrompt"])


# --- Saved conversations ---


def test_ticks_are_saved_and_shown_in_the_transcript_and_csv(client, anon, chat_project, fake_ai) -> None:
    ids = _plan_project(client, chat_project)
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")
    fake_ai.llm_reply = "Hallo! ::done_1:: Los geht's. ::done_2::"
    anon.post(f"/public/{slug}/messages/stream", json={"message": "Hi"})
    fake_ai.llm_reply = "Hm, ::undone_2:: das schauen wir uns nochmal an."
    anon.post(f"/public/{slug}/messages", json={"message": "Hä?"})
    # The teacher deletes step 1 afterwards: the transcript keeps the tick, without a title.
    client.put(f"/projects/{chat_project['id']}", json={"checklistItems": [{"id": ids[1], "title": "Fotosynthese erklären"}]})

    conversation_id = client.get("/conversations/ids").json()[0]
    messages = client.get(f"/conversations/{conversation_id}").json()["messages"]
    assert messages[1]["content"] == "Hallo! Los geht's."
    assert messages[1]["checks"] == [
        {"id": ids[0], "done": True, "offset": 7, "title": None, "number": None},
        {"id": ids[1], "done": True, "offset": len("Hallo! Los geht's."), "title": "Fotosynthese erklären", "number": 1},
    ]
    assert messages[3]["checks"] == [{"id": ids[1], "done": False, "offset": 4, "title": "Fotosynthese erklären", "number": 1}]
    assert messages[0]["checks"] is None

    csv = client.post("/conversations/export", json={"conversationIds": [conversation_id]}).text
    assert "Zeitpunkt,Avatar,Schüler:in,Plan" in csv
    assert "✓ (gelöschter Schritt); ✓ Fotosynthese erklären" in csv
    assert "✗ Fotosynthese erklären" in csv


def test_a_reply_never_returns_more_ticks_than_a_history_entry_accepts(client, anon, chat_project, fake_ai) -> None:
    # A model stuck in a loop can tick the same step over and over; echoed back unchanged, a list
    # longer than ChatHistoryEntry allows would make every later request of that chat fail (422).
    ids = _plan_project(client, chat_project)
    fake_ai.llm_reply = " ".join(["Ja. ::done_1:: ::undone_1::"] * 15) + " ::done_2:: Gut."
    slug = chat_project["shareSlug"]
    anon.get(f"/public/{slug}")

    body = anon.post(f"/public/{slug}/messages", json={"message": "Hi"}).json()

    # Only the last tick per step counts, so the list is at most one entry per step.
    assert body["checks"] == [{"id": ids[0], "done": False}, {"id": ids[1], "done": True}]
    history = [{"role": "assistant", "content": body["reply"], "checks": body["checks"]}]
    assert anon.post(f"/public/{slug}/messages", json={"message": "Weiter", "history": history}).status_code == 200
