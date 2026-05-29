"""orders table

Revision ID: 0013_orders
Revises: 0012_client_settings_contacts
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision: str = "0013_orders"
down_revision: Union[str, None] = "0012_client_settings_contacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    table_exists = inspector.has_table("orders")

    if not table_exists:
        # Fresh DB — create with full schema
        op.create_table(
            "orders",
            sa.Column("id",             sa.Integer(),               nullable=False, autoincrement=True),
            sa.Column("client_id",      sa.Integer(),               nullable=True),
            sa.Column("customer_name",  sa.String(255),             nullable=True,  server_default=""),
            sa.Column("customer_phone", sa.String(64),              nullable=True,  server_default=""),
            sa.Column("customer_city",  sa.String(255),             nullable=True),
            sa.Column("comment",        sa.Text(),                  nullable=True),
            sa.Column("items_json",     sa.Text(),                  nullable=False, server_default="[]"),
            sa.Column("total",          sa.Numeric(10, 2),          nullable=False, server_default="0"),
            sa.Column("status",         sa.String(32),              nullable=False, server_default="new"),
            sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("NOW()")),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE",
                                    name="fk_orders_client_id"),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        # Table already exists — add every missing column safely
        existing_cols = {col["name"] for col in inspector.get_columns("orders")}

        # client_id: added nullable (existing rows can't have a NOT NULL backfill)
        if "client_id" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "client_id INTEGER"
            )

        if "customer_name" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "customer_name VARCHAR(255) NOT NULL DEFAULT ''"
            )

        if "customer_phone" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "customer_phone VARCHAR(64) NOT NULL DEFAULT ''"
            )

        if "customer_city" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "customer_city VARCHAR(255)"
            )

        if "comment" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "comment TEXT"
            )

        if "items_json" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "items_json TEXT NOT NULL DEFAULT '[]'"
            )

        if "total" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "total NUMERIC(10, 2) NOT NULL DEFAULT 0"
            )

        if "status" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "status VARCHAR(32) NOT NULL DEFAULT 'new'"
            )

        if "created_at" not in existing_cols:
            op.execute(
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            )

        # FK client_id -> clients(id): add only if missing
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("orders")}
        if "fk_orders_client_id" not in existing_fks:
            # Check if client_id column exists now (we may have just added it)
            op.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'fk_orders_client_id'
                    ) THEN
                        ALTER TABLE orders
                            ADD CONSTRAINT fk_orders_client_id
                            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;
                    END IF;
                END $$
            """)

    # Ensure indexes exist (safe regardless of whether table was just created or pre-existing)
    # Re-read indexes after potential column additions
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("orders")}
    existing_cols_now = {col["name"] for col in inspector.get_columns("orders")}

    if "ix_orders_client_id" not in existing_indexes and "client_id" in existing_cols_now:
        op.create_index("ix_orders_client_id", "orders", ["client_id"])
    if "ix_orders_status" not in existing_indexes and "status" in existing_cols_now:
        op.create_index("ix_orders_status", "orders", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    if inspector.has_table("orders"):
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("orders")}
        if "ix_orders_status" in existing_indexes:
            op.drop_index("ix_orders_status",    table_name="orders")
        if "ix_orders_client_id" in existing_indexes:
            op.drop_index("ix_orders_client_id", table_name="orders")
        op.drop_table("orders")
