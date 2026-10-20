"""
Bootstrap Admin Account

An idempotent startup step that creates or promotes an admin account from ADMIN_EMAIL/
ADMIN_PASSWORD (see core/config.py) — the way a self-hoster gets their first admin without
touching the database by hand. Run automatically right after migrations by
docker/backend-entrypoint.sh and scripts/dev.py; for any other deploy, run it manually the same way.

What does it do?
- Neither ADMIN_EMAIL nor ADMIN_PASSWORD set: does nothing (most instances, most of the time).
- ADMIN_EMAIL doesn't match an existing account: creates one with that email/password, already
  an admin, flagged to change that password on first login.
- ADMIN_EMAIL matches an existing non-admin account: promotes it to admin. Never touches its
  password — only a brand-new account gets one from ADMIN_PASSWORD.
- ADMIN_EMAIL already belongs to an admin: does nothing.

Safe to run on every container start (and to leave the env vars set indefinitely) — none of the
above ever re-applies a change that's already in place.

How to use:
    python -m app.cli.bootstrap_admin
"""

import logging

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import Session

from app.core.config import settings
from app.db.session import engine
from app.features.auth.schemas import _validate_password_strength
from app.features.auth.service import register_user
from app.features.users.service import find_user_by_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_email_adapter = TypeAdapter(EmailStr)


def bootstrap_admin() -> None:
    """Create-or-promote the ADMIN_EMAIL/ADMIN_PASSWORD account to admin, if both are set."""
    if not settings.admin_email or not settings.admin_password:
        logger.info("ADMIN_EMAIL/ADMIN_PASSWORD not set — skipping admin bootstrap.")
        return

    # register_user() is a bare service function with no validation of its own — the normal
    # /auth/register route only ever reaches it after RegisterRequest's Pydantic validators
    # (email format, password strength) already passed. Called directly from here with raw env
    # var strings, those checks would otherwise never run at all — e.g. ADMIN_PASSWORD=1 would
    # silently create a fully privileged admin with a one-character password. A failure here is
    # logged and skipped, not raised: this runs from the container entrypoint under `set -e`, and
    # a bad bootstrap value shouldn't take the whole instance down for every other user.
    try:
        _email_adapter.validate_python(settings.admin_email)
        _validate_password_strength(settings.admin_password)
    except (ValidationError, ValueError) as exc:
        logger.error(
            "ADMIN_EMAIL/ADMIN_PASSWORD failed validation (%s) — skipping admin bootstrap. "
            "Fix the values in .env and restart to try again.",
            exc,
        )
        return

    # Same normalization as every email the API accepts (see features/auth/schemas.py::Email).
    admin_email = settings.admin_email.lower()
    with Session(engine) as session:
        user = find_user_by_email(session, admin_email)
        if user is None:
            user = register_user(session, name="Admin", email=admin_email, password=settings.admin_password)
            user.is_admin = True
            user.must_change_password = True
            session.add(user)
            session.commit()
            logger.info("Created new admin account for %s.", settings.admin_email)
        elif not user.is_admin:
            user.is_admin = True
            session.add(user)
            session.commit()
            logger.info("Promoted existing account %s to admin.", settings.admin_email)
        else:
            logger.info("%s is already an admin — nothing to do.", settings.admin_email)


if __name__ == "__main__":
    bootstrap_admin()
