"""products: add group_name

Revision ID: 0010_products_group_name
Revises: 0009_products_extended
Create Date: 2026-05-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_products_group_name"
down_revision: Union[str, None] = "0009_products_extended"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("group_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "group_name")
