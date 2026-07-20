# Frontend integration cheatsheet

Last verified: **2026-07-20 · v0.33.0** from the fixed Railway public origin
against Supabase Postgres and the real tuned endpoint. The Forge Agent was
deterministic mock; autoprovision was off, so the check made no paid LLM, GPU,
or provider request.

## Read this first: intentional data split

1. The completed D4 Job is complete in all three public modes: before
   `0.5833333333333334`, after `0.7833333333333333`, 400/200 curve points,
   ten held-out arena samples, `$3,850` projected monthly savings, and verdict
   `real_gain`.
2. `artifacts` serves the frozen presentation, `hybrid` combines it with
   Supabase relationship facts, and `supabase` serves the deterministic
   `summary_json` projection. Artifacts/S3 remain authoritative if they differ.
3. The contract has exactly 19 documented, frozen, and real/mock-parity
   operations.
4. Cluster GETs currently materialize the static catalog into the repository,
   so they update catalog timestamps while reading. No product value changed in
   this check, but the route is not strictly read-only internally.

There were no unexpected HTTP errors. The expected disabled Start response was:

```json
{"detail":"Start Forge is disabled because VF_AUTOPROVISION=false"}
```

## Start the true API

Run from the repository root. `SUPABASE_DB_URL`, `VF_S3_BUCKET`, and the AWS
variables used for a newly generated Agent trace stay in ignored `.env`.

```bash
VF_DB_BACKEND=postgres \
VF_AGENT_ENABLED=true \
VF_AGENT_BINDING=mock \
VF_AUTOPROVISION=false \
VF_PROVISION_BINDING=mock \
VF_API_DATA_MODE=hybrid \
VF_PROXY_DB_PATH=./runs/frontend-unused.sqlite3 \
uvicorn app.api.main:app \
  --host 127.0.0.1 \
  --port 8010 \
  --env-file .env
```

Use this frontend base URL:

```text
http://127.0.0.1:8010
```

For the hosted reviewer, set exactly one frontend variable (no trailing slash):

```text
VITE_VF_API_BASE_URL=https://verifierforge-production.up.railway.app
```

The hosted origin requires HTTP Basic Auth on product requests; username is
`judge` and the invitation code is shared out-of-band. `GET /healthz` and CORS
preflights do not require credentials. A tuned-endpoint outage may change
health to `degraded`, but report reads and proxy fallback remain available.

Loopback routes require no authentication. Every JSON POST/PUT uses
`Content-Type: application/json`; GET requests need no special header. The
reviewer sandbox is different: it wraps the same API in HTTP Basic Auth and
requires `Authorization: Basic ...` except for `/healthz`.

Provider Settings PUT additionally requires a stable `VF_CRED_KEY` in the API
environment. Generate it without echoing it into a command or log, store it in
`.env`, and never rotate it while stored credentials must remain decryptable.

### Action request headers

| Action | Authentication on loopback | Required header |
| --- | --- | --- |
| `POST /jobs` | none | `Content-Type: application/json` |
| `PUT /clusters/{id}/sample-source` | none | `Content-Type: application/json` |
| `POST /clusters/{id}/agent/analyze` | none | `Content-Type: application/json` when a body is sent; body is optional |
| `POST /agent-decisions/{id}/approvals` | none | `Content-Type: application/json` |
| `POST /approvals/{id}/start-forge` | none | `Content-Type: application/json` |
| `PUT /clusters/{id}/routing` | none | `Content-Type: application/json` |
| `PUT /settings/provider-credentials/{provider}` | none | `Content-Type: application/json`; never retain or log the key in the browser |

All GET routes are header-free on loopback. When the same calls go through the
reviewer sandbox, add its Basic `Authorization` header to every row above.

## Frozen operations

`Fields` means the response parsed through the public Pydantic contract and
contained the required keys. `Source check` means a matching Supabase row/value
was read directly where that claim applies.

