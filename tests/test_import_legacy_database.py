from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from app.db.migration import migrate_sqlite
from app.db.settings import DatabaseSettings
from scripts.import_legacy_database import (
    LegacyImportError,
    apply_import_plan,
    build_import_plan,
    main,
    sanitize_error,
    verify_import_plan,
)


NOW = "2026-07-18T12:00:00Z"


@pytest.fixture
def legacy_source(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "legacy.sqlite3"
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _create_legacy_db(db_path)
    _create_run(runs_dir / "job-one")
    return db_path, runs_dir


def test_plan_preserves_decisions_and_filters_fk_bound_non_product_rows(
    legacy_source: tuple[Path, Path],
) -> None:
    db_path, runs_dir = legacy_source

    plan = build_import_plan(db_path, runs_dir)

    assert plan.counts()["clusters"] == 3
    assert plan.counts()["agent_decisions"] == 3
    assert {record.cluster_id for record in plan.agent_decisions} == {
        "data-pull-sql",
        "adversarial-over-budget-case",
        "rare-report",
    }
    assert plan.counts()["routing_state"] == 1
    assert plan.counts()["guardian_scores"] == 1
    assert plan.counts()["live_pass_rate"] == 1
    assert plan.skipped == {
        "guardian_scores_non_product": 1,
        "live_pass_rate_non_product": 1,
        "routing_state_non_product": 1,
    }
    assert plan.counts()["jobs"] == 1


def test_source_is_opened_read_only_without_schema_backfill(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    _create_legacy_db(db_path, include_route_path=False)

    plan = build_import_plan(db_path, tmp_path / "missing-runs")

    assert plan.counts()["traffic_requests"] == 2
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(traffic)")}
    assert "route_path" not in columns


@pytest.mark.asyncio
async def test_apply_is_idempotent_and_verify_matches(legacy_source: tuple[Path, Path], tmp_path: Path) -> None:
    db_path, runs_dir = legacy_source
    plan = build_import_plan(db_path, runs_dir)
    target = tmp_path / "target.sqlite3"
    settings = DatabaseSettings.sqlite(target)
    await migrate_sqlite(settings)

    first = await apply_import_plan(plan, settings)
    second = await apply_import_plan(plan, settings)
    verification = await verify_import_plan(plan, settings)

    assert first.verification.ok is True
    assert first.inserted["agent_decisions"] == 3
    assert second.verification.ok is True
    assert all(count == 0 for count in second.inserted.values())
    assert verification.ok is True

    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM clusters").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM agent_decisions").fetchone()[0] == 3
        non_product = connection.execute(
            """
            SELECT count(*) FROM agent_decisions
            WHERE cluster_id = 'adversarial-over-budget-case'
            """
        ).fetchone()[0]
        assert non_product == 1


@pytest.mark.asyncio
async def test_verify_and_reapply_detect_conflicting_target_rows(
    legacy_source: tuple[Path, Path], tmp_path: Path
) -> None:
    db_path, runs_dir = legacy_source
    plan = build_import_plan(db_path, runs_dir)
    target = tmp_path / "target.sqlite3"
    settings = DatabaseSettings.sqlite(target)
    await migrate_sqlite(settings)
    await apply_import_plan(plan, settings)

    with sqlite3.connect(target) as connection:
        connection.execute(
            "UPDATE agent_decisions SET model_name = 'changed-model' WHERE id = 'decision-non-product'"
        )

    verification = await verify_import_plan(plan, settings)
    assert verification.ok is False
    with pytest.raises(LegacyImportError, match="conflicting"):
        await apply_import_plan(plan, settings)


def test_cli_dry_run_prints_counts_without_database_url(
    legacy_source: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    db_path, runs_dir = legacy_source

    code = main(["dry-run", "--source-db", str(db_path), "--runs-dir", str(runs_dir)])

    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "planned"
    assert payload["counts"]["agent_decisions"] == 3
    assert "postgres://" not in captured.out
    assert captured.err == ""


def test_error_sanitizer_removes_database_urls_and_passwords() -> None:
    fixture_url = "".join(
        ("postgresql", "://", "owner", ":", "fixture", "@", "example.test", "/db")
    )
    raw = f"could not connect to {fixture_url} password=fixture SUPABASE_DB_URL={fixture_url}"

    sanitized = sanitize_error(raw)

    assert "fixture" not in sanitized
    assert "example.test" not in sanitized
    assert "[redacted" in sanitized


def _create_legacy_db(path: Path, *, include_route_path: bool = True) -> None:
    with sqlite3.connect(path) as connection:
        route_path_sql = ", route_path TEXT NOT NULL DEFAULT 'default'" if include_route_path else ""
        connection.execute(
            f"""
            CREATE TABLE traffic (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                system_prompt_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                estimated_cost_usd REAL NOT NULL
                {route_path_sql}
            )
            """
        )
        if include_route_path:
            connection.executemany(
                """
                INSERT INTO traffic (
                    id, timestamp, system_prompt_hash, model, input_tokens,
                    output_tokens, latency_ms, estimated_cost_usd, route_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, NOW, "a" * 64, "baseline", 10, 5, 12.5, 0.001, "default"),
                    (2, NOW, "b" * 64, "tuned", 9, 4, 10.0, 0.0008, "tuned"),
                ],
            )
        else:
            connection.executemany(
                """
                INSERT INTO traffic (
                    id, timestamp, system_prompt_hash, model, input_tokens,
                    output_tokens, latency_ms, estimated_cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, NOW, "a" * 64, "baseline", 10, 5, 12.5, 0.001),
                    (2, NOW, "b" * 64, "tuned", 9, 4, 10.0, 0.0008),
                ],
            )
        connection.execute(
            """
            CREATE TABLE routing (
                cluster_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                canary_percent INTEGER NOT NULL,
                target_upstream TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO routing VALUES (?, ?, ?, ?)",
            [
                ("data-pull-sql", 1, 50, "tuned"),
                ("non-product-route", 1, 10, "tuned"),
            ],
        )
        connection.execute(
            """
            CREATE TABLE guardian_scores (
                id INTEGER PRIMARY KEY,
                cluster_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                score REAL NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO guardian_scores VALUES (?, ?, ?, ?)",
            [
                (1, "data-pull-sql", NOW, 1.0),
                (2, "non-product-route", NOW, 1.0),
            ],
        )
        connection.execute(
            """
            CREATE TABLE live_pass_rate (
                id INTEGER PRIMARY KEY,
                cluster_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                pass_rate REAL NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO live_pass_rate VALUES (?, ?, ?, ?)",
            [
                (1, "data-pull-sql", NOW, 1.0),
                (2, "non-product-route", NOW, 1.0),
            ],
        )
        connection.execute(
            """
            CREATE TABLE agent_decisions (
                id TEXT PRIMARY KEY,
                cluster_id TEXT NOT NULL,
                evidence_fingerprint TEXT,
                run_status TEXT NOT NULL,
                decision_json TEXT,
                trace_id TEXT NOT NULL,
                trace_s3_key TEXT,
                provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                tokens_in INTEGER NOT NULL,
                tokens_out INTEGER NOT NULL,
                summary_json TEXT NOT NULL
            )
            """
        )
        decisions = [
            (
                "decision-product",
                "data-pull-sql",
                _decision_json("forge"),
            ),
            (
                "decision-non-product",
                "adversarial-over-budget-case",
                _decision_json("skip"),
            ),
            (
                "decision-failed",
                "rare-report",
                None,
            ),
        ]
        connection.executemany(
            """
            INSERT INTO agent_decisions (
                id, cluster_id, evidence_fingerprint, run_status, decision_json,
                trace_id, trace_s3_key, provider, model_name, created_at,
                tokens_in, tokens_out, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    decision_id,
                    cluster_id,
                    "c" * 64,
                    "failed" if decision_json is None else "completed",
                    decision_json,
                    f"trace-{decision_id}",
                    f"vf/agent-traces/trace-{decision_id}.json",
                    "mock",
                    "model-test",
                    NOW,
                    100,
                    20,
                    json.dumps(
                        {
                            "decision_id": decision_id,
                            "trace_id": f"trace-{decision_id}",
                            "cluster_id": cluster_id,
                            "evidence_fingerprint": "c" * 64,
                            "run_status": "failed" if decision_json is None else "completed",
                            "decision": json.loads(decision_json) if decision_json else None,
                            "trace_s3_key": f"vf/agent-traces/trace-{decision_id}.json",
                            "provider": "mock",
                            "model": "model-test",
                            "created_at": NOW,
                            "total_input_tokens": 100,
                            "total_output_tokens": 20,
                        },
                        separators=(",", ":"),
                    ),
                )
                for decision_id, cluster_id, decision_json in decisions
            ],
        )
        connection.execute(
            """
            CREATE TABLE approvals (
                id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL UNIQUE,
                approved_by TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                approval_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO approvals VALUES (?, ?, ?, ?, ?)",
            (
                "approval-non-product",
                "decision-non-product",
                "owner",
                NOW,
                json.dumps(
                    {
                        "approval_id": "approval-non-product",
                        "decision_id": "decision-non-product",
                        "approved_by": "owner",
                        "approved_at": NOW,
                    },
                    separators=(",", ":"),
                ),
            ),
        )


def _create_run(path: Path) -> None:
    path.mkdir()
    (path / "metrics.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "job_id": path.name,
                        "step": 1,
                        "reward_mean": 0.1,
                        "pass_at_1": 0.2,
                        "entropy": 1.0,
                        "timestamp": NOW,
                    },
                    separators=(",", ":"),
                ),
                json.dumps(
                    {
                        "job_id": path.name,
                        "step": 2,
                        "reward_mean": 0.3,
                        "pass_at_1": 0.4,
                        "entropy": 0.9,
                        "timestamp": "2026-07-18T12:00:01Z",
                    },
                    separators=(",", ":"),
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    final = path / "artifacts" / "final"
    final.mkdir(parents=True)
    (final / "model.txt").write_text("done\n", encoding="utf-8")


def _decision_json(kind: str) -> str:
    if kind == "forge":
        payload = {
            "decision": "forge",
            "rationale": "forge it",
            "confidence": 0.9,
            "config": {
                "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
                "steps": 400,
                "k": 8,
                "checkpoint_interval": 50,
                "budget_usd_cap": 25.0,
                "provider_pref": "auto",
            },
        }
    else:
        payload = {
            "decision": "skip",
            "rationale": "do not forge",
            "confidence": 0.8,
            "config": None,
        }
    return json.dumps(payload, separators=(",", ":"))
