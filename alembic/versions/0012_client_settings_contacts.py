"""client_settings: add shop contact fields

Revision ID: 0012_client_settings_contacts
Revises: 0011_client_settings_theme
Create Date: 2026-05-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_client_settings_contacts"
down_revision: Union[str, None] = "0011_client_settings_theme"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("client_settings", sa.Column("shop_title",    sa.String(255),  nullable=True))
    op.add_column("client_settings", sa.Column("phone",         sa.String(64),   nullable=True))
    op.add_column("client_settings", sa.Column("address",       sa.String(255),  nullable=True))
    op.add_column("client_settings", sa.Column("telegram_url",  sa.String(512),  nullable=True))
    op.add_column("client_settings", sa.Column("instagram_url", sa.String(512),  nullable=True))
    op.add_column("client_settings", sa.Column("logo_url",      sa.String(1024), nullable=True))


def downgrade() -> None:
    for col in ("logo_url", "instagram_url", "telegram_url", "address", "phone", "shop_title"):
        op.drop_column("client_settings", col)
