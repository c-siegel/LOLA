"""add deep_llm_api_key_id, deep_llm_model and bridge_enabled to project

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('project') as batch_op:
        batch_op.add_column(sa.Column('deep_llm_api_key_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('deep_llm_model', sa.String(), nullable=True))
        # server_default 'true': projects that already reason get the bridge phrase too.
        batch_op.add_column(sa.Column('bridge_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.create_index(batch_op.f('ix_project_deep_llm_api_key_id'), ['deep_llm_api_key_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_project_deep_llm_api_key_id_userapikey', 'userapikey', ['deep_llm_api_key_id'], ['id']
        )


def downgrade() -> None:
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_constraint('fk_project_deep_llm_api_key_id_userapikey', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_project_deep_llm_api_key_id'))
        batch_op.drop_column('bridge_enabled')
        batch_op.drop_column('deep_llm_model')
        batch_op.drop_column('deep_llm_api_key_id')
