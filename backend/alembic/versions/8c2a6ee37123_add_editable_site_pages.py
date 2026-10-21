"""add editable site pages

Admins can now edit the landing page's texts, the imprint and the privacy policy, and add pages
such as an accessibility statement (features/pages). Three new tables only, so nothing existing
needs batch mode; the booleans and texts get a server_default anyway so rows inserted outside the
ORM stay valid. The built-in pages' rows are created on first use, not here.

Revision ID: 8c2a6ee37123
Revises: e0f1a2b3c4d5
Create Date: 2026-10-18 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8c2a6ee37123'
down_revision: Union[str, None] = 'e0f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sitepage',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('show_in_footer', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('footer_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sitepage_slug'), 'sitepage', ['slug'], unique=True)

    op.create_table(
        'sitepagetranslation',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('page_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('locale', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('content_md', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('fields_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='{}'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['page_id'], ['sitepage.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('page_id', 'locale', name='uq_sitepagetranslation_page_locale'),
    )
    op.create_index(op.f('ix_sitepagetranslation_page_id'), 'sitepagetranslation', ['page_id'], unique=False)

    op.create_table(
        'sitepagerevision',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('page_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('locale', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('content_md', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('fields_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='{}'),
        sa.Column('author_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['author_id'], ['user.id']),
        sa.ForeignKeyConstraint(['page_id'], ['sitepage.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sitepagerevision_page_id'), 'sitepagerevision', ['page_id'], unique=False)
    op.create_index(op.f('ix_sitepagerevision_author_id'), 'sitepagerevision', ['author_id'], unique=False)
    op.create_index(op.f('ix_sitepagerevision_created_at'), 'sitepagerevision', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sitepagerevision_created_at'), table_name='sitepagerevision')
    op.drop_index(op.f('ix_sitepagerevision_author_id'), table_name='sitepagerevision')
    op.drop_index(op.f('ix_sitepagerevision_page_id'), table_name='sitepagerevision')
    op.drop_table('sitepagerevision')
    op.drop_index(op.f('ix_sitepagetranslation_page_id'), table_name='sitepagetranslation')
    op.drop_table('sitepagetranslation')
    op.drop_index(op.f('ix_sitepage_slug'), table_name='sitepage')
    op.drop_table('sitepage')
