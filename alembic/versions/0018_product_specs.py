"""Add product_specs and category_specs tables

Revision ID: 0018_product_specs
Revises: 0017_premium_plan
Create Date: 2026-05-31 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision: str = "0018_product_specs"
down_revision: Union[str, None] = "0017_premium_plan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing = inspector.get_table_names()

    if "product_specs" not in existing:
        op.create_table(
            "product_specs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "product_id",
                sa.Integer(),
                sa.ForeignKey("products.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "client_id",
                sa.Integer(),
                sa.ForeignKey("clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("value", sa.String(512), nullable=False),
        )
        op.create_index("ix_product_specs_product_id", "product_specs", ["product_id"])
        op.create_index("ix_product_specs_client_id", "product_specs", ["client_id"])

    if "category_specs" not in existing:
        op.create_table(
            "category_specs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "client_id",
                sa.Integer(),
                sa.ForeignKey("clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("category", sa.String(128), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column(
                "is_filterable",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
        )
        op.create_index("ix_category_specs_client_id", "category_specs", ["client_id"])


def downgrade() -> None:
    op.drop_table("category_specs")
    op.drop_table("product_specs")
