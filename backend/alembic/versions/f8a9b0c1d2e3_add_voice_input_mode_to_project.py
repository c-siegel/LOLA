"""add voice_input_mode to project

Hands-free voice input next to push-to-talk, chosen per project.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default: every existing project keeps push-to-talk, the only mode there was.
    with op.batch_alter_table('project') as batch_op:
        batch_op.add_column(
            sa.Column('voice_input_mode', sa.String(), nullable=False, server_default='push_to_talk')
        )


def downgrade() -> None:
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_column('voice_input_mode')
