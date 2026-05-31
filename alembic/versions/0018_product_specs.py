"""Add product_specs and category_specs tables

Revision ID: 0018_product_specs
Revises: 0017_premium_plan
Create Date: 2026-05-31 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_product_specs"
down_revision: Union[str, None] = "0017_premium_plan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Use raw SQL so the migration is truly idempotent regardless of
    # SQLAlchemy version — no Inspector.from_engine() needed.
    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS product_specs (
            id         SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            client_id  INTEGER NOT NULL REFERENCES clients(id)  ON DELETE CASCADE,
            name       VARCHAR(128) NOT NULL,
            value      VARCHAR(512) NOT NULL
        )
    """))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_product_specs_product_id"
        " ON product_specs (product_id)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_product_specs_client_id"
        " ON product_specs (client_id)"
    ))

    bind.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS category_specs (
            id            SERIAL PRIMARY KEY,
            client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            category      VARCHAR(128) NOT NULL,
            name          VARCHAR(128) NOT NULL,
            is_filterable BOOLEAN NOT NULL DEFAULT TRUE
        )
    """))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_category_specs_client_id"
        " ON category_specs (client_id)"
    ))


def downgrade() -> None:
    op.drop_table("category_specs")
    op.drop_table("product_specs")
