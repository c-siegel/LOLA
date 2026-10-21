"""
Site Page Logic

Everything about the admin-editable public pages (see models.py): creating the built-in rows on
first use, saving and resetting a language's text, and recording every change in the history.

Which text a visitor sees is decided in the frontend, which gets every language at once so a
language switch needs no new request: the visitor's language, then English, then the built-in
template (or, for a custom page without one, whichever language exists). The landing page falls
back field by field, so an admin can change just the headline.

How to use:
    from app.features.pages import service

    page = service.get_page(session, page_id)
    service.save_translation(session, admin, page, "de", data)
"""

import json
from datetime import datetime, timezone

from sqlalchemy import delete, update
from sqlmodel import Session, select

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.features.pages.models import SitePage, SitePageRevision, SitePageTranslation
from app.features.pages.schemas import PageCreate, TranslationUpdate
from app.features.users.models import User

# The built-in pages and their fixed slugs, in the order the admin list shows them. The slugs
# match the frontend routes ("/impressum", "/datenschutz"; the landing page is "/").
BUILTIN_SLUGS: dict[str, str] = {"landing": "landing", "imprint": "impressum", "privacy": "datenschutz"}

# The landing page's editable texts; each one left empty keeps the built-in text. A feature
# card's title replaces both its long and its short (mobile) variant.
LANDING_FIELDS = (
    "badge",
    "headingFull",
    "headingShort",
    "heroText",
    "configureTitle",
    "configureText",
    "publishTitle",
    "publishText",
    "flexibilityTitle",
    "flexibilityText",
)

# Enough to see who has been editing a page; older entries are kept but not listed.
HISTORY_LIMIT = 200


class PageNotFound(DomainError):
    status_code = 404
    detail = ErrorCode.PAGE_NOT_FOUND


class SlugTaken(DomainError):
    status_code = 409
    detail = ErrorCode.PAGE_SLUG_TAKEN


class BuiltinProtected(DomainError):
    status_code = 400
    detail = ErrorCode.PAGE_BUILTIN_PROTECTED


class TitleRequired(DomainError):
    status_code = 400
    detail = ErrorCode.PAGE_TITLE_REQUIRED


