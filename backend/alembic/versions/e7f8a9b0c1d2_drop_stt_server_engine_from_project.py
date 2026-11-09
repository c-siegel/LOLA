"""drop stt_server_engine from project

faster-whisper was removed; Parakeet is the only local server engine, so there is nothing left
for a project to choose.

Revision ID: e7f8a9b0c1d2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch mode: SQLite can only drop a column by rebuilding the table.
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_column('stt_server_engine')


def downgrade() -> None:
    # Re-added empty: NULL meant "follow the deployment's setting", the old default. The engine
    # choices projects had made before the upgrade are gone.
    with op.batch_alter_table('project') as batch_op:
        batch_op.add_column(sa.Column('stt_server_engine', sa.String(), nullable=True))
