"""add tts_browser_enabled to project

Revision ID: f3a4b5c6d7e8
Revises: 8c2a6ee37123
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = '8c2a6ee37123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Off for every existing project: on-device speech output means a model download on each
    # visitor's device, so a teacher opts in (see Project.tts_browser_enabled).
    with op.batch_alter_table('project') as batch_op:
        batch_op.add_column(sa.Column('tts_browser_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_column('tts_browser_enabled')
