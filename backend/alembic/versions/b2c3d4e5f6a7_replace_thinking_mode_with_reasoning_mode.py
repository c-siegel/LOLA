"""replace thinking_mode with reasoning_mode and reasoning_effort

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('project') as batch_op:
        batch_op.add_column(sa.Column('reasoning_mode', sa.String(), nullable=False, server_default='default'))
        batch_op.add_column(sa.Column('reasoning_effort', sa.String(), nullable=True))
    # "on" becomes "always"; "off" and "default" keep their meaning.
    op.execute("UPDATE project SET reasoning_mode = CASE thinking_mode WHEN 'on' THEN 'always' ELSE thinking_mode END")
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_column('thinking_mode')


def downgrade() -> None:
    with op.batch_alter_table('project') as batch_op:
        batch_op.add_column(sa.Column('thinking_mode', sa.String(), nullable=False, server_default='default'))
    # "adaptive" (a later phase) has no old equivalent and falls back to the model's default.
    op.execute(
        "UPDATE project SET thinking_mode = CASE reasoning_mode WHEN 'always' THEN 'on' WHEN 'off' THEN 'off' ELSE 'default' END"
    )
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_column('reasoning_effort')
        batch_op.drop_column('reasoning_mode')
