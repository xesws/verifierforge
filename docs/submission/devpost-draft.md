# VerifierForge — Devpost draft

## Project name

VerifierForge

## Tagline

Verifier-backed fine-tuning with held-out proof, reversible delivery, and
disposable GPU workers.

## Short description

VerifierForge is a developer tool for turning a narrow, objectively checkable
task into a small-model training run and an inspectable delivery record. Its v0
demonstration is NL→SQL: a SQLite-backed verifier grades completions, then a
held-out set decides whether a checkpoint earned its improvement.

## Inspiration

Fine-tuning a small model is easy to demo badly: a chart can go up because of
formatting, contaminated evaluation, or a checkpoint that cannot actually be
served. We wanted the product boundary to be evidence rather than a checkpoint:
freeze the task, run a random-reward comparison, retain sample-level evaluation
records, and make rollout/serving failures visible.

## What it does

1. Scores NL→SQL completions with tiered SQL parse, SQLite execution, and exact
   result-set checks.
2. Stores metrics/checkpoints behind a local-or-S3 Storage contract.
3. Runs training through an SSH/tmux control plane so a rented GPU worker is
   disposable.
4. Keeps a random-reward control and held-out evaluation evidence beside the
   main curve.
5. Provides an OpenAI-compatible proxy with reversible routing and a sampled,
   non-blocking SQL verifier guardian.

The committed D4 demonstration moved held-out pass@1 from **0.5833** to
**0.7833** on 60 rows at selected step 350. Pass@8 moved from **0.7667** to
**0.9000**. The repository also includes the 0.5B random-reward control curve;
these facts are task-specific evidence, not a broad benchmark claim.

## How we built it

The codebase is Python/FastAPI with Pydantic contracts, `sqlparse` and SQLite
for verification, verl/vLLM for the GPU path, and a small bash `vf` control
plane. A FastAPI artifact mode serves committed evidence without model weights
or a cloud account. `S3Storage` publishes immutable object generations with a
manifest-last boundary; a real-bucket round trip restored a checkpoint SHA,
recovered 50 metrics, and kept an interrupted upload invisible.

Codex was used as the implementation collaborator: the work log in README
records decisions, runtime failures, exact stack versions, and validation
results. The product client is OpenAI-compatible and configurable; local
development uses a deterministic fake upstream so tests incur no model cost.

## Challenges we ran into

- A first vLLM stack selected a CUDA 13 runtime that did not match the pod;
  the compatible runtime was pinned and recorded instead of silently relaxed.
- Network-volume file permissions made an SSH key unusable until the key was
  copied to a temporary 0600 path before Git operations.
- A serving export could exist on disk but fail vLLM loading. The publication
  gate now requires a loopback vLLM load and completion before it calls an
  export publishable.
- The public RunPod proxy route timed out with zero response bytes. Local
  vLLM passed, but we do not claim public canary or guardian results.

## Accomplishments we are proud of

- A frozen, held-out result with a selected checkpoint and an explicit random
  reward comparison instead of a single training curve.
- A lightweight storage boundary that supports local development and an
  atomic, tested S3 implementation.
- A reviewer-safe, tracked artifact mode that makes the principal result
  inspectable without GPUs, secrets, weights, or provider spending.

## What is next

Finish the GPU node-loss S3 resume proof, expose the serving port reliably,
then run the existing proxy's 50% canary and guardian on a real endpoint.
After that: verifier templates beyond NL→SQL, a product report view, and a
production model registry. Automatic multi-node recovery and broad evaluation
remain out of scope for v0.

## Built with

Python, FastAPI, Pydantic, SQLite, sqlparse, pytest, verl, vLLM, Ray, tmux,
rsync, S3/boto3, RunPod, GitHub Actions-compatible Git tooling, and Codex.

## How to test

The ten-minute reviewer path is in [`JUDGES.md`](../../JUDGES.md). It starts
the real FastAPI routes in `VF_API_DATA_MODE=artifacts`, verifies the committed
D4 metrics and report shape, and runs the test suite. It requires no GPU,
credential, model download, or paid model request.

## Availability and limitations

The repository is private. The reproducible artifact path is the supported
review surface. A local vLLM service did successfully load the selected
step-350 export and produce NL→SQL output, but the public RunPod gateway timed
out during delivery verification; there is no public hosted endpoint claim in
this draft.
