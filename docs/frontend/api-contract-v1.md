# Frontend API Contract v1

**Coverage:** **19 documented = 19 frozen = 19 real/mock parity**
**Frozen:** v0.32.3 · 2026-07-20 · tags `frontend-api-v1`, `frontend-api-v1.1`

This is the additive integration boundary for the Monday frontend. JSON field
names and meanings below are frozen. The real API and `mock/server.py` use the
same Pydantic response models for every listed endpoint. Unknown response
fields must be ignored so later additions remain compatible.

## Transport and authentication

Loopback API calls require no authentication. The reviewer sandbox wraps the
entire API, proxy and demo page in HTTP Basic Auth. Use username `judge` and the
separately shared invite code as the password:

```http
Authorization: Basic <base64("judge:<invite-code>")>
```

Never put the invite code, provider key or database URL in source or browser
logs. `/healthz` is the sandbox's only unauthenticated route.

## Discover clusters

### `GET /clusters`

Returns `Cluster[]`. `GET /clusters/{cluster_id}` returns the same shape for
one cluster. `routing`, `live_pass_rate`, `approved_sample_source`, and
`analyzer_decision` are nullable until their respective workflow has run.

```json
[
  {
    "cluster_id": "data-pull-sql",
    "name": "Data pull SQL",
    "monthly_calls": 95000,
    "monthly_cost_usd": 5500.0,
    "trainable": true,
    "status": "discovered",
    "job_id": null,
    "routing": {
      "cluster_id": "data-pull-sql",
      "enabled": true,
      "canary_percent": 50,
      "target_model": "tuned"
    },
    "live_pass_rate": {
      "cluster_id": "data-pull-sql",
      "points": [{"timestamp": "2026-07-19T12:00:00Z", "pass_rate": 0.85}]
    },
    "approved_sample_source": {
      "kind": "repository_jsonl",
      "uri": "data/nl2sql/v0.10.0-training-pool.jsonl",
      "sha256": "c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c",
      "row_count": 50,
      "approved_by": "owner",
      "approved_at": "2026-07-19T12:00:00Z"
    },
    "analyzer_decision": {
      "decision": "forge",
      "rationale": "Deterministic verification and positive payback support a forge.",
      "confidence": 0.98,
      "config": {
        "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
        "steps": 400,
        "k": 8,
        "checkpoint_interval": 50,
        "budget_usd_cap": 5.0,
        "provider_pref": "runpod"
      }
    }
  }
]
```

`status` is `discovered | forging | live`. Monthly volume/cost are stable
product facts, not extrapolations from the latest short traffic sample.

### Sample source (the Input step)

`GET /clusters/{cluster_id}/sample-source` returns the nullable
`approved_sample_source`. To approve a repository JSONL source:

```http
PUT /clusters/data-pull-sql/sample-source
Content-Type: application/json

{
  "uri": "data/nl2sql/v0.10.0-training-pool.jsonl",
  "approved_by": "owner",
  "expected_sha256": "c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c",
  "expected_row_count": 50
}
```

The server recomputes byte identity; a mismatch is HTTP 422. The response is
the `approved_sample_source` object shown above.

## Analyze → Approve → Start Forge

These are deliberately three separate actions. Analyze is advisory; Approve
writes human intent only; only Start Forge may reach a paid provider.

### 1. Analyze

```http
POST /clusters/data-pull-sql/agent/analyze
Content-Type: application/json

{
  "data_source": "app/proxy/traffic.db",
  "execution_profile": "standard",
  "force_refresh": false
}
```

The body is optional. Response:

```json
{
  "decision_id": "decision-01",
  "cluster_id": "data-pull-sql",
  "decision": {
    "decision": "forge",
    "rationale": "Deterministic verification and positive payback support a forge.",
    "confidence": 0.98,
    "config": {
      "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
      "steps": 400,
      "k": 8,
      "checkpoint_interval": 50,
      "budget_usd_cap": 5.0,
      "provider_pref": "runpod"
    }
  },
  "cached": false,
  "created_at": "2026-07-19T12:00:00Z"
}
```

Decision values are `forge | skip | need_more_data`; only `forge` includes a
config. Reload the latest result with
`GET /clusters/{cluster_id}/agent/decision`.

