"""
Public Page Routes

The unauthenticated read side of the admin-editable pages: a page's texts in every language it
has, and the custom pages the site footer links to. Unpublished custom pages don't exist here.
"""

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.deps import get_session
from app.features.pages import service
from app.features.pages.schemas import FooterPageOut, PublicPageOut, PublicTranslationOut

router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("", response_model=list[FooterPageOut])
def list_footer_pages(session: Session = Depends(get_session)):
    """The published custom pages shown in the site footer."""
    return [
        FooterPageOut(slug=page.slug, titles={row.locale: row.title for row in translations})
        for page, translations in service.footer_pages(session)
    ]


@router.get("/{slug}", response_model=PublicPageOut)
def get_page(slug: str, session: Session = Depends(get_session)):
    """A page's texts per language; no texts means the frontend shows its built-in template."""
    kind, translations = service.get_public_page(session, slug)
    return PublicPageOut(
        slug=slug,
        kind=kind,
        translations={
            row.locale: PublicTranslationOut(
                title=row.title, content_md=row.content_md, fields=service.fields_of(row), updated_at=row.updated_at
            )
            for row in translations
        },
    )
