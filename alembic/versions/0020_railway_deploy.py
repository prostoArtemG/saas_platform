"""Add railway deploy fields to clients table

Revision ID: 0020_railway_deploy
Revises: 0019_bot_mode
Create Date: 2026-06-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_railway_deploy"
down_revision: Union[str, None] = "0019_bot_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "railway_project_id VARCHAR(128)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "railway_service_id VARCHAR(128)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "railway_url VARCHAR(512)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "deployment_status VARCHAR(32)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS "
        "deployment_error TEXT"
    ))


def downgrade() -> None:
    op.drop_column("clients", "deployment_error")
    op.drop_column("clients", "deployment_status")
    op.drop_column("clients", "railway_url")
    op.drop_column("clients", "railway_service_id")
    op.drop_column("clients", "railway_project_id")
