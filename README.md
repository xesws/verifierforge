# VerifierForge

VerifierForge turns production traffic with a programmatic success criterion
into an evidence-backed small-model forge: Discover identifies a candidate,
an audited Agent recommends whether to train, a human approves, and a
disposable GPU path publishes results through durable storage.

The v1 prototype is deliberately narrow and inspectable. NL→SQL is the proved
vertical; the verifier is the source of truth; held-out data selects the model;
and routing remains reversible.

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
| Delivery | A public Cloudflare quick tunnel served the selected model; 200 canary requests split 120 default / 80 tuned and produced Guardian LivePassRate `0.85`, then canary zero sent 20 / 20 requests to default. |
| Provisioning | Mock P-1 lifecycle/fuses pass. The P-2 RunPod adapter is implemented, but its live tag is withheld because a deleted gold pod produced no billing-history row within the 15-minute evidence deadline. |

## Architecture

```text
OpenAI-compatible traffic ──▶ proxy ──▶ default / tuned model
          │                    │                │
          ▼                    └── guardian ────┘
   Discover clusters                 verifier score
          │
          ▼
 Forge Agent (read-only tools) ──▶ human approval ──▶ provisioner
          │                                               │
          ▼                                               ▼
 Supabase / SQLite                         disposable verl + vLLM worker
                                                          │
                                                          ▼
                                        LocalStorage or manifest-last S3
```

Pydantic contracts sit at each boundary. Product metadata, decisions,
approvals, routing, and audit events use one repository layer. Full Agent
traces and training objects remain in S3. A GPU worker is an executor, never a
source of truth.

## Product workflow

1. The proxy records hashes and usage metadata, not prompt bodies, then groups
   a stable task cluster in Discover.
2. A user confirms a repository sample source; the server recomputes its path,
   row count and SHA-256.
3. Forge Agent calls read-only traffic, sample, economics and verifiability
   tools. Its only terminal actions are `forge`, `skip`, or `need_more_data`.
4. `Approve & Forge` writes durable human intent. It does not hide a GPU side
   effect inside the web request.
5. The training path freezes data/verifier identity, runs the main job and a
   random-reward control, and selects only on held-out evidence.
6. The proxy canaries the tuned endpoint while a non-blocking guardian scores
   sampled SQL output; setting canary to zero restores the default path.

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

## Engineering boundaries

The training control plane detaches jobs in tmux, records process groups for
kill/recovery, and keeps checkpoint publication separate from transient verl
staging. S3 uses immutable generations plus a manifest-last boundary; a real
bucket proof restored a checkpoint SHA, 50 ordered metrics, and kept an
interrupted upload invisible.

Forge Agent is advisory. It has bounded turns/tokens/time, read-only tools,
strict structured submission, and no provisioning or training handle. Gate C
passed, but `VF_AGENT_ENABLED` stays false unless an operator opts in. The web
approval remains a database write; the separate P-2 CLI is the execution seam.

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

## Limitations

- The demonstrated quality result is one NL→SQL task family with 50 training
  rows and a 60-row held-out set; it is not a broad benchmark claim.
- The successful public model proof used an ephemeral Cloudflare quick tunnel,
  not a durable production hostname or SLA.
- P-1 provisioning is proved with a mock. P-2 created and deleted a real gold
  pod, but RunPod returned no billing row within the declared 15-minute gate;
  orphan and approval-driven training proofs therefore did not run and the
  completion tag is absent.
- Agent Gate C covers a frozen 12-scenario evaluator. It is not evidence that
  arbitrary business traffic should auto-train; the flag remains default-off
  and approval is required.
- Demo artifacts exclude weights, checkpoints, credentials, raw traffic, and
  any paid-provider dependency.


## How we worked with Codex

### 2026-07-18–19 — v0.18.0 through v0.28.0 product/infrastructure log

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

4. **Real provisioning needed a fail-closed receipt.** The human set `$5`,
   180-minute and cleanup limits and required create/status/delete/billing
   before training. Codex implemented the REST adapter and approval-driven S3
   executor. The first gold pod reached SSH and was deleted, but billing
   history stayed empty for 15 minutes. Codex stopped without creating the
   orphan or training pods and withheld `provisioner-p2-live`; implementation
   success was not relabeled as operational completion.

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
