"""Add bot_mode, bot_username, bot_id columns to clients table

Revision ID: 0019_bot_mode
Revises: 0018_product_specs
Create Date: 2026-06-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_bot_mode"
down_revision: Union[str, None] = "0018_product_specs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "bot_mode VARCHAR(16) NOT NULL DEFAULT 'shared'"
    ))
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "bot_username VARCHAR(64)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "bot_id BIGINT"
    ))


def downgrade() -> None:
    op.drop_column("clients", "bot_id")
    op.drop_column("clients", "bot_username")
    op.drop_column("clients", "bot_mode")
