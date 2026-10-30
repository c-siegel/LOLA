"""
Saving Chat Turns

Appends each visitor message and the tutor's reply to the visitor's saved conversation for a
project (only for projects with save_conversations enabled, see pipeline.py).
"""

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.db.session import engine
from app.features.chat.models import Conversation


def save_turn(
    project_id: str,
    visitor_id: str,
    user_message: str,
    reply: str,
    user_timestamp: datetime,
    reply_timestamp: datetime,
    visitor_name: str | None = None,
    sources: list[dict] | None = None,
    motions: list[dict] | None = None,
    checks: list[dict] | None = None,
    route: str | None = None,
    model: str | None = None,
) -> None:
    """Append one exchange to the visitor's saved conversation for this project, creating it on
    the first message.

    Opens its own session instead of using the request-scoped one: the caller has already closed
    that one before the slow LLM/TTS calls (so a class-sized burst of chats doesn't exhaust the
    connection pool), and for a streamed reply this runs after the route has already returned,
    when the request-scoped session's teardown relative to the streamed body is fragile.

    user_timestamp/reply_timestamp are passed in (rather than read with datetime.now() here)
    because this runs after both the LLM call and any TTS synthesis — by then "now" would no
    longer be when the visitor actually sent the message or when the reply was actually ready,
    which matters for the per-message timestamps in the analytics CSV export.

    `sources` are the knowledge-base passages the reply was given (IDs and page numbers only, see
    features/knowledge/retrieval.py::sources_for_transcript); None for projects without one.

    `motions` are the avatar gestures the LLM marked in the reply, as {"name", "offset"} with the
    offset into `reply` (see features/chat/motion.py); None when it used none.

    `checks` are the conversation-plan steps the reply ticked off or took back, as {"id", "done",
    "offset"} (see features/chat/checklist.py); None when it ticked none.

    `route` ("fast" or "deep", see features/chat/reasoning_router.py) and `model` (the litellm model
    string that answered) are stored for projects that use reasoning, so a teacher can see which
    replies came from the reasoning route; None for projects that don't. Metadata only, no text.
    """
    with Session(engine) as session:
        existing = session.exec(
            select(Conversation).where(Conversation.project_id == project_id).where(Conversation.visitor_id == visitor_id)
        ).first()

        user_entry = {"role": "user", "content": user_message, "timestamp": user_timestamp.isoformat()}
        assistant_entry = {"role": "assistant", "content": reply, "timestamp": reply_timestamp.isoformat()}
        if sources is not None:
            assistant_entry["sources"] = sources
        if motions:
            assistant_entry["motions"] = motions
        if checks:
            assistant_entry["checks"] = checks
        if route:
            assistant_entry["route"] = route
        if model:
            assistant_entry["model"] = model

        if existing:
            messages = json.loads(existing.messages_json)
            messages.append(user_entry)
            messages.append(assistant_entry)
            existing.messages_json = json.dumps(messages)
            existing.updated_at = datetime.now(timezone.utc)
            # Only overwrites if a name actually arrived on this turn — an already-stored name must
            # survive a stray request that (for whatever reason) didn't carry the header.
            if visitor_name:
                existing.visitor_name = visitor_name
        else:
            messages = [user_entry, assistant_entry]
            conversation = Conversation(
                project_id=project_id, visitor_id=visitor_id, messages_json=json.dumps(messages), visitor_name=visitor_name
            )
            session.add(conversation)

        session.commit()