### 2. Approve & Forge (no spend)

```http
POST /agent-decisions/decision-01/approvals
Content-Type: application/json

{"approved_by": "owner"}
```

```json
{
  "approval_id": "approval-01",
  "decision_id": "decision-01",
  "approved_by": "owner",
  "approved_at": "2026-07-19T12:01:00Z"
}
```

Reload with `GET /agent-decisions/{decision_id}/approval`. This endpoint never
provisions a GPU.

### 3. Start Forge (spend boundary)

```http
POST /approvals/approval-01/start-forge
Content-Type: application/json

{"requested_by": "owner", "confirm_provider_spend": true}
```

The requester must match the approver and `confirm_provider_spend` must be the
literal boolean `true`. Initial response:

```json
{
  "approval_id": "approval-01",
  "decision_id": "decision-01",
  "job_id": "forge-01",
  "provider": "runpod",
  "state": "provisioning",
  "budget_usd_cap": 5.0,
  "cost_accrued_usd": 0.0,
  "provision_handle": null,
  "credential_source": "stored",
  "detail": "Provision requested",
  "created_at": "2026-07-19T12:02:00Z",
  "updated_at": "2026-07-19T12:02:00Z"
}
```

Poll `GET /approvals/{approval_id}/forge-execution`. States are `approved →
provisioning → running → collecting → done`, or `failed`. The effective budget
is the lower of Agent config and system cap.

## Provider Settings (BYO key)

Write a key with `PUT /settings/provider-credentials/{provider}`:

```json
{"user_id": "owner", "api_key": "<write-only>"}
```

Read only status with
`GET /settings/provider-credentials/{provider}?user_id=owner`:

```json
{
  "user_id": "owner",
  "provider": "runpod",
  "configured": true,
  "source": "stored",
  "credential_id": "credential-01",
  "updated_at": "2026-07-19T12:00:00Z"
}
```

The key is Fernet-encrypted and is never returned. `source` is `stored |
system_env | missing`. `system_env` is the local/reviewer fallback only.

## Jobs and reports

### `POST /jobs`

This queues metadata only; it is not an alias for Start Forge.

```json
{
  "template": "nl2sql",
  "model": "Qwen/Qwen2.5-1.5B-Instruct"
}
```

HTTP 201 returns the `Job` shape with `status: "queued"`, empty curves and
null report/endpoint. `GET /jobs` returns summaries such as
`[{"job_id":"job-01","status":"queued"}]`.

### `GET /jobs/{job_id}`

The normal four UI states are `queued | running | done | failed`;
`early_stopped` is also a supported terminal state. A completed response:

```json
{
  "job_id": "d4-m3-1p5b-r1-v0125",
  "template": "nl2sql",
  "status": "done",
  "model": "Qwen/Qwen2.5-1.5B-Instruct",
  "created_at": "2026-07-16T00:00:00Z",
  "metrics": {
    "steps": [1, 50, 100, 350, 400],
    "reward_mean": [0.18, 0.39, 0.51, 0.78, 0.79],
    "pass_at_1": [0.20, 0.45, 0.58, 0.7833, 0.77],
    "entropy": [1.40, 1.12, 0.95, 0.72, 0.70]
  },
  "control": {"pass_at_1": [0.18, 0.19, 0.20]},
  "report": {
    "baseline_pass_at_1": 0.5833,
    "final_pass_at_1": 0.7833,
    "control_final_pass_at_1": 0.20,
    "verdict": "real_gain",
    "narrative": "Held-out pass@1 improved beyond the random-reward control.",
    "projected_monthly_savings_usd": 3850.0,
    "arena": {
      "win_rate": 0.20,
      "samples": [
        {
          "prompt": "What is the name of the department based in New York?",
          "baseline_output": "SELECT ... JOIN employees ...",
          "tuned_output": "SELECT name FROM departments WHERE location = 'New York'",
          "baseline_score": 0.5,
          "tuned_score": 1.0
        }
      ]
    },
    "savings_projection": {
      "current_monthly_cost_usd": 5500.0,
      "projected_monthly_cost_usd": 1650.0,
      "projected_monthly_savings_usd": 3850.0,
      "formula": "projected_monthly_savings_usd = current_monthly_cost_usd - (current_monthly_cost_usd * 0.30)",
      "assumptions": [
        "Current cost is the Discover product fact for 95,000 calls.",
        "Tuned inference is estimated at 30% of recurring workflow cost.",
        "One-time training and provisioning costs are excluded."
      ]
    },
    "provenance": {
      "artifact_version": "v0.32.3",
      "s3_prefix": null,
      "generated_at": "2026-07-17T05:34:13.189221Z",
      "content_sha256": "2d5d919148e3f9cb54972b23cadaebc57d0449061bfe924e942b59020a7b5326",
      "sources": [
        {"path": "data/nl2sql/v0.10.0-heldout.jsonl", "sha256": "482f0e7678e7603311f72aeead381364cd92f0596c20745cc58c96916a9177e8"}
      ]
    }
  },
  "endpoint": {
    "base_url": "https://model.example/v1",
    "model_name": "verifierforge-step-350"
  }
}
```

