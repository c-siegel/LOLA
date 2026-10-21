"""add thinking_mode to project

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 'default': existing projects keep the model's own behaviour.
    with op.batch_alter_table('project') as batch_op:
        batch_op.add_column(sa.Column('thinking_mode', sa.String(), nullable=False, server_default='default'))


def downgrade() -> None:
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_column('thinking_mode')
