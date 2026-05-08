"""domains table

Revision ID: 0007_domains
Revises: 0006_payments_table
Create Date: 2026-05-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_domains"
down_revision: Union[str, None] = "0006_payments_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "dns_connected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("domain", name="uq_domains_domain"),
    )
    op.create_index("ix_domains_client_id", "domains", ["client_id"])
    op.create_index("ix_domains_domain", "domains", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_domains_domain", table_name="domains")
    op.drop_index("ix_domains_client_id", table_name="domains")
    op.drop_table("domains")
