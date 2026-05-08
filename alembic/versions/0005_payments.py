"""payment_requests + clients.domain_status

Revision ID: 0005_payments
Revises: 0004_products
Create Date: 2026-05-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_payments"
down_revision: Union[str, None] = "0004_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "domain_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_table(
        "payment_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_slug", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_payment_requests_client_slug", "payment_requests", ["client_slug"])


def downgrade() -> None:
    op.drop_index("ix_payment_requests_client_slug", table_name="payment_requests")
    op.drop_table("payment_requests")
    op.drop_column("clients", "domain_status")
