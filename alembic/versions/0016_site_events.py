"""Add site_events table

Revision ID: 0016_site_events
Revises: 0015_product_seo
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision: str = "0016_site_events"
down_revision: Union[str, None] = "0015_product_seo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    if "site_events" not in inspector.get_table_names():
        op.create_table(
            "site_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "client_id",
                sa.Integer(),
                sa.ForeignKey("clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_site_events_client_id", "site_events", ["client_id"])
        op.create_index("ix_site_events_created_at", "site_events", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    if "site_events" in inspector.get_table_names():
        op.drop_index("ix_site_events_created_at", table_name="site_events")
        op.drop_index("ix_site_events_client_id", table_name="site_events")
        op.drop_table("site_events")
