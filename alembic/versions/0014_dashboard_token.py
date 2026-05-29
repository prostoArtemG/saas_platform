"""Add dashboard_token to clients

Revision ID: 0014_dashboard_token
Revises: 0013_orders
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision: str = "0014_dashboard_token"
down_revision: Union[str, None] = "0013_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("clients")}

    if "dashboard_token" not in existing_cols:
        op.add_column("clients", sa.Column("dashboard_token", sa.String(64), nullable=True))

    # Backfill tokens for existing clients that don't have one.
    # md5 over random + id + timestamp — no extra extensions required.
    op.execute("""
        UPDATE clients
           SET dashboard_token = md5(random()::text || id::text || clock_timestamp()::text)
         WHERE dashboard_token IS NULL
    """)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("clients")}
    if "dashboard_token" in existing_cols:
        op.drop_column("clients", "dashboard_token")
