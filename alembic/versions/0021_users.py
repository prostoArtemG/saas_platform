"""Add users table and clients.user_id FK

Revision ID: 0021_users
Revises: 0020_railway_deploy
Create Date: 2026-06-22 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_users"
down_revision: Union[str, None] = "0020_railway_deploy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Create users table
    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
            id          SERIAL PRIMARY KEY,
            email       VARCHAR(255) UNIQUE,
            password_hash VARCHAR(255),
            telegram_id BIGINT UNIQUE,
            name        VARCHAR(128),
            role        VARCHAR(32) NOT NULL DEFAULT 'client',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        )
    """))

    # Indexes
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_users_telegram_id ON users (telegram_id)"
    ))

    # Add user_id FK to clients
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_clients_user_id ON clients (user_id)"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("ALTER TABLE clients DROP COLUMN IF EXISTS user_id"))
    bind.execute(sa.text("DROP TABLE IF EXISTS users"))
