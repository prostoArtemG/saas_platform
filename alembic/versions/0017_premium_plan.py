"""Seed Premium plan

Revision ID: 0017_premium_plan
Revises: 0016_site_events
Create Date: 2026-05-30 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision: str = "0017_premium_plan"
down_revision: Union[str, None] = "0016_site_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    tables = inspector.get_table_names()

    if "plans" not in tables:
        return  # plans table not yet created — skip

    # Insert Premium plan only if no row with slug='premium' already exists
    bind.execute(
        sa.text(
            """
            INSERT INTO plans
                (name, slug, price, currency, price_monthly, products_limit,
                 analytics_enabled, active, can_buyout)
            SELECT
                'Premium', 'premium', 30.00, 'USD', 30.00, 1000,
                TRUE, TRUE, FALSE
            WHERE NOT EXISTS (
                SELECT 1 FROM plans WHERE slug = 'premium'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM plans WHERE slug = 'premium'"))
