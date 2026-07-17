"""Replay zero-cost mixed product traffic through the local OpenAI-compatible proxy."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
import itertools
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.proxy.clusters import SYSTEM_PROMPTS_BY_CLUSTER


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FROZEN_TRAINING_POOL = REPOSITORY_ROOT / "data" / "nl2sql" / "v0.10.0-training-pool.jsonl"
FAMILIES = ("support-ticket", "invoice", "data-pull-sql")
FAMILY_CLUSTER_IDS = {
    "support-ticket": "support-ticket-extraction",
    "invoice": "invoice-field-extraction",
    "data-pull-sql": "data-pull-sql",
}
SYSTEM_PROMPTS = {family: SYSTEM_PROMPTS_BY_CLUSTER[cluster_id] for family, cluster_id in FAMILY_CLUSTER_IDS.items()}
SUPPORT_EMAILS = (
    "Order SO-1001 arrived with a cracked screen. Please replace it before Friday.",
    "I was charged twice for invoice INV-2002; refund the duplicate card charge.",
    "Cannot reset the password for account mia@example.test after the verification email expired.",
    "Our shipment TRK-4404 is missing; the carrier says it was delivered yesterday.",
    "Please cancel renewal for workspace Nimbus before the 30-day trial ends.",
    "The export button returns a 500 error for project Northstar; this blocks today's audit.",
    "I need an address change for order SO-1007 before it leaves the warehouse.",
    "My team lost SSO access after the domain change to example.test. Urgent incident.",
    "The annual plan invoice lists 18 seats but we purchased 15. Please correct it.",
    "Can you restore deleted report Q2-pipeline from yesterday's backup?",
    "Package SO-1011 contains the wrong color item and the event is tomorrow.",
    "Please explain why usage surged for account Acme-West between Monday and Tuesday.",
    "The mobile app loops on payment confirmation for order SO-1013.",
    "I need a VAT receipt for last month's subscription, account EU-44.",
    "Our webhook has stopped delivering events since 09:20 UTC; project Beacon is affected.",
    "The discount code SUMMER25 was accepted but the final invoice omitted it.",
    "Please transfer workspace ownership from former-admin@example.test to lina@example.test.",
    "A seat was removed by mistake from the Analytics group; restore user rao@example.test.",
    "Order SO-1019 has been pending fulfillment for six days with no status update.",
    "The API says rate limit exceeded even though our dashboard reports only 12 requests today.",
)
INVOICES = (
    "Invoice INV-3001 | Vendor: Atlas Supplies | Due: 2026-08-01 | USD 1,240.00",
    "Factura F-3002; proveedor Brisa Labs; vence 2026-08-02; EUR 980.50.",
    "Northwind Consulting / bill NW-3003 / due 2026-08-03 / total GBP 2,100.00",
    "Invoice: 3004, Vendor=Orchid Media, Payment due 2026-08-04, CAD $645.25",
    "Receipt INV-3005 from Summit Office; due 2026-08-05; USD 89.99",
    "Kite Logistics — invoice KL-3006 — 2026-08-06 — JPY 155000",
    "Vendor: Harbor Systems; invoice HS-3007; due date 2026-08-07; USD 4,500.00",
    "Invoice 3008 | Copper Works | 2026-08-08 | AUD 760.00",
    "Maple Studio invoice MS-3009, due 2026-08-09, EUR 1,320.40",
    "Billing statement INV-3010, River Cloud, payment due 2026-08-10, USD 199.00",
    "Pine Analytics / PA-3011 / due: 2026-08-11 / SEK 8,400.00",
    "Invoice INV-3012 — Lumen Travel — due 2026-08-12 — USD 3,045.70",
    "Quartz Hardware bill QH-3013 payable 2026-08-13 total CHF 1,110.00",
    "Vendor Ember Foods; invoice EF-3014; due 2026-08-14; USD 548.32",
    "Cedar Security, invoice CS-3015, due 2026-08-15, EUR 2,890.00",
    "Invoice FL-3016 from Fjord Labs; due 2026-08-16; NOK 13,500.00",
    "Granite Services | GS-3017 | payment date 2026-08-17 | USD 725.00",
    "Invoice INV-3018 / Indigo Print / due 2026-08-18 / CAD 402.19",
    "Juniper Health bill JH-3019; due 2026-08-19; USD 1,875.00",
    "Invoice 3020, Vendor Luna Design, due 2026-08-20, EUR 640.00",
)


@dataclass(frozen=True)
class TrafficStats:
    sent: int = 0
    success: int = 0
    failed: int = 0
    interrupted: bool = False


def build_requests(pool_path: Path = FROZEN_TRAINING_POOL) -> dict[str, list[dict[str, Any]]]:
    """Build the three D5 request families without modifying frozen source data."""
    sql_prompts = _frozen_sql_prompts(pool_path)
    return {
        "support-ticket": [_request("support-ticket", email) for email in SUPPORT_EMAILS],
        "invoice": [_request("invoice", invoice) for invoice in INVOICES],
        "data-pull-sql": [_request("data-pull-sql", prompt) for prompt in sql_prompts],
    }


def parse_mix(value: str) -> dict[str, int]:
    """Parse `support-ticket=1,invoice=1,data-pull-sql=1` into positive weights."""
    weights: dict[str, int] = {}
    for part in value.split(","):
        name, separator, raw_weight = part.strip().partition("=" if "=" in part else ":")
        if not separator or name not in FAMILIES or not raw_weight.strip():
            raise ValueError(f"invalid mix segment: {part!r}")
        try:
            weight = int(raw_weight)
        except ValueError as error:
            raise ValueError(f"invalid mix weight: {part!r}") from error
        if weight < 1:
            raise ValueError(f"mix weights must be positive: {part!r}")
        weights[name] = weight
    if set(weights) != set(FAMILIES):
        raise ValueError("mix must provide support-ticket, invoice, and data-pull-sql")
    return weights


def replay_requests(
    requests_by_family: Mapping[str, list[dict[str, Any]]],
    *,
    base_url: str,
    rate: float,
    total: int,
    mix: Mapping[str, int],
    sender: Callable[[str, Mapping[str, Any]], bool] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> TrafficStats:
    """Send a weighted cyclic stream; total=0 deliberately means run until interrupted."""
    if rate < 0 or total < 0:
        raise ValueError("rate and total must be non-negative")
    if any(not requests_by_family[name] for name in FAMILIES):
        raise ValueError("each traffic family needs at least one request")
    send = sender or _send
    sequence = list(itertools.chain.from_iterable(([name] * mix[name] for name in FAMILIES)))
    positions = {name: 0 for name in FAMILIES}
    sent = success = failed = 0
    started = clock()
    interrupted = False
    try:
        for name in itertools.cycle(sequence):
            if total and sent >= total:
                break
            request = requests_by_family[name][positions[name] % len(requests_by_family[name])]
            positions[name] += 1
            sent += 1
            if send(base_url, request):
                success += 1
            else:
                failed += 1
            if rate:
                delay = started + sent / rate - clock()
                if delay > 0:
                    sleeper(delay)
    except KeyboardInterrupt:
        interrupted = True
    return TrafficStats(sent=sent, success=success, failed=failed, interrupted=interrupted)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--rate", type=float, default=5.0, help="requests per second; zero is unpaced")
    parser.add_argument("--total", type=int, default=300, help="request count; zero runs until interrupted")
    parser.add_argument("--mix", default="support-ticket=1,invoice=1,data-pull-sql=1")
    args = parser.parse_args()
    try:
        stats = replay_requests(
            build_requests(),
            base_url=args.base_url,
            rate=args.rate,
            total=args.total,
            mix=parse_mix(args.mix),
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(asdict(stats), sort_keys=True))
    raise SystemExit(130 if stats.interrupted else 0)


def _request(family: str, content: str) -> dict[str, Any]:
    return {
        "model": "vf-demo",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPTS[family]},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
    }


def _frozen_sql_prompts(path: Path) -> list[str]:
    prompts: list[str] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            prompt = record.get("prompt")
            if isinstance(prompt, str) and prompt:
                prompts.append(prompt)
            if len(prompts) == 20:
                break
    if len(prompts) < 20:
        raise ValueError(f"frozen training pool needs at least 20 prompts: {path}")
    return prompts


def _send(base_url: str, payload: Mapping[str, Any]) -> bool:
    request = Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit operator-provided proxy URL.
            return 200 <= response.status < 300
    except (HTTPError, OSError, URLError):
        return False


if __name__ == "__main__":
    main()
