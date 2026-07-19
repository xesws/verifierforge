from datetime import datetime, timezone

from core.contracts import (
    ApprovedSampleSource,
    ApprovedSampleSourceKind,
    Arena,
    ArenaSample,
    Cluster,
    ClusterStatus,
    Control,
    Endpoint,
    Job,
    JobStatus,
    LivePassRate,
    LivePassRatePoint,
    Metrics,
    Report,
    ReportVerdict,
    RoutingState,
)


def test_job_contract_round_trip() -> None:
    job = Job(
        job_id="run-123",
        template="nl2sql",
        status=JobStatus.DONE,
        model="tiny-model",
        created_at=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
        metrics=Metrics(
            steps=[1, 2],
            reward_mean=[0.2, 0.5],
            pass_at_1=[0.1, 0.4],
            entropy=[1.0, 0.8],
        ),
        control=Control(pass_at_1=[0.1, 0.1]),
        report=Report(
            baseline_pass_at_1=0.1,
            final_pass_at_1=0.4,
            control_final_pass_at_1=0.1,
            verdict=ReportVerdict.REAL_GAIN,
            narrative="The verifier reward improved held-out pass@1.",
        ),
        endpoint=Endpoint(base_url="http://localhost:8000", model_name="tiny-model"),
    )

    restored = Job.model_validate_json(job.model_dump_json())

    assert restored == job
    assert restored.status is JobStatus.DONE
    assert restored.report is not None
    assert restored.report.verdict is ReportVerdict.REAL_GAIN


def test_legacy_report_json_without_new_fields_still_validates() -> None:
    payload = {
        "baseline_pass_at_1": 0.1,
        "final_pass_at_1": 0.4,
        "control_final_pass_at_1": 0.1,
        "verdict": "real_gain",
        "narrative": "legacy fixture",
    }

    report = Report.model_validate(payload)

    assert report.projected_monthly_savings_usd is None
    assert report.arena is None


def test_cluster_and_live_pass_rate_round_trip() -> None:
    cluster = Cluster(
        cluster_id="support-ticket-extraction",
        name="Support ticket extraction",
        monthly_calls=240_000,
        monthly_cost_usd=4800.0,
        trainable=True,
        status=ClusterStatus.LIVE,
        job_id="nl2sql-gain",
        routing=RoutingState(
            cluster_id="support-ticket-extraction",
            enabled=True,
            canary_percent=100,
            target_model="vf-nl2sql-gain",
        ),
        live_pass_rate=LivePassRate(
            cluster_id="support-ticket-extraction",
            points=[
                LivePassRatePoint(
                    timestamp=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
                    pass_rate=0.89,
                )
            ],
        ),
        approved_sample_source=ApprovedSampleSource(
            kind=ApprovedSampleSourceKind.REPOSITORY_JSONL,
            uri="data/nl2sql/v0.10.0-training-pool.jsonl",
            sha256="a" * 64,
            row_count=50,
            approved_by="owner@example.com",
            approved_at=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        ),
    )

    restored = Cluster.model_validate_json(cluster.model_dump_json())

    assert restored == cluster
    assert restored.live_pass_rate is not None
    assert restored.approved_sample_source is not None
    assert restored.approved_sample_source.row_count == 50
    dumped = restored.live_pass_rate.points[0].model_dump()
    assert "pass_rate" in dumped
    assert "pass_at_1" not in dumped


def test_report_arena_extension_round_trip() -> None:
    report = Report(
        baseline_pass_at_1=0.16,
        final_pass_at_1=0.76,
        control_final_pass_at_1=0.2,
        verdict=ReportVerdict.REAL_GAIN,
        narrative="gain",
        projected_monthly_savings_usd=4300.0,
        arena=Arena(
            win_rate=0.95,
            samples=[
                ArenaSample(
                    prompt="q",
                    baseline_output="a",
                    tuned_output="b",
                    baseline_score=0.4,
                    tuned_score=0.9,
                )
            ],
        ),
    )

    restored = Report.model_validate_json(report.model_dump_json())

    assert restored.projected_monthly_savings_usd == 4300.0
    assert restored.arena is not None
    assert restored.arena.win_rate == 0.95
