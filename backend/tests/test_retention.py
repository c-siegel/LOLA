"""Tests for tasks/retention.py: old messages must go even from a conversation still in use."""

import json
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.features.chat.models import Conversation
from app.features.site_settings.service import get_or_create_site_settings
from app.tasks.retention import purge_expired_data


def _entry(role: str, age_days: int) -> dict:
    sent = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {"role": role, "content": f"{role} {age_days}d", "timestamp": sent.isoformat()}


def test_purge_trims_old_messages_from_an_active_conversation(engine) -> None:
    with Session(engine) as session:
        get_or_create_site_settings(session).conversation_retention_days = 30
        session.add(get_or_create_site_settings(session))
        messages = [_entry("user", 60), _entry("assistant", 60), _entry("user", 1), _entry("assistant", 1)]
        session.add(Conversation(
            id="active", project_id="p", visitor_id="v", messages_json=json.dumps(messages),
            started_at=datetime.now(timezone.utc) - timedelta(days=60),
        ))
        session.add(Conversation(
            id="stale", project_id="p", visitor_id="w", messages_json=json.dumps([_entry("user", 60)]),
            started_at=datetime.now(timezone.utc) - timedelta(days=60),
            updated_at=datetime.now(timezone.utc) - timedelta(days=60),
        ))
        session.commit()

        purge_expired_data(session)

        active = session.get(Conversation, "active")
        assert [m["content"] for m in json.loads(active.messages_json)] == ["user 1d", "assistant 1d"]
        assert active.started_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) - timedelta(days=30)
        assert session.get(Conversation, "stale") is None
