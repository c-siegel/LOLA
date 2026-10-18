"""
Site Page Request/Response Shapes

The shapes for the public page routes (public_router.py) and the admin editor (admin_router.py).

How to use:
    from app.features.pages.schemas import PublicPageOut
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.core.error_codes import ErrorCode
from app.core.schema import CamelModel

Locale = Literal["de", "en"]
PageKind = Literal["landing", "imprint", "privacy", "custom"]
RevisionAction = Literal["created", "content_saved", "content_reset", "settings_changed"]

# Generous for a legal text (a full privacy policy is ~30k characters), small enough that one
# request can't bloat the database.
MAX_CONTENT_CHARS = 100_000
MAX_TITLE_CHARS = 200
MAX_FIELD_CHARS = 2_000
MAX_SLUG_CHARS = 80
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _check_slug(value: str | None) -> str | None:
    if value is None:
        return value
    value = value.strip().lower()
    if not value or len(value) > MAX_SLUG_CHARS or not _SLUG_PATTERN.match(value):
        raise ValueError(ErrorCode.PAGE_SLUG_INVALID)
    return value


class PublicTranslationOut(CamelModel):
    title: str
    content_md: str
    fields: dict[str, str]
    updated_at: datetime


class PublicPageOut(CamelModel):
    """A page with every language it has text in; the frontend picks one (see service.py)."""

    slug: str
    kind: PageKind
    translations: dict[str, PublicTranslationOut]


class FooterPageOut(CamelModel):
    """A published custom page linked from the site footer, with its title per language."""

    slug: str
    titles: dict[str, str]


class AuthorOut(CamelModel):
    name: str
    email: str


class LastChangeOut(CamelModel):
    at: datetime
    # None when the account that made the change has since been deleted.
    author: AuthorOut | None


class AdminTranslationOut(PublicTranslationOut):
    locale: Locale


class AdminPageOut(CamelModel):
    id: str
    kind: PageKind
    slug: str
    is_published: bool
    show_in_footer: bool
    footer_order: int
    translations: list[AdminTranslationOut]
    last_change: LastChangeOut | None


class RevisionOut(CamelModel):
    id: str
    action: RevisionAction
    locale: Locale | None
    created_at: datetime
    author: AuthorOut | None


class PageCreate(CamelModel):
    slug: str
    is_published: bool = False
    show_in_footer: bool = True
    footer_order: int = Field(default=0, ge=0, le=1000)

    _slug = field_validator("slug")(_check_slug)


class PageUpdate(CamelModel):
    slug: str | None = None
    is_published: bool | None = None
    show_in_footer: bool | None = None
    footer_order: int | None = Field(default=None, ge=0, le=1000)

    _slug = field_validator("slug")(_check_slug)


class TranslationUpdate(CamelModel):
    """The text for one language. Everything empty means "back to the fallback"."""

    title: str = Field(default="", max_length=MAX_TITLE_CHARS)
    content_md: str = Field(default="", max_length=MAX_CONTENT_CHARS)
    fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def check_field_lengths(cls, value: dict[str, str]) -> dict[str, str]:
        # Which keys are allowed depends on the page kind, so that's checked in the service.
        if any(len(text) > MAX_FIELD_CHARS for text in value.values()):
            raise ValueError(ErrorCode.PAGE_FIELD_TOO_LONG)
        return value
