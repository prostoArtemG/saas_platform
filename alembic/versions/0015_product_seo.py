"""Add badge and SEO fields to products

Revision ID: 0015_product_seo
Revises: 0014_dashboard_token
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision: str = "0015_product_seo"
down_revision: Union[str, None] = "0014_dashboard_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("products")}

    new_cols = [
        ("badge",           sa.Column("badge",           sa.String(64),  nullable=True)),
        ("seo_title",       sa.Column("seo_title",       sa.String(255), nullable=True)),
        ("seo_description", sa.Column("seo_description", sa.Text(),      nullable=True)),
        ("seo_keywords",    sa.Column("seo_keywords",    sa.Text(),      nullable=True)),
    ]
    for col_name, col_def in new_cols:
        if col_name not in existing_cols:
            op.add_column("products", col_def)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("products")}
    for col_name in ("badge", "seo_title", "seo_description", "seo_keywords"):
        if col_name in existing_cols:
            op.drop_column("products", col_name)
