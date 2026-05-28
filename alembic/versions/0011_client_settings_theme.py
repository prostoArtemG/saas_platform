"""client_settings: add theme_name

Revision ID: 0011_client_settings_theme
Revises: 0010_products_group_name
Create Date: 2026-05-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_client_settings_theme"
down_revision: Union[str, None] = "0010_products_group_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "client_settings",
        sa.Column(
            "theme_name",
            sa.String(length=64),
            nullable=False,
            server_default="light_red",
        ),
    )


def downgrade() -> None:
    op.drop_column("client_settings", "theme_name")
