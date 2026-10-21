"""
Authentication Routes

Handles account registration, login, logout, and the password-reset flow (the current-user
check is GET /me, see app/features/users/profile_router.py). Every route here is rate-limited (see app/core/rate_limit.py) to protect against
brute-force login attempts, credential stuffing, and mass account creation.

How does login work here?
On success, login/register set an httponly cookie (see app/core/cookies.py) containing a signed
JWT (JSON Web Token) — the browser sends it automatically on later requests, and JavaScript
can't read it. There's no bearer token in request headers to manage on the frontend.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlmodel import Session

from app.core.cookies import clear_auth_cookie, set_auth_cookie
from app.core.deps import get_session
from app.core.error_codes import ErrorCode
from app.core.rate_limit import (
    enforce_login_rate_limit,
    enforce_password_reset_rate_limit,
    enforce_register_rate_limit,
)
from app.features.auth.password_reset import request_password_reset, reset_password
from app.features.auth.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    RegistrationStatusOut,
    ResetPasswordRequest,
    UserOut,
)
from app.features.auth.service import authenticate_user, register_user, user_to_out
from app.features.site_settings.service import get_or_create_site_settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/registration-status", response_model=RegistrationStatusOut)
def registration_status(session: Session = Depends(get_session)):
    """Whether self-registration is currently enabled."""
    # Public (no login needed) — the registration page checks this before rendering the form,
    # see pages/Register. The actual enforcement happens below, in register(). DB-backed (see
    # features/site_settings/service.py) so an admin can toggle it without a redeploy.
    return RegistrationStatusOut(enabled=get_or_create_site_settings(session).registration_enabled)


@router.post("/register", response_model=UserOut)
def register(data: RegisterRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    """Create a new account and log the user in, unless registration is disabled."""
    if not get_or_create_site_settings(session).registration_enabled:
        # Checked before the rate limit — no reason to spend that budget when registration is
        # switched off entirely anyway (at launch: internal use only).
        raise HTTPException(status_code=403, detail=ErrorCode.REGISTRATION_DISABLED)
    enforce_register_rate_limit(request)
    user = register_user(session, data.name, data.email, data.password)
    set_auth_cookie(response, user)
    return user_to_out(user)


@router.post("/login", response_model=UserOut)
def login(data: LoginRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    """Authenticate with email + password and set the auth cookie."""
    enforce_login_rate_limit(request, data.email)
    user = authenticate_user(session, data.email, data.password)
    if user is None:
        raise HTTPException(status_code=401, detail=ErrorCode.INVALID_CREDENTIALS)
    set_auth_cookie(response, user)
    return user_to_out(user)


@router.post("/logout")
def logout(response: Response):
    """Clear the auth cookie."""
    clear_auth_cookie(response)
    return None


@router.post("/forgot-password")
def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Start a password reset for the given email, if an account with it exists."""
    enforce_password_reset_rate_limit(request, data.email)
    request_password_reset(session, data.email, background_tasks)
    # Always the same response, whether or not the email exists or SMTP is configured — this
    # prevents enumerating which accounts are registered (see features/auth/password_reset.py).
    return {"detail": "Falls ein Konto mit dieser E-Mail existiert, wurde eine Nachricht verschickt."}


@router.post("/reset-password")
def reset_password_route(data: ResetPasswordRequest, session: Session = Depends(get_session)):
    """Complete a password reset using the token from the reset email."""
    user = reset_password(session, data.token, data.new_password)
    if user is None:
        raise HTTPException(status_code=400, detail=ErrorCode.RESET_LINK_INVALID)
    return {"detail": "Passwort wurde zurückgesetzt."}
