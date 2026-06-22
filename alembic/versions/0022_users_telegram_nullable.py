"""Make users.telegram_id nullable (email-only registration has no telegram_id)

Revision ID: 0022_users_telegram_nullable
Revises: 0021_users
Create Date: 2026-06-22 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_users_telegram_nullable"
down_revision: Union[str, None] = "0021_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Drop NOT NULL on telegram_id — email/password registration leaves it NULL.
    # The column was defined as nullable in the ORM model from the start; this
    # migration corrects any DB state where the constraint was created incorrectly.
    bind.execute(sa.text(
        "ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    # Re-add NOT NULL only if there are no NULLs present (otherwise this fails).
    bind.execute(sa.text(
        "ALTER TABLE users ALTER COLUMN telegram_id SET NOT NULL"
    ))
