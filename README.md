<p align="center">
  <img src="assets/brand/verifierforge-wordmark.svg" alt="VerifierForge" width="520" />
</p>

<p align="center"><strong>Turn repetitive, verifiable LLM work into an evidence-backed small-model forge.</strong></p>

<p align="center">
  <img alt="tests passing" src="https://img.shields.io/badge/tests-passing-00a67e" />
  <img alt="version v0.36.1" src="https://img.shields.io/badge/version-v0.36.1-087cf0" />
  <img alt="Python 3.11" src="https://img.shields.io/badge/python-3.11-17212b" />
</p>

# VerifierForge

VerifierForge turns production traffic with a programmatic success criterion
into an evidence-backed small-model forge: Discover identifies a candidate,
an audited Agent recommends whether to train, a human approves, and a
disposable GPU path publishes results through durable storage.

The v1 prototype is deliberately narrow and inspectable. NL→SQL is the proved
vertical; the verifier is the source of truth; held-out data selects the model;
and routing remains reversible.

## Technical Deep Dive

The full engineering account is available as a public, invitation-free
[visual article](https://verifierforge-web.vercel.app/tech) and as the
[versioned Markdown source](docs/blog/technical-deep-dive.md). It covers:

- the executable NL→SQL verifier, GRPO group-relative update, and gate A;
- the M3 quality run versus its deliberately imperfect random-reward
  falsification reference;
- eight-checkpoint held-out selection and why step 350 shipped;
- the strict-schema Forge Agent and Gate C evaluator; and
- disposable S3 workers, Supabase facts, capacity-aware provisioning, and
  scale-to-zero serving—with limitations kept beside the numbers.

## What is proved in this repository

The committed demo artifacts preserve the completed NL→SQL D4 result without
shipping model weights:

| Measurement | Before | Selected step-350 after |
| --- | ---: | ---: |
| Held-out pass@1 (60 rows) | 0.5833 | 0.7833 |
| Held-out pass@8 | 0.7667 | 0.9000 |
| Mixed fraction | 0.4667 | 0.4333 |

The 0.5B random-reward control curve is included beside the main curve. It is a
falsification reference, not proof that one training run establishes a general
causal claim.

Other completed gates are equally explicit:

| Layer | Current evidence |
| --- | --- |
| Forge Agent | Live 12-scenario Gate C on `gpt-5.6-luna`: decision `1.0`, chain `1.0`, illegal actions `0`, config legality `1.0`; feature flag remains off by default. |
| Product decision | A source-less production Analyze returned `need_more_data`; after a human-approved 50-row source, a fresh run returned `forge` at confidence `0.98` and created an approval in Supabase. |
| Database | SQLite remains local default; the same async SQLAlchemy repositories and Alembic schema passed a real Supabase Postgres migration, reconciliation, and product smoke. |
| Delivery | The reviewer API/proxy is a fixed Railway control plane; tuned GPU inference is now scale-to-zero. The frozen frontend boundary covers 22 operations, including a tuned-only reviewer probe that cannot mutate canary routing or fall back. Generated SQL can then run live in an ephemeral browser SQLite/WASM database against the frozen fixture—no canned rows, backend execution, GPU, or second model call. Two RunPod wake cycles reached ready in 282.14s and 266.68s, served real traffic, then idled to provider-inventory zero. The live 200-request proof split 111 default / 89 tuned with no fallback and Guardian `0.95`. |
| Provisioning | P-1 mock lifecycle/fuses pass. P-2 executed an approved 0.5B/100-step S3 run and deleted the pod. P-4 then proved the separate web approval → explicit Start Forge → real RunPod readiness → delete wiring. Before every allocation, RunPod live capacity is queried, approved offers are price-ranked with bounded fallback, and the chosen GPU/rate is audited; the live proof selected RTX 4000 Ada at `$0.20/hr` and deleted it immediately. |

## Architecture

```text
Vercel frontend ──▶ Railway reviewer API + proxy ──▶ on-demand GPU vLLM
       │
       └── browser SQLite/WASM ──▶ frozen synthetic demo rows
                           │          │                  ▲
                           │          └── guardian       │ wake/idle reap
                           │                             │
                           ├── serving registry ─────────┘
                           ▼
                Supabase facts + S3 evidence
                           │
                           ▼
 Forge Agent (read-only) ──▶ human approval ──▶ explicit Start Forge
                                                    │
                                                    ▼
                                      disposable training executor
```

Pydantic contracts sit at each boundary. Product metadata, decisions,
approvals, routing, and audit events use one repository layer. Full Agent
traces and training objects remain in S3. A GPU worker is an executor, never a
source of truth.

The deployable reviewer image is intentionally separate from GPU execution. It
is a single non-root Uvicorn service with no torch, vLLM, verl, Ray, or
Transformers dependency. An invite-protected wake allocates one capacity-aware
serving GPU, verifies the frozen S3 model, starts vLLM, and publishes its
ephemeral endpoint into Supabase; an idle reaper deletes it again.
The browser sets `VITE_VF_API_BASE_URL` to the reviewer origin, so a backend
rollback or tuned-endpoint rotation does not require a frontend rebuild unless
the reviewer origin itself changes.

### Data ownership and API read modes

The mixed backend is deliberate. Supabase owns relational facts: traffic,
clusters, routing, Guardian points, the Jobs ledger, Agent decisions, and
approvals. Artifacts/S3 own immutable curves, raw held-out arena evidence, and
evidence bundles. The full Job stored in `jobs.summary_json` is a deterministic
presentation projection with source hashes; it is never hand-authored. If a
projection conflicts with artifacts/S3, the artifact evidence wins and the
projection is rebuilt.

`VF_API_DATA_MODE=hybrid` is the default reviewer/product mode. `artifacts` is
the immutable offline reviewer mode, while `supabase` proves the relational
projection can serve the same report without local files. Legacy `runs` remains
only as a temporary local fake-trainer compatibility mode.

## Product workflow

1. The proxy records hashes and usage metadata, not prompt bodies, then groups
   a stable task cluster in Discover.
2. A user confirms a repository sample source; the server recomputes its path,
   row count and SHA-256.
3. Forge Agent calls read-only traffic, sample, economics and verifiability
   tools. Its only terminal actions are `forge`, `skip`, or `need_more_data`.
4. `Approve & Forge` writes durable human intent. It does not hide a GPU side
   effect inside the web request.
5. A separate `Start Forge` action requires a second literal confirmation,
   applies the lower config/system budget cap, and exposes provisioning status.
6. The training path freezes data/verifier identity, runs the main job and a
   random-reward control, and selects only on held-out evidence.
7. The proxy canaries the tuned endpoint while a non-blocking guardian scores
   sampled SQL output; setting canary to zero restores the default path.
8. In Ship, a reviewer can explicitly execute the exact generated SQL in a
   fresh browser-side SQLite database and inspect real rows or the real SQLite
   error. This execution is separate from model generation and can continue
   after the GPU returns to cold.

## Quickstart

Install the lightweight local dependencies and run the test suite:

```bash
python -m pip install -r requirements-app.txt -r requirements-trainer.txt
pytest -q
```

Start the reviewer-safe artifact API and deterministic fake proxy without a
GPU, cloud account, or model-provider request:

```bash
bash scripts/start_reviewer_sandbox.sh
curl http://127.0.0.1:8012/jobs
curl http://127.0.0.1:8012/jobs/d4-m3-1p5b-r1-v0125/metrics
```

Open `http://127.0.0.1:8012/docs` for the API. The optional mock Agent demo is
documented in [JUDGES.md](JUDGES.md); it uses the real Discover UI and stores a
decision/approval locally without a paid call.

The fixed product frontend is
`https://verifierforge-web.vercel.app`; it calls the Railway API at
`https://verifierforge-production.up.railway.app`. Product paths require the
separately shared Basic Auth invitation. Training autoprovision remains off.
Serving wake has its own explicit confirmation, one-session concurrency limit,
`$5` cap, and idle reaper; report and arena evidence do not require a live GPU.

Owners can expose the full product composition—Supabase repositories, the
configured real tuned endpoint, mock Agent, mock provisioner and Guardian—via
an authenticated Cloudflare quick tunnel:

```bash
bash scripts/start_reviewer_sandbox.sh --mode full
```

The launcher prints the ephemeral URL and the path to a `0600`, ignored invite
code file. Reviewers authenticate as Basic Auth user `judge`; the code must be
shared separately. Quick tunnels are temporary evidence, not production
hosting. `VF_AUTOPROVISION` is enabled only inside this launcher together with
`VF_PROVISION_BINDING=mock`, so Start Forge cannot create a paid resource.

For permanent reviewer hosting, build the root `Dockerfile` and run
`scripts/start_hosted_backend.sh`. The hosted service uses Supabase, S3,
invitation auth, a Gate-C-qualified live Forge Agent, a disabled-by-default
training provisioner, and the dynamic serving registry. Discover exposes the
provider/model, unique trace ID, timestamps, token counts, exact validated
decision JSON, and persisted read-only tool trace; mock or cached receipts are
labelled explicitly. Deployment and inference rollback are documented
in
[docs/infrastructure/v0.33.0-hosted-backend.md](docs/infrastructure/v0.33.0-hosted-backend.md).

## Engineering boundaries

The training control plane detaches jobs in tmux, records process groups for
kill/recovery, and keeps checkpoint publication separate from transient verl
staging. S3 uses immutable generations plus a manifest-last boundary; a real
bucket proof restored a checkpoint SHA, 50 ordered metrics, and kept an
interrupted upload invisible.

Forge Agent is advisory. It has bounded turns/tokens/time, read-only tools,
strict structured submission, and no provisioning or training handle. Gate C
passed, but `VF_AGENT_ENABLED` stays false unless an operator opts in. The web
approval remains a database write. `Start Forge` is a separate endpoint behind
the stricter default-off `VF_AUTOPROVISION` flag; approval alone never spends.

Serving uses the same disposable-node discipline as training but a separate
state machine: `cold → provisioning → loading → ready → draining → cold`.
The pod receives presigned model objects, not AWS credentials, and must prove
all 13 file hashes, the canonical tree, `/v1/models`, and one completion before
the registry becomes ready. A cold or failed endpoint falls back without
breaking the static flagship report.

The Ship SQL runner has a smaller trust boundary: `sql.js` runs in a Web
Worker, creates a fresh in-memory database for each click, loads the frozen
synthetic schema/fixture, enforces one read-only query, caps output, and kills
a query that exceeds two seconds. It does not call Railway or Supabase and it
does not consult frozen reference answers. A successful execution therefore
means “this SQL ran and returned these rows,” not “the query is semantically
correct.”

## Database operations

SQLite remains the safe local default. Production Postgres is explicit and
fail-closed: set `VF_DB_BACKEND=postgres` and provide a Supabase pooler DSN in
`SUPABASE_DB_URL`; the application never falls back to SQLite after a database
error. Apply schema changes and inspect current revision with:

```bash
alembic upgrade head
alembic current
```

Before a migration, take a provider-managed backup or run `pg_dump` using the
environment-only DSN. Restore local service explicitly with
`VF_DB_BACKEND=sqlite VF_PROXY_DB_PATH=./runs/fallback.sqlite3`; this is an
operator decision, not an automatic failover. Postgres pool defaults are 5
connections + 5 overflow with 10-second pool/connect timeouts, overridable via
`VF_DB_POOL_SIZE`, `VF_DB_MAX_OVERFLOW`, `VF_DB_POOL_TIMEOUT_SECONDS`, and
`VF_DB_CONNECT_TIMEOUT_SECONDS`.

Provider credentials require a Fernet key and are ciphertext-only in the
repository. Generate a key once, store it in the deployment secret manager as
`VF_CRED_KEY`, and never commit it:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -m scripts.scan_secrets
```

The Settings API stores a user's RunPod key only as Fernet ciphertext and never
returns it. Decryption happens afresh at each provider HTTP call. For local and
reviewer demos only, a user without a stored credential may fall back to the
system process's `.env` `RUNPOD_API_KEY`; production deployments should supply
BYO credentials through Settings and keep that fallback unset.

## Limitations

- The demonstrated quality result is one NL→SQL task family with 50 training
  rows and a 60-row held-out set; it is not a broad benchmark claim.
- The reviewer uses a fixed Railway subdomain, but each on-demand GPU session
  still uses an ephemeral Cloudflare quick tunnel rather than a serving SLA.
  The reviewer degrades to artifact reports and deterministic proxy fallback
  while inference is cold or unavailable.
- P-4 proves real approval/Start/provision/delete wiring, not a second complete
  training run from the web. `VF_AUTOPROVISION` remains default-off. The P-4
  smoke estimate was `$0.000623`; final provider billing remains asynchronous.
- The provider-neutral seam is ready for another adapter, but
  `NebiusAdapter` is roadmap-only; RunPod is the only live implementation.
- Agent Gate C covers a frozen 12-scenario evaluator. It is not evidence that
  arbitrary business traffic should auto-train; the flag remains default-off
  and approval is required.
- Demo artifacts exclude weights, checkpoints, credentials, raw traffic, and
  any paid-provider dependency.
- The live SQL runner intentionally targets the public frozen demo dataset.
  The production roadmap is a separately governed connector to a customer's
  read-only data-warehouse replica; v0.35.4 does not accept database URLs or
  customer credentials.


## How we worked with Codex

### 2026-07-18–19 — v0.18.0 through v0.28.5 product/infrastructure log

1. **The product had evidence but no decision layer.** The human specified an
   advisory Forge Agent with read-only tools, strict action space, a live Gate
   C, and a default-off flag. Codex implemented the Responses-based tool loop,
   discovered the valid Luna model from `/v1/models`, diagnosed the first Gate
   C failures by scenario, and changed the general evidence hierarchy and
   dependency binding rather than the thresholds. The final live tuple was
   `1.0 / 1.0 / 0 / 1.0`; tag `agent-gate-c-pass` records it. A later real
   product run first requested more data, then—after human approval of a
   50-row source—recommended the exact P2 forge profile at confidence `0.98`.

2. **SQLite-specific persistence had become the product foundation.** The
   human chose Supabase/Postgres and required the default switch to remain an
   owner action. Codex extracted async SQLAlchemy repositories, created the
   Alembic schema and idempotent importer, then diagnosed test-time `.env`
   mutation that accidentally leaked Postgres into SQLite fixtures. After
   isolating settings, the migration, row/digest reconciliation and product
   smoke passed; tags `db-1-complete`, `db-2-complete`, and `db-3-complete`
   separate repository, cutover, and credential-hardening judgments.

3. **A model that worked locally still lacked a public delivery proof.** The
   RunPod-native port-8000 hostname returned 404 because the pod exposed only
   8888. The human required a real public request and reversible canary; Codex
   chose a Cloudflare quick tunnel as an explicit ephemeral fallback. The
   official SDK returned `SELECT name FROM users;`; 200 requests split 120/80,
   Guardian ended at `0.85`, and canary zero produced 20/0. This proves the
   traffic path, not a permanent hosting SLA.

4. **Real provisioning needed a fail-closed receipt.** The human initially set
   `$5`, 180-minute and cleanup limits; after RunPod billing lagged, the human
   corrected teardown proof to accepted DELETE plus target absence and raw
   prefix zero, with billing sampled asynchronously. Codex implemented that
   rule, the orphan proof and approval-driven S3 executor. Four training tries
   exposed a same-GPU ordering bug at step 50. The human decided that vLLM must
   run only after trainer exit; Codex implemented candidate manifests plus a
   separate finalizer. Attempt five completed 100 steps, passed models and real
   completion checks, SHA-collected 137 objects, deleted in 4.035 seconds and
   created `provisioner-p2-live`. The `$0.177846` run estimate is not presented
   as settled billing.

### 2026-07-14 — v0.2.0 / v0.3.0 infrastructure log

1. **Laptop/GPU split — problem:** a rented GPU pod can disappear, so it could not be the owner of the development session or durable training state. **Diagnosis:** the human established that the laptop holds the main Codex session and acts as the development host; RunPod is a stateless SSH-driven compute executor. All persistent training state lives on the `/workspace` network volume and crosses a worker lifetime through the pluggable `Storage` contract. **Decision and ownership:** this architecture and its failure assumption came from the human specification. Codex implemented the corresponding control plane in `a41cc0c` (`scripts/vf` subcommands `bootstrap`, `train`, `watch`, `logs`, `status`, `kill`, and `model`) and the tmux-detach discipline, so a job survives the initiating SSH connection rather than the pod being treated as a long-lived workstation.

2. **CUDA dependency conflict — problem:** the initial v0.3 pin in `8a6c9a3` used `vllm==0.25.1`. **Diagnosis:** it installed but failed to import with `ImportError: libcudart.so.13: cannot open shared object file`; its CUDA 13 runtime did not match the L4's CUDA 12.8 environment, and 0.25.1 was outside verl 0.8's declared vLLM range (`>=0.8.5, <=0.12.0`). Codex retained the failed environment and log instead of hiding the evidence. **Decision and ownership:** the human set a 45-minute timebox before the attempt so dependency debugging could not consume the day. Within that limit, Codex made the compatibility judgment to replace the pin in `5488578` with `vllm==0.10.2` and the pinned `verl[vllm]` v0.8 source revision. The detached `vf-runtime-install-v2` session completed in about 13 minutes; the verified stack was `torch 2.8.0+cu128`, `vllm 0.10.2`, `verl 0.8.0`, and `ray 2.56.0` with CUDA available. `f34fcd6` records the outcome.

3. **SSH key permissions on the network volume — problem:** `/workspace` exposed the persisted deploy private key as mode `0666`, which OpenSSH rejects as too permissive. **Diagnosis:** a direct key use failed because the network volume does not preserve enforceable POSIX private-key permissions, so a naive `chmod 600 /workspace/.ssh/id_ed25519` would not stick across the volume boundary/restart. **Decision and ownership:** persisting the read-only deploy identity under `/workspace` followed the human requirement that Pod state survive restart; Codex chose the secure bridge in `523d7e8`: before any Git clone or pull, `scripts/vf` and `trainer/bootstrap.sh` copy it to an ephemeral `/root/.ssh` file with mode `0600`. A forced restore followed by two idempotent bootstraps passed; no key material entered Git.

4. **D1 acceptance gate — problem:** before writing any D1 implementation code, the human required proof of the full laptop → pod → laptop loop, not merely a successful SSH login. **Diagnosis:** the pre-code D1 gate produced a GPU-free fake-trainer run of 150 steps in detached tmux on the pod; `vf watch` rsynced its metrics to the laptop and the local/remote JSONL SHA-256 values matched (`a30b250de8932c6ffef67ab14ade294d51d881df201a2640eafe37e11295ab00`). The local API then served the synchronized curve from `GET /jobs/demo1/metrics` (on port 8010 because port 8000 was occupied). To close an evidence gap in the initial record, Codex also ran an isolated `resumecheck`: it checkpointed at step 20, was stopped with `vf kill`, then restarted with the log `Resuming resumecheck from step 20` followed by step 21; its prior metrics remained append-only. **Decision and ownership:** the human set the gate and the no-real-training boundary; Codex implemented the fake trainer, atomic checkpoint path, rsync exclusions, and evidence capture in `a41cc0c` and `d76a219`. That gave us a tested control plane before provisioning the real runtime.

### 2026-07-15 — v0.4.x D2 engineering log

1. **Real GRPO runtime — problem:** the D2 specification required a real one-L4 GRPO smoke, but verl 0.8's PPO path still imported FlashAttention padding helpers even when Qwen was configured for SDPA. **Diagnosis:** the first real preflight reached the trainer and failed with `ModuleNotFoundError: No module named 'flash_attn'`; the direct eager fallback in `5b43320` then made Ray's control processes import the trainer path and Ray timed out waiting for its metrics-agent port. **Decision and ownership:** the human-owned D2 gate required a real run, not a fake replacement. Codex made the runtime compatibility judgment in `1b6effb`: install a lazy import hook only when `verl.utils.attention_utils` resolves the missing helpers, with Transformers 4.57.6 pinned by `575ba55`. The resulting pod stack was Python 3.12.3, torch 2.8.0+cu128, vLLM 0.10.2, verl 0.8.0, and Ray 2.56.0; Python 3.12 remains a documented pod deviation from the repository's 3.11 target.

2. **Storage-only checkpoint/resume — problem:** the gate required an interrupted GPU job to resume from a durable abstraction rather than from transient verl staging. **Diagnosis:** `d2demo` atomically published `ckpt/step_50/global_step_50`; after `vf kill d2demo`, tmux ended and the GPU was empty. The new invocation logged `Resuming d2demo from Storage checkpoint runs/d2demo/ckpt/step_50/global_step_50`; its first new bridged metric was step 53, while the existing steps 1–52 remained append-only. **Decision and ownership:** the human specified the kill/resume gate and disposable-node premise. Codex implemented the native-checkpoint bridge in the D2 series beginning `c346f15` and verified it through `1b6effb`; it intentionally copies a complete independent checkpoint into Storage instead of treating `.verl-staging` as recovery state.

3. **D2 end-to-end evidence — problem:** a completed pod job was insufficient unless the laptop received safe, contract-shaped evidence. **Diagnosis:** the resumed job completed 100 public metric rows, logged `Published Storage checkpoint step_100`, and generated `artifacts/final/model.txt` plus `curve.png`. `vf watch d2demo` produced matching local/remote metrics SHA-256 (`9aea9fb7a6ffea9d0463934c6e689020ab6b1c5f3e6cb2437b62d7b7d7537cf8`); checkpoint and native staging directories were excluded. Local port 8010 served `/jobs/d2demo/metrics` as four aligned 100-element arrays. **Decision and ownership:** the D2 acceptance boundary came from the human plan; Codex used the existing `vf` control plane and MetricRecord bridge, then recorded the result in v0.4.11. The observed validation accuracy moved from 0.20 initially to 0.60 finally, but this is a 100-step smoke with no spurious control, so it is not a quality-gain claim.

4. **Copilot/sandbox evidence boundary — problem:** D2 adds verifier-authoring infrastructure, but a local test must not be represented as a live provider or host-code execution. **Diagnosis:** `pytest -q` passed 45 tests (1 expected skip), focused Copilot/OpenRouter/data/verifier tests passed 28 with injected dependencies, and Docker integration passed 7 with `VF_RUN_DOCKER_INTEGRATION=1`; `bash -n scripts/vf` also passed. **Decision and ownership:** the human specification keeps verifier approval human-controlled and excludes host fallback. Codex retained that boundary: the D2 run used the committed fixture, no live OpenRouter request was made, and Docker-only validation remained separate from the GPU smoke.
