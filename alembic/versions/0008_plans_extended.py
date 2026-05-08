"""plans extended fields + clients.plan_id

Revision ID: 0008_plans_extended
Revises: 0007_domains
Create Date: 2026-05-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_plans_extended"
down_revision: Union[str, None] = "0007_domains"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- plans: new columns ---
    op.add_column("plans", sa.Column("slug", sa.String(length=64), nullable=True))
    op.add_column("plans", sa.Column("price", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "plans",
        sa.Column(
            "currency", sa.String(length=8), nullable=False, server_default="USD"
        ),
    )
    op.add_column("plans", sa.Column("products_limit", sa.Integer(), nullable=True))
    op.add_column(
        "plans", sa.Column("images_per_product_limit", sa.Integer(), nullable=True)
    )
    op.add_column("plans", sa.Column("domains_limit", sa.Integer(), nullable=True))
    op.add_column("plans", sa.Column("users_limit", sa.Integer(), nullable=True))
    op.add_column(
        "plans",
        sa.Column(
            "analytics_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "plans",
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "plans",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )

    # Backfill: copy price_monthly -> price for existing rows
    op.execute("UPDATE plans SET price = price_monthly WHERE price IS NULL")

    op.create_unique_constraint("uq_plans_slug", "plans", ["slug"])
    op.create_index("ix_plans_slug", "plans", ["slug"])

    # --- clients: plan_id ---
    op.add_column("clients", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_clients_plan_id",
        "clients",
        "plans",
        ["plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_clients_plan_id", "clients", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_clients_plan_id", table_name="clients")
    op.drop_constraint("fk_clients_plan_id", "clients", type_="foreignkey")
    op.drop_column("clients", "plan_id")

    op.drop_index("ix_plans_slug", table_name="plans")
    op.drop_constraint("uq_plans_slug", "plans", type_="unique")
    op.drop_column("plans", "created_at")
    op.drop_column("plans", "active")
    op.drop_column("plans", "analytics_enabled")
    op.drop_column("plans", "users_limit")
    op.drop_column("plans", "domains_limit")
    op.drop_column("plans", "images_per_product_limit")
    op.drop_column("plans", "products_limit")
    op.drop_column("plans", "currency")
    op.drop_column("plans", "price")
    op.drop_column("plans", "slug")
