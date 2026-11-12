"""
Site Settings Request/Response Shapes

The request/response shapes for the admin-editable, DB-backed instance settings (see
app/features/site_settings/admin_router.py and public_router.py): the imprint details, whether self-registration
is open, and how long student chat data is kept.

How to use:
    from app.features.site_settings.schemas import SiteSettingsOut
"""

from pydantic import Field, field_validator

from app.core.schema import CamelModel


class PublicSiteSettingsOut(CamelModel):
    """The parts of the site settings that public pages (Impressum, Register) may read."""

    # Imprint details are public by definition — §5 DDG requires them to be shown to everyone.
    contact_email: str | None
    contact_phone: str | None
    provider_name: str | None
    provider_street: str | None
    provider_city: str | None
    provider_country: str | None
    registration_enabled: bool


class PublicSettingsOut(PublicSiteSettingsOut):
    """GET /settings/public: the public site settings plus where the on-device speech models live,
    so the student start page can download them before a chat is chosen. Not part of
    SiteSettingsOut — these come from the deployment's config, not from the admin-editable row."""

    # None when the deployment doesn't offer the model (see features/api_keys/resolve.py).
    browser_stt_model_url: str | None
    browser_tts_model_url: str | None


class SiteSettingsOut(PublicSiteSettingsOut):
    """Everything an admin sees — the public fields plus operational settings."""

    conversation_retention_days: int
    # Knowledge-base (RAG) limits, see SiteSettings and features/knowledge/limits.py.
    rag_max_upload_mb: int
    rag_max_pages: int
    rag_max_chars_per_document: int
    rag_max_documents_per_kb: int
    rag_max_kb_per_user: int
    rag_user_quota_mb: int
    rag_upload_rate_per_10min: int
    rag_eval_max_cases_per_run: int


class KnowledgeCeilingsOut(CamelModel):
    """The operator's ceilings for the admin-editable knowledge limits (GET /admin/settings/knowledge-ceilings)."""

    enabled: bool
    max_upload_mb: int
    max_pages: int
    max_chars: int


class SiteSettingsUpdate(CamelModel):
    contact_email: str | None = None
    contact_phone: str | None = None
    provider_name: str | None = None
    provider_street: str | None = None
    provider_city: str | None = None
    provider_country: str | None = None
    registration_enabled: bool | None = None
    conversation_retention_days: int | None = None
    # Each at least 1 (a limit of 0 would silently disable uploads); upper bounds for the first
    # three come from the knowledge service, checked in admin_router.py.
    rag_max_upload_mb: int | None = Field(default=None, ge=1)
    rag_max_pages: int | None = Field(default=None, ge=1)
    rag_max_chars_per_document: int | None = Field(default=None, ge=1000)
    rag_max_documents_per_kb: int | None = Field(default=None, ge=1, le=10_000)
    rag_max_kb_per_user: int | None = Field(default=None, ge=1, le=1000)
    rag_user_quota_mb: int | None = Field(default=None, ge=1, le=1_000_000)
    rag_upload_rate_per_10min: int | None = Field(default=None, ge=1, le=10_000)
    rag_eval_max_cases_per_run: int | None = Field(default=None, ge=1, le=500)

    @field_validator("conversation_retention_days")
    @classmethod
    def validate_retention(cls, value: int | None) -> int | None:
        # Negative would silently mean "cutoff in the future", i.e. delete everything — reject it
        # rather than let a typo wipe the saved conversations.
        if value is not None and value < 0:
            raise ValueError("conversation_retention_days must be 0 or greater")
        return value