`metrics` is the real training curve; `control.pass_at_1` is the spurious
random-reward comparison. Before/after quality comes only from held-out
evaluation. `verdict` is `real_gain | suspect_formatting | collapsed`.
`GET /jobs/{job_id}/metrics` returns only the `metrics` object.

The real payload contains exactly ten arena samples: six deterministic
baseline-fail/tuned-pass examples, two both-pass examples, and two both-fail
examples. The JSON above shows one card to keep the contract readable.

## Routing and Guardian

`GET /clusters/{cluster_id}/routing` returns `RoutingState`. Update it with:

```http
PUT /clusters/data-pull-sql/routing
Content-Type: application/json

{
  "cluster_id": "data-pull-sql",
  "enabled": true,
  "canary_percent": 50,
  "target_model": "tuned"
}
```

`canary_percent` is an integer from 0 to 100. Zero guarantees no tuned route.
`GET /clusters/{cluster_id}/live-pass-rate` returns:

```json
{
  "cluster_id": "data-pull-sql",
  "points": [
    {"timestamp": "2026-07-19T12:00:00Z", "pass_rate": 0.85}
  ]
}
```

Guardian scoring is asynchronous and never blocks proxy completions.

## Local launch matrix

Mock API (deterministic, no database/provider/LLM spend):

```bash
VF_AGENT_ENABLED=true VF_AUTOPROVISION=true python mock/server.py
```

True API with safe local defaults:

```bash
VF_AGENT_ENABLED=true \
VF_AGENT_BINDING=mock \
VF_PROXY_DB_PATH=./runs/frontend-v1.sqlite3 \
uvicorn app.api.main:app --host 127.0.0.1 --port 8010
```

Full reviewer composition:

```bash
bash scripts/start_reviewer_sandbox.sh --mode full
```

| Variable | Default | Effect |
| --- | --- | --- |
| `VF_AGENT_ENABLED` | `false` | Hides Analyze/Approve/decision APIs and UI when off. |
| `VF_AGENT_BINDING` | `real` | Use `mock` for deterministic zero-cost evaluation. |
| `VF_DB_BACKEND` | `sqlite` | Set `postgres` to require `SUPABASE_DB_URL`; no silent fallback. |
| `VF_AUTOPROVISION` | `false` | Hides Start Forge when off; approval remains available. |
| `VF_PROVISION_BINDING` | `mock` | Provider adapter used only after Start Forge. |
| `VF_API_DATA_MODE` | `hybrid` | `artifacts` is immutable, `hybrid` combines authoritative artifacts with relational facts, and `supabase` reads only derived DB projections. Legacy `runs` is internal compatibility only. |

The automated parity suite validates shared contract parsing and real/mock
response keys for jobs, clusters, Settings and Start Forge. OpenAPI remains
available at `/docs` and `/openapi.json` for generated frontend clients.

## Internal and debug routes — frontend must not use

These routes are intentionally outside the 19-operation frozen contract:

- `GET /discover` — FastAPI-hosted demonstration page.
- `POST /copilot/nl2sql/proposals` and `POST /copilot/nl2sql/validate` —
  verifier-authoring/internal workflow.
- `GET /docs`, `GET /openapi.json`, and reviewer `GET /healthz` — operational
  discovery and health surfaces.

They may change without a frontend contract revision. Product code must use
the 19 frozen operations above.
