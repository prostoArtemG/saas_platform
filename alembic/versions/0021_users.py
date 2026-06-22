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

    # 1. Ensure users table exists (may already exist with missing columns)
    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY
        )
    """))

    # 2. Add all columns idempotently (IF NOT EXISTS handles partial migrations)
    for ddl in [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email         VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_id   BIGINT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS name          VARCHAR(128)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role          VARCHAR(32) NOT NULL DEFAULT 'client'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at    TIMESTAMPTZ NOT NULL DEFAULT now()",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
    ]:
        bind.execute(sa.text(ddl))

    # 3. Unique constraints / indexes (only after columns exist)
    bind.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'users_email_key' AND conrelid = 'users'::regclass
            ) THEN
                ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
            END IF;
        END $$
    """))
    bind.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'users_telegram_id_key' AND conrelid = 'users'::regclass
            ) THEN
                ALTER TABLE users ADD CONSTRAINT users_telegram_id_key UNIQUE (telegram_id);
            END IF;
        END $$
    """))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_users_email       ON users (email)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_users_telegram_id ON users (telegram_id)"
    ))

    # 4. clients.user_id column
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS user_id INTEGER"
    ))

    # 5. FK constraint (idempotent via DO block)
    bind.execute(sa.text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'clients_user_id_fkey' AND conrelid = 'clients'::regclass
            ) THEN
                ALTER TABLE clients
                    ADD CONSTRAINT clients_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END $$
    """))

    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_clients_user_id ON clients (user_id)"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_user_id_fkey"
    ))
    bind.execute(sa.text("ALTER TABLE clients DROP COLUMN IF EXISTS user_id"))
    bind.execute(sa.text("DROP INDEX IF EXISTS ix_users_email"))
    bind.execute(sa.text("DROP INDEX IF EXISTS ix_users_telegram_id"))
    bind.execute(sa.text("DROP TABLE IF EXISTS users"))
