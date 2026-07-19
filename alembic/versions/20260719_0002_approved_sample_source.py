"""Add governed sample-source metadata to clusters.

Revision ID: 20260719_0002
Revises: 20260718_0001
Create Date: 2026-07-19
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260719_0002"
down_revision: str | None = "20260718_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clusters",
        sa.Column("approved_sample_source", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clusters", "approved_sample_source")

