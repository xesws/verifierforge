from datetime import datetime, timezone

from core.contracts import (
    Control,
    Endpoint,
    Job,
    JobStatus,
    Metrics,
    Report,
    ReportVerdict,
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
