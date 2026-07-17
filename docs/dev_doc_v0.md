# VerifierForge v0 — external development document

**Status:** delivery evidence through D5 infrastructure work, 2026-07-17.
Operational detail and live stop conditions are maintained in
[`p0-run-sheet.md`](p0-run-sheet.md); this document is the public-facing design
and evidence record.

## Purpose

VerifierForge is a small-system pattern for verifier-backed reinforcement
learning. It separates three concerns that are often blurred together:

1. a programmatic verifier defines success;
2. a disposable GPU worker produces candidate training state; and
3. a durable, inspectable control plane retains evidence and allows recovery.

The v0 implementation is intentionally narrow: NL→SQL is the first vertical,
the verifier is SQLite-backed, and the product surface is a FastAPI API/proxy
with a reversible routing switch. The aim is to show a credible end-to-end
engineering loop, not to claim a general language-model benchmark.

## Architecture

```text
                  ┌────────────────────────────────────┐
                  │ Laptop / review host                │
                  │ FastAPI · proxy · verifier · docs   │
                  └───────────────┬────────────────────┘
                                  SSH
                                  ▼
                  ┌────────────────────────────────────┐
                  │ Disposable GPU worker               │
                  │ verl GRPO · vLLM rollout · tmux     │
                  └───────────────┬────────────────────┘
                                  Storage
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
             LocalStorage                  S3Storage
             (default)                 (durable proof)
```

The laptop is the development host and contains the principal Codex session.
RunPod is a stateless compute executor driven over SSH. A worker can die
without becoming the authoritative owner of metrics, evidence, or checkpoints.
`scripts/vf` provides the deliberately small remote control plane:
`bootstrap`, `train`, `watch`, `logs`, `status`, `kill`, and `model`.

## Stable interfaces

`core/contracts.py` supplies the frontend-facing job, metric, report, routing,
and live-pass shapes. The storage boundary is equally small:

```python
save_checkpoint(job_id, step, path)
load_latest_checkpoint(job_id)
append_metrics(job_id, record)
put_artifact(job_id, name, path)
get_artifact(job_id, name, dest)
```

Local storage publishes checkpoints through a temporary directory and rename;
metrics are append-only JSONL. S3 storage uploads immutable generations and
publishes one manifest object last. A reader considers only a manifest-published
checkpoint resumable, so a truncated object set is never mistaken for state.

## Verified development record

| Milestone | Evidence |
| --- | --- |
| D1 remote loop | GPU-free fake trainer completed 150 tmux steps; rsynced metrics matched SHA-256; kill/resume retained append-only metrics. |
| D2 GRPO smoke | Real 0.5B GRPO ran through the checkpoint bridge; `vf kill` and resume used Storage rather than transient staging. |
| D3 freeze | Training pool (50), held-out evaluation set (60), and verifier version were frozen before main training. |
| D4 main result | 1.5B step 350 was selected on held-out pass@1: `0.5833 → 0.7833`; pass@8: `0.7667 → 0.9000`; mixed fraction: `0.4667 → 0.4333`. A 0.5B random-reward control was also run. |
| Serving compatibility | The converted step-350 HF export loaded under vLLM 0.10.2 after aligning `transformers==4.57.6`, `tokenizers==0.22.2`, and `huggingface_hub==0.36.2`; local `/v1/models` and an NL→SQL completion passed. |
| S3 semantics | Moto validates all Storage operations; one real bucket restored a checkpoint by SHA, recovered 50 ordered metrics, and kept an intentionally interrupted upload invisible. |

The committed `data/demo-artifacts/` directory carries the review-safe D4
metrics and held-out report. It contains no checkpoint weights or credentials.
`VF_API_DATA_MODE=artifacts` exposes it through the real FastAPI routes.

## Verifier and evaluation workflow

The NL→SQL verifier gives tiered credit: syntactic parsing, successful SQLite
execution against a supplied schema, then exact result-set match. The final
score penalizes overly long completions. This produces interpretable partial
reward while retaining an exact-success endpoint.

The workflow is fixed in order:

1. construct or expand prompts and verifier-screen candidates;
2. baseline sample with multiple completions per prompt;
3. freeze train pool, held-out set, and verifier identity;
4. train plus an independent random-reward control;
5. select checkpoints only on held-out data; and
6. preserve full sample-level evidence before making a report claim.

The training pool is monitoring-only after freeze. The held-out 60 rows are the
only basis for the `before`/`after` result above.

## Product traffic and routing

`app/proxy/` exposes an OpenAI-compatible transparent proxy. In development,
the default upstream is deterministic `fake`, so product-path tests have no
provider cost. It records request metadata in SQLite, clusters by system-prompt
hash, and can route a configured cluster to a tuned target at a canary percent.

The guardian is a sidecar: sampled data-pull SQL replies receive the NL→SQL
verifier and aggregate to `LivePassRate`. A scoring failure must not block a
user request. The routing and live-pass API shapes are shared by the real API
and mock server.

## Serving status and reproducibility

The verified serving stack is captured in `requirements-serve.txt`:
vLLM 0.10.2, torch 2.8.0, Transformers 4.57.6, tokenizers 0.22.2, and
huggingface_hub 0.36.2. A local L4 vLLM service loaded the selected step-350
export and returned a real SQL completion.

The public RunPod proxy hostname did not return bytes within 30 seconds during
the delivery attempt. Therefore this document makes no live-public-endpoint or
real-canary claim. The endpoint is left as an explicit owner-side exposure
task, not hidden by a fake success.

Run the reviewer-safe API locally:

```bash
python -m pip install -r requirements-app.txt
VF_API_DATA_MODE=artifacts uvicorn app.api.main:app --reload
curl http://127.0.0.1:8000/jobs
```

## Boundaries and follow-up work

v0 does not implement automatic rescheduling, spot-instance orchestration,
cross-card FSDP recovery, a production model registry, or a broad benchmark.
The S3 GPU node-loss demonstration is tracked in the run sheet and is not
claimed complete until its 100-step kill/resume curve and object inventory are
archived.

The next product work is intentionally mundane: expose the serving port,
connect the existing proxy’s one environment-variable target to it, re-run the
50% canary proof, and keep the switch at zero outside a recorded demonstration.
