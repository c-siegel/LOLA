"""
Admin Page Routes

Lets an admin edit the public pages — the landing page's texts, the imprint, the privacy policy —
and create further pages such as an accessibility statement, each per language, with a history
of who changed what when. The logic lives in service.py.
"""

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session

from app.core.deps import get_current_admin, get_session
from app.features.pages import service
from app.features.pages.models import SitePage
from app.features.pages.schemas import (
    AdminPageOut,
    AdminTranslationOut,
    AuthorOut,
    LastChangeOut,
    Locale,
    PageCreate,
    PageUpdate,
    RevisionOut,
    TranslationUpdate,
)
from app.features.users.models import User

router = APIRouter(prefix="/admin/pages", tags=["admin"])


def _author(user: User | None) -> AuthorOut | None:
    return AuthorOut(name=user.name, email=user.email) if user is not None else None


def _to_out(session: Session, page: SitePage) -> AdminPageOut:
    last = service.last_change(session, page)
    return AdminPageOut(
        id=page.id,
        kind=page.kind,
        slug=page.slug,
        is_published=page.is_published,
        show_in_footer=page.show_in_footer,
        footer_order=page.footer_order,
        translations=[
            AdminTranslationOut(
                locale=row.locale,
                title=row.title,
                content_md=row.content_md,
                fields=service.fields_of(row),
                updated_at=row.updated_at,
            )
            for row in service.translations_of(session, page.id)
        ],
        last_change=LastChangeOut(at=last[0].created_at, author=_author(last[1])) if last else None,
    )


@router.get("", response_model=list[AdminPageOut])
def list_pages(admin: User = Depends(get_current_admin), session: Session = Depends(get_session)):
    """Every page, the built-in ones first."""
    return [_to_out(session, page) for page in service.list_pages(session)]


@router.post("", response_model=AdminPageOut, status_code=201)
def create_page(data: PageCreate, admin: User = Depends(get_current_admin), session: Session = Depends(get_session)):
    """Create a custom page (e.g. an accessibility statement); its text is added per language."""
    return _to_out(session, service.create_page(session, admin, data))


@router.get("/{page_id}", response_model=AdminPageOut)
def get_page(page_id: str, admin: User = Depends(get_current_admin), session: Session = Depends(get_session)):
    return _to_out(session, service.get_page(session, page_id))


@router.patch("/{page_id}", response_model=AdminPageOut)
def update_page(
    page_id: str,
    data: PageUpdate,
    admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
):
    """Change a custom page's slug, whether it's published, and its footer link."""
    page = service.get_page(session, page_id)
    return _to_out(session, service.update_page(session, admin, page, data.model_dump(exclude_unset=True)))


@router.delete("/{page_id}", status_code=204)
def delete_page(page_id: str, admin: User = Depends(get_current_admin), session: Session = Depends(get_session)):
    """Delete a custom page with its texts and history. The built-in pages can't be deleted."""
    service.delete_page(session, service.get_page(session, page_id))
    return Response(status_code=204)


@router.put("/{page_id}/translations/{locale}", response_model=AdminPageOut)
def put_translation(
    page_id: str,
    locale: Locale,
    data: TranslationUpdate,
    admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
):
    """Save the page's text in one language; an empty text resets that language."""
    page = service.get_page(session, page_id)
    service.save_translation(session, admin, page, locale, data)
    return _to_out(session, page)


@router.delete("/{page_id}/translations/{locale}", response_model=AdminPageOut)
def delete_translation(
    page_id: str,
    locale: Locale,
    admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
):
    """Reset one language to the fallback (English, then the built-in template)."""
    page = service.get_page(session, page_id)
    service.reset_translation(session, admin, page, locale)
    return _to_out(session, page)


@router.get("/{page_id}/history", response_model=list[RevisionOut])
def get_history(page_id: str, admin: User = Depends(get_current_admin), session: Session = Depends(get_session)):
    """Who changed the page when, newest first."""
    page = service.get_page(session, page_id)
    return [
        RevisionOut(
            id=revision.id,
            action=revision.action,
            locale=revision.locale,
            created_at=revision.created_at,
            author=_author(author),
        )
        for revision, author in service.history(session, page)
    ]
