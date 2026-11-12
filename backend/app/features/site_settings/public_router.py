"""
Public Site Settings Routes

The unauthenticated view of the instance-wide site settings — just the pieces public pages need
(contact email for the Impressum, whether self-registration is open for the Register page, where
the on-device speech models live for the student start page). See
admin_router.py for the admin-only read/write versions of the same data.
"""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.deps import get_session
from app.features.api_keys.resolve import deployment_browser_stt_model_url, deployment_browser_tts_model_url
from app.features.site_settings.schemas import PublicSettingsOut, PublicSiteSettingsOut
from app.features.site_settings.service import get_or_create_site_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/public", response_model=PublicSettingsOut)
def get_public_settings(session: Session = Depends(get_session)):
    """The public-facing site settings: imprint details, whether self-registration is open, and
    where the on-device speech models live."""
    # Deliberately built from PublicSiteSettingsOut, not SiteSettingsOut — operational settings
    # like the retention period are for admins only and must not leak through this
    # unauthenticated route.
    public = PublicSiteSettingsOut.model_validate(get_or_create_site_settings(session), from_attributes=True)
    return PublicSettingsOut(
        **public.model_dump(),
        browser_stt_model_url=deployment_browser_stt_model_url(),
        browser_tts_model_url=deployment_browser_tts_model_url(),
    )
