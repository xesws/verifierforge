"""Record measured serving cold-start duration.

Revision ID: 20260720_0004
Revises: 20260720_0003
Create Date: 2026-07-20
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260720_0004"
down_revision: str | None = "20260720_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "serving_endpoints",
        sa.Column("cold_start_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("serving_endpoints", "cold_start_seconds")
