"""
Ownership checks for a teacher's own resources (projects, keys, voice clips, knowledge bases, …).

The one place that decides whether a row belongs to a user, so every route checks it the same
way. Someone else's row looks exactly like a missing one: 404 rather than 403, so IDs can't be
probed (IDOR enumeration).

How to use:
    from app.core.ownership import find_owned, get_owned

    kb = get_owned(session, KnowledgeBase, kb_id, user.id, ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
    clip = find_owned(session, VoiceClip, clip_id, user.id)  # None instead of raising
"""

from typing import TypeVar

from sqlmodel import Session

from app.core.errors import DomainError

T = TypeVar("T")


def find_owned(session: Session, model: type[T], item_id: str, user_id: str) -> T | None:
    """The `model` row `item_id` if it exists and its `user_id` is `user_id`, else None."""
    item = session.get(model, item_id)
    if item is None or getattr(item, "user_id") != user_id:
        return None
    return item


def get_owned(session: Session, model: type[T], item_id: str, user_id: str, error_code: str) -> T:
    """Like find_owned, but raises a 404 with `error_code` instead of returning None."""
    item = find_owned(session, model, item_id, user_id)
    if item is None:
        raise DomainError(error_code, status_code=404)
    return item
