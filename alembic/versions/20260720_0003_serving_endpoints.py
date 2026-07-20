"""Add the scale-to-zero endpoint registry and audit trail.

Revision ID: 20260720_0003
Revises: 20260719_0002
Create Date: 2026-07-20
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260720_0003"
down_revision: str | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "serving_endpoints",
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("api_key_ref", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("gpu_model", sa.String(length=255), nullable=True),
        sa.Column("hourly_price_usd", sa.Float(), nullable=True),
        sa.Column("cost_accrued_usd", sa.Float(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(state IN ('provisioning','loading','ready','draining') AND active_slot = 1) "
            "OR (state = 'cold' AND active_slot IS NULL)",
            name=op.f("ck_serving_endpoints_active_slot_matches_state"),
        ),
        sa.CheckConstraint(
            "state IN ('cold','provisioning','loading','ready','draining')",
            name=op.f("ck_serving_endpoints_state_allowed"),
        ),
        sa.CheckConstraint(
            "cost_accrued_usd >= 0",
            name=op.f("ck_serving_endpoints_cost_nonnegative"),
        ),
        sa.CheckConstraint(
            "hourly_price_usd IS NULL OR hourly_price_usd >= 0",
            name=op.f("ck_serving_endpoints_hourly_price_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["api_key_ref"],
            ["provider_credentials.id"],
            name=op.f("fk_serving_endpoints_api_key_ref_provider_credentials"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("model_id", name=op.f("pk_serving_endpoints")),
        sa.UniqueConstraint("active_slot", name="uq_serving_endpoints_active_slot"),
        sa.UniqueConstraint("session_id", name=op.f("uq_serving_endpoints_session_id")),
    )
    op.create_index(
        "ix_serving_endpoints_state_updated",
        "serving_endpoints",
        ["state", "updated_at"],
        unique=False,
    )
    op.create_table(
        "serving_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["serving_endpoints.model_id"],
            name=op.f("fk_serving_events_model_id_serving_endpoints"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_serving_events")),
    )
    op.create_index(
        "ix_serving_events_model_time",
        "serving_events",
        ["model_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_serving_events_session_time",
        "serving_events",
        ["session_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_serving_events_session_time", table_name="serving_events")
    op.drop_index("ix_serving_events_model_time", table_name="serving_events")
    op.drop_table("serving_events")
    op.drop_index("ix_serving_endpoints_state_updated", table_name="serving_endpoints")
    op.drop_table("serving_endpoints")
