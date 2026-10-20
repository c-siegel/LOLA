"""
API Key Routes

Lets a user store, edit, test, and delete their own LLM/TTS/STT provider API keys — the "bring
your own key" (BYOK) feature. Keys are encrypted at rest (see crypto.py) and validated against
the provider registry on create/update (see schemas.py); the logic lives in service.py. The
registry itself is served by providers_router.py.
"""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.deps import get_current_user, get_session
from app.core.error_codes import ErrorCode
from app.core.ownership import get_owned
from app.features.api_keys import service
from app.features.api_keys.models import UserApiKey
from app.features.api_keys.schemas import ApiKeyCreate, ApiKeyOut, ApiKeyTestResult, ApiKeyUpdate
from app.features.users.models import User

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _to_out(key: UserApiKey, used_by_projects: int = 0) -> ApiKeyOut:
    # masked_key comes straight from the DB (computed from the plaintext when the key was
    # created), not recomputed from the ciphertext on every list.
    return ApiKeyOut(
        id=key.id,
        provider=key.provider,
        key_type=key.key_type,
        label=key.label,
        masked_key=key.masked_key,
        status=key.status,
        added_at=key.added_at,
        api_base=key.api_base,
        model_id=key.model_id,
        arcana_id=key.arcana_id,
        used_by_projects=used_by_projects,
    )


@router.get("", response_model=list[ApiKeyOut])
def list_keys(current_user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    """List the current user's stored API keys, including how many projects use each one."""
    return [_to_out(key, used) for key, used in service.list_keys_with_usage(session, current_user.id)]


@router.post("", response_model=ApiKeyOut, status_code=201)
def create_key(
    data: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Store a new API key for the current user."""
    return _to_out(service.create_key(session, current_user.id, data))


@router.put("/{key_id}", response_model=ApiKeyOut)
def update_key(
    key_id: str,
    data: ApiKeyUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Update a stored API key; re-syncs any projects that use it as their LLM source."""
    key = get_owned(session, UserApiKey, key_id, current_user.id, ErrorCode.API_KEY_NOT_FOUND)
    return _to_out(service.update_key(session, key, data))


@router.delete("/{key_id}")
def delete_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Delete a stored API key; projects using it fall back to "no key configured"."""
    service.delete_key(session, get_owned(session, UserApiKey, key_id, current_user.id, ErrorCode.API_KEY_NOT_FOUND))
    return None


@router.post("/{key_id}/test", response_model=ApiKeyTestResult)
def test_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Try the stored key against its provider and record whether it works."""
    # Tests the already-stored key (the frontend doesn't send the plaintext here, see
    # frontend/src/api/apiKeys.ts::test) — not a freshly-submitted one.
    key = get_owned(session, UserApiKey, key_id, current_user.id, ErrorCode.API_KEY_NOT_FOUND)
    message = service.run_key_test(session, key)
    return ApiKeyTestResult(status=key.status, message=message)
