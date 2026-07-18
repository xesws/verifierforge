from __future__ import annotations

from pathlib import Path

import pytest

from app.proxy.traffic import TrafficRecord, record_traffic
from scripts import traffic_gen, traffic_summary


def test_generator_builds_twenty_requests_for_each_distinct_product_family() -> None:
    requests = traffic_gen.build_requests()

    assert set(requests) == set(traffic_gen.FAMILIES)
    assert {name: len(items) for name, items in requests.items()} == {
        "support-ticket": 20,
        "invoice": 20,
        "data-pull-sql": 20,
    }
    systems = {items[0]["messages"][0]["content"] for items in requests.values()}
    assert systems == set(traffic_gen.SYSTEM_PROMPTS.values())
    assert all(item["model"] == "vf-demo" for items in requests.values() for item in items)


def test_mix_parser_and_replay_are_weighted_cyclic_and_report_failures() -> None:
    requests = traffic_gen.build_requests()
    sent_systems: list[str] = []

    def sender(_base_url: str, request: dict[str, object]) -> bool:
        messages = request["messages"]
        sent_systems.append(messages[0]["content"])  # type: ignore[index]
        return len(sent_systems) != 4

    stats = traffic_gen.replay_requests(
        requests,
        base_url="http://proxy.test",
        rate=0,
        total=6,
        mix=traffic_gen.parse_mix("support-ticket=1,invoice:1,data-pull-sql=1"),
        sender=sender,
    )

    assert stats.sent == 6
    assert (stats.success, stats.failed, stats.interrupted) == (5, 1, False)
    assert sent_systems[:3] == [
        traffic_gen.SYSTEM_PROMPTS["support-ticket"],
        traffic_gen.SYSTEM_PROMPTS["invoice"],
        traffic_gen.SYSTEM_PROMPTS["data-pull-sql"],
    ]
    with pytest.raises(ValueError, match="must provide"):
        traffic_gen.parse_mix("support-ticket=1,invoice=1")


def test_replay_accepts_one_family_for_the_sql_canary() -> None:
    systems: list[str] = []
    stats = traffic_gen.replay_requests(
        traffic_gen.build_requests(),
        base_url="http://proxy.test",
        rate=0,
        total=5,
        mix={"data-pull-sql": 1},
        sender=lambda _url, request: not systems.append(request["messages"][0]["content"]),
    )

    assert (stats.sent, stats.success, stats.failed) == (5, 5, 0)
    assert set(systems) == {traffic_gen.SYSTEM_PROMPTS["data-pull-sql"]}


def test_summary_groups_metadata_by_system_prompt_hash(tmp_path: Path) -> None:
    db = tmp_path / "traffic.db"
    assert record_traffic(
        TrafficRecord("2026-07-16T00:00:00Z", "hash-a", "vf-demo", 3, 2, 4.0, 0.00001), db_path=db
    )
    assert record_traffic(
        TrafficRecord("2026-07-16T00:00:01Z", "hash-a", "vf-demo", 5, 4, 5.0, 0.00002), db_path=db
    )
    assert record_traffic(
        TrafficRecord("2026-07-16T00:00:02Z", "hash-b", "vf-demo", 7, 1, 6.0, 0.00003), db_path=db
    )

    assert traffic_summary.summarize(db) == [
        ("hash-a", 2, 14, pytest.approx(0.00003)),
        ("hash-b", 1, 8, pytest.approx(0.00003)),
    ]
