# VerifierForge

VerifierForge is a developer tool for improving small open models against a
programmatic verifier, then proving that the improvement survives a held-out
evaluation and operational handoff.

It is deliberately a narrow, inspectable system: a verifier is the source of
truth; training nodes are disposable; metrics and checkpoints flow through a
small Storage contract; and a routing/guardian layer makes a canary reversible.

## What is real in this repository

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

## Architecture

```text
Laptop / CI                         Disposable GPU worker
───────────                         ─────────────────────
FastAPI + proxy + verifier ─SSH──▶  verl GRPO + vLLM rollout
     │                                      │
     │  contracts / Storage                 │ append metrics, publish checkpoints
     ▼                                      ▼
Demo artifacts / local runs  ◀──────  LocalStorage or S3Storage
     │
     └── routing canary + sampled verifier guardian
```

The laptop owns development and reviewable artifacts. A RunPod worker is an
executor, not a source of truth: it may be replaced after a failure. Local
Storage is the normal path; S3 Storage uses immutable object generations and a
manifest-last publication boundary so an interrupted upload cannot become a
resume checkpoint.

## Six-step workflow

1. Define a task and a deterministic verifier with tiered scoring.
2. Build and verifier-screen a candidate prompt set.
3. Measure a baseline with multiple samples, then freeze data and verifier
   identities before training.
4. Train a small model and run a random-reward control under the same control
   plane.
5. Select checkpoints only on held-out data; retain sample-level evidence.
6. Serve through an OpenAI-compatible endpoint, route a reversible canary, and
   score sampled traffic in a non-blocking guardian.

## Quickstart

Install the lightweight local dependencies and run the test suite:

```bash
python -m pip install -r requirements-app.txt -r requirements-trainer.txt
pytest -q
```

Serve the committed, reviewer-safe D4 evidence without a GPU or cloud account:

```bash
VF_API_DATA_MODE=artifacts uvicorn app.api.main:app --reload
curl http://127.0.0.1:8000/jobs
curl http://127.0.0.1:8000/jobs/d4-m3-1p5b-r1-v0125/metrics
```

The default API mode reads ignored local `runs/`; artifact mode is deliberately
read-only. For proxy development, use `VF_PROXY_UPSTREAM=fake`; it makes no
network request. A disposable serving pod uses the direct, locked
`requirements-serve.txt` environment and an ignored `VF_ENDPOINT_API_KEY`.

## Stateless-compute battle history

The project was built against disposable GPU nodes rather than treating a pod
as a workstation. The control plane detaches jobs in tmux, records process
groups for kill/recovery, and keeps checkpoint publication separate from
transient verl staging. A real S3 round trip has already verified checkpoint
SHA recovery, 50 append-only metrics, and invisible interrupted uploads. The
separate GPU node-loss proof is recorded live in `docs/p0-run-sheet.md` rather
than being claimed early here.

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
- The public RunPod proxy endpoint was not reachable during the delivery test
  (30-second timeout with zero bytes). Local vLLM serving passed, but public
  canary/guardian claims are therefore intentionally absent.
- S3 object semantics are tested and a true-bucket proof passed; automated
  multi-node rescheduling, cross-card FSDP recovery, and spot orchestration are
  explicitly out of scope.
- Demo artifacts exclude weights, checkpoints, credentials, raw traffic, and
  any paid-provider dependency.


## How we worked with Codex

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
