# Frontend integration cheatsheet

Last verified: **2026-07-19 · v0.32.1** against the real FastAPI process and
Supabase Postgres. The Forge Agent was deterministic mock; autoprovision was
off, so the check made no paid LLM, GPU, or provider request.

## Read this first: current integration gaps

1. The completed D4 Job is served from committed demo artifacts in hybrid mode,
   not Supabase. Its real before/after and both curves are present, but the two
   requested report fields are currently absent:

   ```json
   {
     "arena": null,
     "projected_monthly_savings_usd": null
   }
   ```

   Observed values were before `0.5833333333333334`, after
   `0.7833333333333333`, 400 main-curve points, 200 control points, and verdict
   `real_gain`. There are zero arena samples, not ten. This self-check does not
   invent missing report data.
2. The prose contract names 19 HTTP operations; the frozen parity list contains
   16. The three additional implemented reads are listed separately below.
3. `/jobs` and the cluster catalog are deliberately mixed-source. Static
   product facts/artifacts are enriched with Supabase state; a 200 response is
   not proof that the entire payload originated in Postgres.
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

## Verified frozen operations

`Fields` means the response parsed through the public Pydantic contract and
contained the required keys. `Source check` means a matching Supabase row/value
was read directly where that claim applies.

| # | Method + path | HTTP | Fields | Source | Source check |
| ---: | --- | ---: | --- | --- | --- |
| 1 | `GET /jobs` | 200 | yes | mixed: artifacts + Supabase | yes |
| 2 | `POST /jobs` | 201 | yes | Supabase | yes |
| 3 | `GET /jobs/{job_id}` (new test Job) | 200 | yes | Supabase | yes |
| 4 | `GET /clusters` | 200 | yes; exactly 3 | static + Supabase | yes |
| 5 | `GET /clusters/data-pull-sql` | 200 | yes | static + Supabase | yes |
| 6 | `POST /clusters/data-pull-sql/agent/analyze` | 200 | yes | mock analysis + Supabase decision | yes |
| 7 | `POST /agent-decisions/{decision_id}/approvals` | 200 | yes | Supabase | yes |
| 8 | `POST /approvals/{approval_id}/start-forge` | 404 | explicit disabled body | flag gate; no persistence/provider | yes |
| 9 | `GET /approvals/{approval_id}/forge-execution` | 200 | yes | Supabase | yes |
| 10 | `GET /clusters/data-pull-sql/routing` | 200 | yes | Supabase | yes |
| 11 | `PUT /clusters/data-pull-sql/routing` | 200 | yes | Supabase | yes |
| 12 | `GET /clusters/data-pull-sql/live-pass-rate` | 200 | yes; 128 points | Supabase | yes |
| 13 | `GET /clusters/data-pull-sql/sample-source` | 200 | yes | Supabase | yes |
| 14 | `PUT /clusters/data-pull-sql/sample-source` | 200 | yes | local file identity + Supabase metadata | yes |
| 15 | `GET /settings/provider-credentials/nebius` | 200 | yes | Supabase | yes |
| 16 | `PUT /settings/provider-credentials/nebius` | 200 | yes; key never returned | Supabase | yes |

The three implemented but non-frozen reads also returned 200:

- `GET /jobs/d4-m3-1p5b-r1-v0125/metrics`
- `GET /clusters/data-pull-sql/agent/decision`
- `GET /agent-decisions/{decision_id}/approval`

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
| `VF_API_DATA_MODE` | launch override `hybrid` | D4 immutable artifacts coexist with Supabase product state and new Jobs. |
| `VF_CORS_ORIGINS` | unset/local default | Allows localhost and 127.0.0.1 on 3000, 5173, and 8080. Comma-separated values replace the list; only explicit `*` opens all origins. |

Analyze is advisory. Approve writes intent only. Start Forge is the only spend
boundary and remains disabled in this launch. Do not treat `POST /jobs` as a
training action; it only queues metadata.
