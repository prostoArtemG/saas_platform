"""orders table

Revision ID: 0013_orders
Revises: 0012_client_settings_contacts
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_orders"
down_revision: Union[str, None] = "0012_client_settings_contacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id",             sa.Integer(),      nullable=False, autoincrement=True),
        sa.Column("client_id",      sa.Integer(),      nullable=False),
        sa.Column("customer_name",  sa.String(255),    nullable=False),
        sa.Column("customer_phone", sa.String(64),     nullable=False),
        sa.Column("customer_city",  sa.String(255),    nullable=True),
        sa.Column("comment",        sa.Text(),          nullable=True),
        sa.Column("items_json",     sa.Text(),          nullable=False, server_default="[]"),
        sa.Column("total",          sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("status",         sa.String(32),     nullable=False, server_default="new"),
        sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_client_id", "orders", ["client_id"])
    op.create_index("ix_orders_status",    "orders", ["status"])


def downgrade() -> None:
    op.drop_index("ix_orders_status",    table_name="orders")
    op.drop_index("ix_orders_client_id", table_name="orders")
    op.drop_table("orders")