class FieldsNotAllowed(DomainError):
    status_code = 400
    detail = ErrorCode.PAGE_FIELDS_NOT_ALLOWED


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fields_of(row: SitePageTranslation | SitePageRevision) -> dict[str, str]:
    try:
        value = json.loads(row.fields_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def ensure_builtin_pages(session: Session) -> None:
    """Create the rows of the built-in pages that don't exist yet."""
    existing = set(session.exec(select(SitePage.kind).where(SitePage.kind.in_(BUILTIN_SLUGS))))
    missing = [kind for kind in BUILTIN_SLUGS if kind not in existing]
    for kind in missing:
        session.add(SitePage(kind=kind, slug=BUILTIN_SLUGS[kind], is_published=True, show_in_footer=False))
    if missing:
        session.commit()


def list_pages(session: Session) -> list[SitePage]:
    """Every page: the built-in ones first (fixed order), then the custom ones by footer order."""
    ensure_builtin_pages(session)
    pages = list(session.exec(select(SitePage)))
    builtin_order = list(BUILTIN_SLUGS)

    def sort_key(page: SitePage) -> tuple:
        if page.kind in BUILTIN_SLUGS:
            return (0, builtin_order.index(page.kind), 0, "")
        return (1, 0, page.footer_order, page.slug)

    return sorted(pages, key=sort_key)


def get_page(session: Session, page_id: str) -> SitePage:
    page = session.get(SitePage, page_id)
    if page is None:
        raise PageNotFound()
    return page


def translations_of(session: Session, page_id: str) -> list[SitePageTranslation]:
    rows = session.exec(select(SitePageTranslation).where(SitePageTranslation.page_id == page_id))
    return sorted(rows, key=lambda row: row.locale)


def get_public_page(session: Session, slug: str) -> tuple[str, list[SitePageTranslation]]:
    """The kind and texts of the page at `slug`, as far as the public may see it.

    Read-only on purpose: an unauthenticated visitor must not be able to create rows, so a
    built-in page that has never been edited is answered without one (no texts = the template).
    """
    page = session.exec(select(SitePage).where(SitePage.slug == slug)).first()
    if page is None:
        for kind, builtin_slug in BUILTIN_SLUGS.items():
            if builtin_slug == slug:
                return kind, []
        raise PageNotFound()
    translations = translations_of(session, page.id)
    # A custom page has no template to fall back on, so without any text it isn't a page yet.
    if page.kind == "custom" and (not page.is_published or not translations):
        raise PageNotFound()
    return page.kind, translations


def footer_pages(session: Session) -> list[tuple[SitePage, list[SitePageTranslation]]]:
    """The published custom pages the footer links to, in footer order."""
    pages = session.exec(
        select(SitePage)
        .where(SitePage.kind == "custom", SitePage.is_published, SitePage.show_in_footer)
        .order_by(SitePage.footer_order, SitePage.slug)
    )
    result = []
    for page in pages:
        translations = translations_of(session, page.id)
        if translations:
            result.append((page, translations))
    return result


def _record(session: Session, page: SitePage, author: User, action: str, locale: str | None = None, row=None) -> None:
    revision = SitePageRevision(page_id=page.id, action=action, locale=locale, author_id=author.id)
    if row is not None:
        revision.title = row.title
        revision.content_md = row.content_md
        revision.fields_json = row.fields_json
    session.add(revision)


def _ensure_slug_free(session: Session, slug: str, exclude_page_id: str | None = None) -> None:
    if slug in BUILTIN_SLUGS.values():
        raise SlugTaken()
    other = session.exec(select(SitePage).where(SitePage.slug == slug)).first()
    if other is not None and other.id != exclude_page_id:
        raise SlugTaken()


def create_page(session: Session, author: User, data: PageCreate) -> SitePage:
    _ensure_slug_free(session, data.slug)
    page = SitePage(
        kind="custom",
        slug=data.slug,
        is_published=data.is_published,
        show_in_footer=data.show_in_footer,
        footer_order=data.footer_order,
    )
    session.add(page)
    session.flush()
    _record(session, page, author, "created")
    session.commit()
    session.refresh(page)
    return page


def update_page(session: Session, author: User, page: SitePage, data: dict) -> SitePage:
    """Change a custom page's slug, visibility or footer placement."""
    changes = {field: value for field, value in data.items() if value is not None and getattr(page, field) != value}
    if not changes:
        return page
    # The built-in pages' slugs are frontend routes and they have to stay public, so there's
    # nothing about them to change here — only their text.
    if page.kind != "custom":
        raise BuiltinProtected()
    if "slug" in changes:
        _ensure_slug_free(session, changes["slug"], exclude_page_id=page.id)
    for field, value in changes.items():
        setattr(page, field, value)
    page.updated_at = _now()
    session.add(page)
    _record(session, page, author, "settings_changed")
    session.commit()
    session.refresh(page)
    return page


def _normalized_fields(page: SitePage, fields: dict[str, str]) -> dict[str, str]:
    cleaned = {key: value.strip() for key, value in fields.items() if value and value.strip()}
    allowed = LANDING_FIELDS if page.kind == "landing" else ()
    if any(key not in allowed for key in cleaned):
        raise FieldsNotAllowed()
    return cleaned


def _find_translation(session: Session, page_id: str, locale: str) -> SitePageTranslation | None:
    return session.exec(
        select(SitePageTranslation).where(SitePageTranslation.page_id == page_id, SitePageTranslation.locale == locale)
    ).first()


def save_translation(session: Session, author: User, page: SitePage, locale: str, data: TranslationUpdate) -> None:
    """Save a language's text; saving it empty resets that language to the fallback."""
    title = data.title.strip()
    # The landing page has no heading to set, so a title there would be saved and never shown.
    if page.kind == "landing":
        title = ""
    content_md = data.content_md if data.content_md.strip() else ""
    fields = _normalized_fields(page, data.fields)
    if not (title or content_md or fields):
        reset_translation(session, author, page, locale)
        return
    # A custom page's title is its heading and footer label — it would be unreachable without.
    if page.kind == "custom" and not title:
        raise TitleRequired()

    row = _find_translation(session, page.id, locale) or SitePageTranslation(page_id=page.id, locale=locale)
    row.title = title
    row.content_md = content_md
    row.fields_json = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    row.updated_at = page.updated_at = _now()
    session.add(row)
    session.add(page)
    _record(session, page, author, "content_saved", locale, row)
    session.commit()


def reset_translation(session: Session, author: User, page: SitePage, locale: str) -> None:
    """Remove a language's text, so the page falls back to English or the built-in template."""
    row = _find_translation(session, page.id, locale)
    if row is None:
        return
    session.delete(row)
    page.updated_at = _now()
    session.add(page)
    _record(session, page, author, "content_reset", locale)
    session.commit()


def delete_page(session: Session, page: SitePage) -> None:
    if page.kind != "custom":
        raise BuiltinProtected()
    session.execute(delete(SitePageRevision).where(SitePageRevision.page_id == page.id))
    session.execute(delete(SitePageTranslation).where(SitePageTranslation.page_id == page.id))
    session.delete(page)
    session.commit()


def history(session: Session, page: SitePage, limit: int = HISTORY_LIMIT) -> list[tuple[SitePageRevision, User | None]]:
    """The page's changes, newest first, each with its author (None once that account is gone)."""
    rows = session.exec(
        select(SitePageRevision, User)
        .join(User, SitePageRevision.author_id == User.id, isouter=True)
        .where(SitePageRevision.page_id == page.id)
        .order_by(SitePageRevision.created_at.desc())
        .limit(limit)
    )
    return list(rows)


def last_change(session: Session, page: SitePage) -> tuple[SitePageRevision, User | None] | None:
    rows = history(session, page, limit=1)
    return rows[0] if rows else None


def forget_author(session: Session, user_id: str) -> None:
    """Detach a deleted account from the page history (called by users/account.py, which commits)."""
    session.execute(update(SitePageRevision).where(SitePageRevision.author_id == user_id).values(author_id=None))
