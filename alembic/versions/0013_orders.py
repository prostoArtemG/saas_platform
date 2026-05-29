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
        op.create_table(
            "orders",
            sa.Column("id",             sa.Integer(),             nullable=False, autoincrement=True),
            sa.Column("client_id",      sa.Integer(),             nullable=False),
            sa.Column("customer_name",  sa.String(255),           nullable=False),
            sa.Column("customer_phone", sa.String(64),            nullable=False),
            sa.Column("customer_city",  sa.String(255),           nullable=True),
            sa.Column("comment",        sa.Text(),                nullable=True),
            sa.Column("items_json",     sa.Text(),                nullable=False, server_default="[]"),
            sa.Column("total",          sa.Numeric(10, 2),        nullable=False, server_default="0"),
            sa.Column("status",         sa.String(32),            nullable=False, server_default="new"),
            sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("NOW()")),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    # Ensure all columns exist (safe if table was pre-created)
    existing_cols = {col["name"] for col in inspector.get_columns("orders")} if table_exists else set()

    def add_col_if_missing(col_name: str, ddl: str) -> None:
        if col_name not in existing_cols:
            op.execute(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {ddl}")

    add_col_if_missing("customer_city",  "customer_city  VARCHAR(255)")
    add_col_if_missing("comment",        "comment        TEXT")
    add_col_if_missing("items_json",     "items_json     TEXT NOT NULL DEFAULT '[]'")
    add_col_if_missing("total",          "total          NUMERIC(10, 2) NOT NULL DEFAULT 0")
    add_col_if_missing("status",         "status         VARCHAR(32) NOT NULL DEFAULT 'new'")
    add_col_if_missing("created_at",     "created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()")

    # Ensure indexes exist
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("orders")}
    if "ix_orders_client_id" not in existing_indexes:
        op.create_index("ix_orders_client_id", "orders", ["client_id"])
    if "ix_orders_status" not in existing_indexes:
        op.create_index("ix_orders_status", "orders", ["status"])


def downgrade() -> None:
    # Safe downgrade: only drop if the table exists
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    if inspector.has_table("orders"):
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("orders")}
        if "ix_orders_status" in existing_indexes:
            op.drop_index("ix_orders_status",    table_name="orders")
        if "ix_orders_client_id" in existing_indexes:
            op.drop_index("ix_orders_client_id", table_name="orders")
        op.drop_table("orders")
