"""products: add brand, old_price, specs

Revision ID: 0009_products_extended
Revises: 0008_plans_extended
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_products_extended"
down_revision: Union[str, None] = "0008_plans_extended"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("brand", sa.String(length=128), nullable=True))
    op.add_column("products", sa.Column("old_price", sa.Numeric(10, 2), nullable=True))
    op.add_column("products", sa.Column("specs", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "specs")
    op.drop_column("products", "old_price")
    op.drop_column("products", "brand")
