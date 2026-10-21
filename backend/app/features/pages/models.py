"""
Site Page Tables

Admin-editable public content: the landing page's texts, the imprint (Impressum), the privacy
policy (Datenschutzerklärung) and any number of extra pages such as an accessibility statement
(Barrierefreiheitserklärung).

- SitePage: one row per page. The three built-in pages (kind "landing", "imprint", "privacy")
  have a fixed slug and always exist publicly — without any text of their own they show the
  built-in template from the frontend's translation files. Pages of kind "custom" are created by
  an admin and only reachable once published.
- SitePageTranslation: the admin's text for one page in one language. A missing row means "use
  the fallback" (see service.py::resolve order: the visitor's language, then English, then the
  built-in template).
- SitePageRevision: an append-only log of who changed which page when, with a snapshot of the
  text saved at that point.

How to use:
    from app.features.pages.models import SitePage, SitePageRevision, SitePageTranslation
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SitePage(SQLModel, table=True):
    """One public page, built-in or created by an admin."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    # "landing" | "imprint" | "privacy" | "custom" — see service.py::BUILTIN_SLUGS.
    kind: str
    # The URL segment: built-in pages keep theirs (their routes are fixed in the frontend),
    # custom pages are served at /p/<slug>.
    slug: str = Field(unique=True, index=True)
    # Only meaningful for custom pages; the built-in ones are public by definition (the imprint
    # and privacy policy are legally required to be reachable).
    is_published: bool = False
    show_in_footer: bool = True
    footer_order: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SitePageTranslation(SQLModel, table=True):
    """An admin's text for one page in one language."""

    __table_args__ = (UniqueConstraint("page_id", "locale", name="uq_sitepagetranslation_page_locale"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    page_id: str = Field(foreign_key="sitepage.id", index=True)
    # "de" | "en", the same codes as the frontend's i18n.
    locale: str
    # The heading and footer label. Optional on the imprint/privacy page (empty keeps the
    # built-in heading), ignored on the landing page.
    title: str = ""
    # Markdown, rendered without raw HTML. Replaces the whole template on the imprint/privacy
    # page; on the landing page it's an extra section below the feature cards.
    content_md: str = ""
    # The landing page's fixed-layout texts (headline, hero text, feature cards) as a JSON
    # object {field: text}; "{}" for every other page. See service.py::LANDING_FIELDS.
    fields_json: str = "{}"
    updated_at: datetime = Field(default_factory=_now)


class SitePageRevision(SQLModel, table=True):
    """One entry of a page's change history."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    page_id: str = Field(foreign_key="sitepage.id", index=True)
    # "created" | "content_saved" | "content_reset" | "settings_changed"
    action: str
    # The language the change was made in; None for page-level changes (slug, footer, published).
    locale: str | None = None
    # Snapshot of the translation as saved, so the history keeps what was published when, even
    # after it has been edited again. Empty for a reset or a settings change.
    title: str = ""
    content_md: str = ""
    fields_json: str = "{}"
    # Cleared when that account is deleted (users/account.py) — the entry then shows a deleted
    # account rather than keeping the person's details around.
    author_id: str | None = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=_now, index=True)
