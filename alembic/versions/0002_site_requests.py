"""site_requests table

Revision ID: 0002_site_requests
Revises: 0001_initial
Create Date: 2026-05-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_site_requests"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_name", sa.String(length=255), nullable=False),
        sa.Column("telegram", sa.String(length=128), nullable=False),
        sa.Column("site_type", sa.String(length=64), nullable=False),
        sa.Column("plan", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("site_requests")
