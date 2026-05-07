"""clients.template_name

Revision ID: 0003_clients_template
Revises: 0002_site_requests
Create Date: 2026-05-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_clients_template"
down_revision: Union[str, None] = "0002_site_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "template_name",
            sa.String(length=64),
            nullable=False,
            server_default="technovlada",
        ),
    )


def downgrade() -> None:
    op.drop_column("clients", "template_name")