| # | Method + path | HTTP | Fields | Source | Source check |
| ---: | --- | ---: | --- | --- | --- |
| 1 | `GET /jobs` | 200 | yes | mixed: artifacts + Supabase | yes |
| 2 | `POST /jobs` | 201 | yes | Supabase | yes |
| 3 | `GET /jobs/{job_id}` (new test Job) | 200 | yes | Supabase | yes |
| 4 | `GET /jobs/{job_id}/metrics` | 200 | yes | artifacts or Supabase projection | yes |
| 5 | `GET /clusters` | 200 | yes; exactly 3 | static + Supabase | yes |
| 6 | `GET /clusters/data-pull-sql` | 200 | yes | static + Supabase | yes |
| 7 | `POST /clusters/data-pull-sql/agent/analyze` | 200 | yes | mock analysis + Supabase decision | yes |
| 8 | `GET /clusters/data-pull-sql/agent/decision` | 200 | yes | Supabase | yes |
| 9 | `POST /agent-decisions/{decision_id}/approvals` | 200 | yes | Supabase | yes |
| 10 | `GET /agent-decisions/{decision_id}/approval` | 200 | yes | Supabase | yes |
| 11 | `POST /approvals/{approval_id}/start-forge` | 404 | explicit disabled body | flag gate; no provider action | yes |
| 12 | `GET /approvals/{approval_id}/forge-execution` | 200 | yes | Supabase | yes |
| 13 | `GET /clusters/data-pull-sql/routing` | 200 | yes | Supabase | yes |
| 14 | `PUT /clusters/data-pull-sql/routing` | 200 | yes | Supabase | yes |
| 15 | `GET /clusters/data-pull-sql/live-pass-rate` | 200 | yes; real points | Supabase | yes |
| 16 | `GET /clusters/data-pull-sql/sample-source` | 200 | yes | Supabase | yes |
| 17 | `PUT /clusters/data-pull-sql/sample-source` | 200 | yes | local identity + Supabase metadata | yes |
| 18 | `GET /settings/provider-credentials/nebius` | 200 | yes | Supabase | yes |
| 19 | `PUT /settings/provider-credentials/nebius` | 200 | yes; key never returned | Supabase | yes |

All scoped self-check Job, credential, and approval rows were removed. Routing
and sample-source product values were restored, and
`runs/frontend-unused.sqlite3` was never created.

## Current true data snapshot

| Cluster | Monthly calls | Monthly cost | Analyzer decision |
| --- | ---: | ---: | --- |
| `support-ticket-extraction` | 240,000 | $4,800 | `skip` |
| `invoice-field-extraction` | 180,000 | $6,000 | `forge` |
| `data-pull-sql` | 95,000 | $5,500 | `forge` |

The SQL route read back as enabled with canary `50`, target `tuned`. Guardian
returned 128 real Supabase points; the latest rolling pass rate was `0.85`.

## Flags and frontend-visible behavior

| Variable | Current/effective value | Effect |
| --- | --- | --- |
| `VF_DB_BACKEND` | `.env`: `postgres` | Repositories require `SUPABASE_DB_URL`; there is no silent SQLite fallback. |
| `VF_AGENT_ENABLED` | `.env`: `true` | Exposes Analyze, decision, approval, sample-source, and Discover routes. |
| `VF_AUTOPROVISION` | unset/default `false` | Start Forge returns the explicit 404 above and cannot spend. |
| `VF_AGENT_BINDING` | launch override `mock` | Analyze is deterministic and makes no LLM request; decision metadata still lands in Supabase. |
| `VF_API_DATA_MODE` | default `hybrid` | Public modes are `artifacts`, `hybrid`, and `supabase`; `runs` is deprecated local compatibility. |
| `VF_CORS_ORIGINS` | unset/local default | Allows localhost and 127.0.0.1 on 3000, 5173, and 8080. Comma-separated values replace the list; only explicit `*` opens all origins. |

Analyze is advisory. Approve writes intent only. Start Forge is the only spend
boundary and remains disabled in this launch. Do not treat `POST /jobs` as a
training action; it only queues metadata.

Public acceptance repeated all 19 operations. The flagship report contained
400 main and 200 control points, ten held-out arena samples, `$3,850` projected
savings, and `real_gain`. Twelve tuned requests succeeded and Guardian added a
new point; the SQL route was restored to its prior 50% state afterward.
