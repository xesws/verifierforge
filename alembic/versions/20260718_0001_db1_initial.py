"""DB-1 initial relational schema.

Revision ID: 20260718_0001
Revises: None
Create Date: 2026-07-18
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260718_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "traffic_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("route_taken", sa.String(64), nullable=False),
        sa.CheckConstraint("tokens_in >= 0", name="ck_traffic_requests_tokens_in_nonnegative"),
        sa.CheckConstraint("tokens_out >= 0", name="ck_traffic_requests_tokens_out_nonnegative"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_traffic_requests_latency_nonnegative"),
        sa.CheckConstraint("cost_usd >= 0", name="ck_traffic_requests_cost_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_traffic_requests"),
    )
    op.create_index(
        "ix_traffic_requests_prompt_ts", "traffic_requests", ["prompt_hash", "ts"]
    )
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("template", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("s3_prefix", sa.String(1024), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_table(
        "clusters",
        sa.Column("cluster_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("monthly_calls", sa.Integer(), nullable=False),
        sa.Column("monthly_cost_usd", sa.Float(), nullable=False),
        sa.Column("trainable", sa.Boolean(), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("analyzer_summary", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("monthly_calls >= 0", name="ck_clusters_monthly_calls_nonnegative"),
        sa.CheckConstraint(
            "monthly_cost_usd >= 0", name="ck_clusters_monthly_cost_nonnegative"
        ),
        sa.PrimaryKeyConstraint("cluster_id", name="pk_clusters"),
    )
    op.create_index("ix_clusters_status", "clusters", ["status"])
    op.create_table(
        "routing_state",
        sa.Column("cluster_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("canary_percent", sa.Integer(), nullable=False),
        sa.Column("target_model", sa.String(1024), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "canary_percent >= 0 AND canary_percent <= 100",
            name="ck_routing_state_canary_percent_range",
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"], ["clusters.cluster_id"], name="fk_routing_state_cluster_id_clusters", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("cluster_id", name="pk_routing_state"),
    )
    op.create_table(
        "guardian_scores",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cluster_id", sa.String(128), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_guardian_scores_score_range"),
        sa.ForeignKeyConstraint(
            ["cluster_id"], ["clusters.cluster_id"], name="fk_guardian_scores_cluster_id_clusters", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guardian_scores"),
    )
    op.create_index(
        "ix_guardian_scores_cluster_ts", "guardian_scores", ["cluster_id", "ts"]
    )
    op.create_table(
        "live_pass_rate",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cluster_id", sa.String(128), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pass_rate", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "pass_rate >= 0 AND pass_rate <= 1", name="ck_live_pass_rate_pass_rate_range"
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"], ["clusters.cluster_id"], name="fk_live_pass_rate_cluster_id_clusters", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_live_pass_rate"),
    )
    op.create_index(
        "ix_live_pass_rate_cluster_ts", "live_pass_rate", ["cluster_id", "ts"]
    )
    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("cluster_id", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("trace_s3_key", sa.String(1024), nullable=True),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=True),
        sa.Column("run_status", sa.String(32), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_agent_decisions_confidence_range",
        ),
        sa.CheckConstraint("tokens_in >= 0", name="ck_agent_decisions_tokens_in_nonnegative"),
        sa.CheckConstraint("tokens_out >= 0", name="ck_agent_decisions_tokens_out_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_decisions"),
    )
    op.create_index(
        "ix_agent_decisions_cluster_created", "agent_decisions", ["cluster_id", "created_at"]
    )
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_provider_credentials"),
        sa.UniqueConstraint(
            "user_id", "provider", name="uq_provider_credentials_user_provider"
        ),
    )
    op.create_index("ix_provider_credentials_user", "provider_credentials", ["user_id"])
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provision_handle", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["agent_decisions.id"], name="fk_approvals_decision_id_agent_decisions", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.UniqueConstraint("decision_id", name="uq_approvals_decision_id"),
    )
    op.create_index("ix_approvals_approved_at", "approvals", ["approved_at"])
    op.create_table(
        "provision_events",
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("approval_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name="fk_provision_events_approval_id_approvals", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_provision_events"),
    )
    op.create_index(
        "ix_provision_events_approval_time", "provision_events", ["approval_id", "occurred_at"]
    )
    op.create_index(
        "ix_provision_events_job_time", "provision_events", ["job_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_table("provision_events")
    op.drop_table("approvals")
    op.drop_table("provider_credentials")
    op.drop_table("agent_decisions")
    op.drop_table("live_pass_rate")
    op.drop_table("guardian_scores")
    op.drop_table("routing_state")
    op.drop_table("clusters")
    op.drop_table("jobs")
    op.drop_table("traffic_requests")
