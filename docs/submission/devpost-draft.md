# VerifierForge — Devpost draft

## Project name

VerifierForge

## Tagline

Turn verifiable production traffic into a small-model forge—and ship the
held-out proof, not just a checkpoint.

## Short description

VerifierForge discovers a repeated, objectively checkable task in API traffic,
asks a guarded Agent whether fine-tuning is justified, records human approval,
and runs an evidence-first training/delivery workflow. The proved vertical is
NL→SQL.

## Inspiration

A fine-tuning demo can look successful because formatting improved, train data
leaked into evaluation, or a selected checkpoint cannot actually be served.
We wanted every claim to cross an explicit boundary: programmatic verification,
frozen held-out data, a random-reward comparison, loadable model artifacts,
and reversible production routing.

## What it does

1. An OpenAI-compatible proxy records hashes, tokens, latency, cost and route,
   while keeping prompt/response bodies out of product storage.
2. Discover shows traffic volume, monthly cost and an owner-confirmed sample
   source for clusters such as Data Pull SQL.
3. Forge Agent calls four read-only tools—traffic, samples, economics and
   verifiability—and submits `forge`, `skip`, or `need_more_data` under strict
   Pydantic guards.
4. `Approve & Forge` records durable human intent. Provisioning/training stays
   on a separate audited execution boundary.
5. NL→SQL training uses a SQLite result-set verifier, frozen train/held-out
   data, GRPO, checkpoint serving gates and a random-reward control.
6. A tuned OpenAI-compatible endpoint can receive a reversible canary while a
   sampled guardian verifies SQL off the request path.

## Evidence, not claims

On the frozen 60-row held-out set, selected step 350 moved pass@1 from
**0.5833 to 0.7833** and pass@8 from **0.7667 to 0.9000**. Mixed fraction moved
from 0.4667 to 0.4333. The committed artifact bundle also contains the 0.5B
random-reward control; it is a falsification reference, not a broad causal or
benchmark claim.

Forge Agent's live 12-scenario Gate C used the exact provider-listed model ID
`gpt-5.6-luna`. Final metrics were decision accuracy **1.0**, chain success
**1.0**, illegal actions **0**, and legal config rate **1.0**. The feature flag
still defaults off. In a separate real product run, no approved samples yielded
`need_more_data`; after a human approved a SHA-bound 50-row source, a fresh run
returned `forge` at confidence 0.98 and wrote an approval to Supabase.

The delivery proof used a temporary Cloudflare quick tunnel after RunPod's
undeclared port-8000 URL returned 404. The official SDK produced real SQL; a
200-request canary split 120 default / 80 tuned and ended with Guardian
LivePassRate 0.85. Resetting canary to zero produced 20 default / 0 tuned.

## How we built it

Python 3.11, FastAPI, Pydantic v2, sqlparse/SQLite, SQLAlchemy async, Alembic,
Supabase Postgres, verl, vLLM, Ray, tmux, rsync and S3. SQLite remains the local
default; the same repository implementation passed real Postgres migration and
row/digest reconciliation. `S3Storage` publishes immutable generations and a
manifest last, so an interrupted upload is never a resume checkpoint.

OpenAI Agent turns use the Responses API because a preflight matrix proved
Luna's Chat Completions tool path rejects reasoning+tools. OpenRouter retains
Chat Completions. Every formal evaluation must pass a no-tool and tool-call
flight check before the scenario batch.

Codex was the implementation collaborator. The human set architecture, gates,
budgets and stop conditions; Codex diagnosed runtime/API failures, made bounded
implementation choices, retained counter-evidence, and committed a detailed
run sheet. Thresholds were never lowered to manufacture a pass.

## Challenges

- CUDA 13 vLLM initially failed against a CUDA 12.8 pod; the recorded working
  training stack became torch 2.8.0+cu128 / vLLM 0.10.2 / verl 0.8 / Ray 2.56.
- Network-volume key permissions exposed a private key as 0666. Git operations
  now copy it to a disposable 0600 path because chmod on that volume did not
  persist.
- A nominal HF export was not vLLM-loadable. Publication now includes a serving
  smoke; step 350 was converted and evaluated before delivery.
- The public RunPod route did not expose port 8000. The failure stayed in the
  record; the quick tunnel proved the path without being called a permanent SLA.
- The new RunPod P-2 adapter created, reached and deleted a real gold pod, but
  billing history returned no row within its declared 15-minute evidence gate.
  It stopped before orphan/training resources and did not create the completion
  tag.

## Accomplishments

- A complete Discover → Agent decision → human approval product flow with a
  default-off Agent and no hidden GPU side effect.
- A frozen held-out model result plus random-reward control and full sample
  evidence.
- One repository layer proven on local SQLite and Supabase Postgres.
- A public model/canary/guardian proof with a recorded reset to zero.
- A reviewer-safe artifact mode requiring no GPU, weights, secrets or paid API.

## What is next

Close P-2 only after RunPod emits an auditable billing row, then run its orphan
proof and approved 0.5B S3-only job. Replace the quick tunnel with a durable
authenticated endpoint, and connect the product approval to execution only in
the separately scoped P-4 wave. Broader verifiers and benchmarks remain future
work.

## Built with

Python, FastAPI, Pydantic, SQLAlchemy, Alembic, Supabase, SQLite, sqlparse,
pytest, OpenAI Responses, OpenRouter, verl, vLLM, Ray, S3/boto3, tmux, rsync,
RunPod, Cloudflare Tunnel, GitHub and Codex.

## How to test

[`JUDGES.md`](../../JUDGES.md) starts the real API in immutable artifact mode,
the deterministic proxy, and an optional mock-bound Discover/Agent UI. It needs
no GPU, model weight, cloud credential, or paid request.

## Availability and limitations

The repository is private. The stable review surface is the committed artifact
path. The public tunnel was ephemeral; Forge Agent is default-off; P-2 live
completion remains blocked at billing evidence; and one NL→SQL result is not a
general language-model benchmark.
