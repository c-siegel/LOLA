"""
Password Reset Flow

Requesting and completing a password reset: issues a single-use, expiring token (only its hash
is stored) and emails a reset link, then verifies that token and sets the new password.

How to use:
    from app.features.auth.password_reset import request_password_reset, reset_password

    request_password_reset(session, email, background_tasks)
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks
from sqlalchemy import update
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import hash_password
from app.features.auth.email import send_password_reset_email
from app.features.auth.models import PasswordResetToken
from app.features.users.models import User
from app.features.users.service import find_user_by_email

logger = logging.getLogger(__name__)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _invalidate_unused_tokens(session: Session, user_id: str, now: datetime) -> None:
    """Mark all of the user's still-unused reset tokens as used (not committed)."""
    # Only the newest reset link should ever work: an older one may sit in a mailbox someone
    # else can read, and the user asking again is a sign they don't trust (or can't find) it.
    session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )


def _send_reset_email_safely(to_email: str, reset_link: str) -> None:
    """Send the reset email, logging (not raising) a failure; runs after the response is sent."""
    try:
        send_password_reset_email(to_email, reset_link)
    except Exception:
        # Not logged with the address or link: both are personal data / a live credential.
        logger.exception("Sending the password-reset email failed")


def request_password_reset(session: Session, email: str, background_tasks: BackgroundTasks) -> None:
    """Issue a password-reset token and queue the reset email, if an account with `email` exists."""
    # Always responds the same way to the caller (see features/auth/router.py), whether or not the email
    # exists — this prevents using it to enumerate registered accounts.
    user = find_user_by_email(session, email)
    if user is None or not user.enabled:
        return

    now = datetime.now(timezone.utc)
    _invalidate_unused_tokens(session, user.id, now)
    raw_token = secrets.token_urlsafe(32)
    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=now + timedelta(minutes=settings.password_reset_token_expire_minutes),
    )
    session.add(reset_token)
    session.commit()

    if not settings.smtp_configured:
        # Local development without a mail server: the token still exists in the DB as normal
        # (e.g. for manual testing), it just isn't emailed, instead of failing with a connection
        # error.
        return

    reset_link = f"{settings.frontend_base_url}/reset-password?token={raw_token}"
    # Sent after the response: SMTP latency or errors would otherwise make a known email
    # distinguishable from an unknown one (slower, or a 500).
    background_tasks.add_task(_send_reset_email_safely, user.email, reset_link)


def reset_password(session: Session, raw_token: str, new_password: str) -> User | None:
    """Complete a password reset: verify the token and set the new password."""
    token_hash = _hash_token(raw_token)
    reset_token = session.exec(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).first()

    now = datetime.now(timezone.utc)
    if (
        reset_token is None
        or reset_token.used_at is not None
        or reset_token.expires_at.replace(tzinfo=timezone.utc) < now
    ):
        return None

    user = session.get(User, reset_token.user_id)
    if user is None:
        return None

    user.password_hash = hash_password(new_password)
    # The user just chose this password themselves, which is all an admin-forced change asks for.
    user.must_change_password = False
    # Also invalidates all existing sessions — a reset password suggests a compromised account,
    # so old tokens shouldn't just keep working (see token_version).
    user.token_version += 1
    _invalidate_unused_tokens(session, user.id, now)
    session.add(user)
    session.commit()
    return user
