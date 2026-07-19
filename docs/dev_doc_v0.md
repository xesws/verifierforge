# VerifierForge v1 prototype — external development record

**Status:** evidence through Forge Agent, Supabase, public canary, and P-2
adapter implementation, 2026-07-19. The detailed operational ledger is
[`p0-run-sheet.md`](p0-run-sheet.md).

## Product thesis

VerifierForge is a controlled path from repeated production traffic to a
smaller task model. It separates five authorities:

1. traffic metadata establishes demand and economics;
2. a programmatic verifier defines success;
3. a bounded advisory Agent recommends `forge`, `skip`, or `need_more_data`;
4. a human approval authorizes a concrete configuration; and
5. held-out evidence—not the training curve—decides whether a model ships.

The proved vertical is NL→SQL. This is a credible narrow system, not a claim
that every task is verifiable or every small model should be fine-tuned.

## Architecture

```text
Client ──▶ OpenAI-compatible proxy ──▶ default / tuned upstream
                 │                           │
                 ├── traffic metadata       └── sampled guardian
                 ▼
              Discover
                 │ approved sample identity
                 ▼
       Forge Agent (read-only tools)
                 │ strict decision
                 ▼
          human approval record
                 │ separate execution seam
                 ▼
  provisioner ──▶ disposable verl/vLLM worker ──▶ Storage
                 │                                 │
                 └──────── audit / status ─────────┘

SQLite (local) or Supabase Postgres stores product facts.
S3 stores full Agent traces and durable training objects.
```

Pydantic v2 contracts protect product, Agent, provisioning and storage
boundaries. The proxy never needs prompt bodies for clustering: it records a
system-prompt hash, model, tokens, latency, estimated cost and selected route.

## Discover, Agent and approval

Data Pull SQL is presented as 95,000 queries/month and $5,500/month. Because
traffic storage is body-free, a user must confirm an approved repository sample
source. The server accepts only a repository-relative JSONL path, recomputes its
SHA-256 and row count, validates required NL→SQL fields, and stores identity
metadata. The current source is a frozen 50-row pool.

Forge Agent can call four tools: analyze traffic, inspect approved samples,
estimate economics, and check verifiability. Tools are read-only, use strict
input/output models and have deterministic mock bindings. The Runner enforces
turn/token/time limits, an ordered dependency chain, a closed action space and
strict terminal schema. It contains no provisioning/training handle.

Live Gate C used the provider-listed `gpt-5.6-luna` via OpenAI Responses. The
12-scenario final tuple was:

```text
decision_accuracy=1.0
chain_success_rate=1.0
illegal_action_count=0
config_legality_rate=1.0
```

The feature flag remains false by default. One real product Analyze with no
approved source returned `need_more_data` at confidence 0.99. After human source
approval, a fresh run returned `forge` at confidence 0.98 with the exact P2
profile: Qwen2.5-0.5B, 100 steps, k=8, checkpoint 50, RunPod, budget at most $5.
`Approve & Forge` then wrote an approval to Supabase. The web request did not
start a GPU.

## Verifier, freeze and training evidence

`NL2SQLVerifier` awards interpretable tiers for parseable SQL, successful
execution against the supplied in-memory SQLite schema, and exact result-set
match, with a length penalty. Candidate data is verifier-screened before the
three-piece freeze: 50 training rows, 60 non-overlapping held-out rows, and the
verifier source identity.

The main 1.5B GRPO run completed 400 steps; a 0.5B random-reward control ran 200
steps. Eight checkpoints were evaluated on held-out data, and step 350 won by
pass@1 (ties would choose the earlier step):

| Held-out metric (60 rows, k=8) | Before | Step 350 |
| --- | ---: | ---: |
| pass@1 | 0.5833 | 0.7833 |
| pass@8 | 0.7667 | 0.9000 |
| mixed fraction | 0.4667 | 0.4333 |

The committed `data/demo-artifacts/` bundle contains metrics, report, control
and per-file SHA identities, but no weights. `VF_API_DATA_MODE=artifacts`
serves those bytes through the real API.

## Disposable compute and Storage

The laptop owns development and orchestration. GPU nodes run detached tmux
jobs and can die. `LocalStorage` uses append-only JSONL and atomic directory
publication. `S3Storage` uploads immutable generations and publishes a manifest
last; readers ignore every generation lacking that manifest. A real bucket
round trip restored a checkpoint SHA, read 50 ordered metric records, and kept
an interrupted upload invisible.

The P-1 provisioner defines provider-neutral specs/status and a lifecycle from
REQUESTED through TERMINATED/FAILED. Tests cover budget, concurrency, runtime,
kill switch, orphan reap and audit fuses with a deterministic adapter.

P-2 adds a RunPod REST adapter and approval-driven executor. It manages only
pods whose name begins `vf-auto-` and whose owner marker matches; Blackwell is
blocked; S3 state and Git-bundle bootstrap keep pods disposable. Its live gold
pod reached SSH and was deleted, but RunPod returned no billing-history row
within the hard 15-minute gate. Therefore orphan and 100-step training proofs
did not run, and `provisioner-p2-live` is absent.

## Database and security

All product facts use async SQLAlchemy repositories: traffic, clusters,
routing, guardian/live points, jobs, Agent decisions, approvals, credentials
and provisioning audits. SQLite is the zero-config default. With
`VF_DB_BACKEND=postgres`, `SUPABASE_DB_URL` selects Supabase's pooler; there is
no silent fallback after a database error.

Alembic owns the schema. The idempotent legacy importer reconciled row counts
and canonical digests, and the product smoke passed against real Postgres.
Provider credentials use Fernet with environment-only `VF_CRED_KEY`; database
errors are sanitized; the CI secret scan rejects tracked environment files,
private keys and common credential shapes.

## Delivery evidence

The selected serviceable step-350 export passed vLLM load and one real
completion with the locked serving stack. RunPod's native port-8000 hostname
returned 404 because the pod registered only port 8888. An outbound Cloudflare
quick tunnel provided the bounded public proof instead:

- official SDK response: `SELECT name FROM users;`;
- 200 canary requests: 120 default / 80 tuned, 0 failures;
- 13 new Guardian points, final LivePassRate 0.85; and
- post-reset proof: 20 default / 0 tuned.

The tunnel was ephemeral. The stable reviewer path is the committed artifact
API, not an expired public hostname.

## Reproduce the safe review surface

```bash
python -m pip install -r requirements-app.txt
pytest -q
bash scripts/start_reviewer_sandbox.sh
```

Then inspect `http://127.0.0.1:8012/docs` and follow
[`JUDGES.md`](../JUDGES.md). No GPU, cloud credential, model weight, or paid LLM
request is needed.

## Explicit boundaries

- One NL→SQL task family is not a broad benchmark.
- Forge Agent is default-off and advisory; approval is mandatory.
- The public tunnel is not durable production hosting.
- P-2 is implemented but has not passed billing/orphan/full-training DoD.
- Product artifacts omit weights, credentials, raw traffic and paid-provider
  dependencies.
- Multi-node recovery, spot scheduling, Nebius, broad verifier templates and
  button-triggered P-4 execution remain future work.
