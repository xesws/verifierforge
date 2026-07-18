# Repository Guidelines

## Project Structure & Module Organization

`core/` contains shared contracts, verifiers, and storage. `trainer/` is GPU-facing; `fake_train.py` is the local smoke path. `app/api/` serves local run data, `mock/` supplies frontend fixtures, `scripts/vf` is the remote control plane, and `tests/` mirrors module areas (for example, `test_storage.py`).

## Build, Test, and Development Commands

Install the lightweight dependencies with:

```bash
python -m pip install -r requirements-app.txt -r requirements-trainer.txt
pytest -q
```

Use `python -m trainer.fake_train --job demo1 --steps 10 --interval 0.1` for a resumable local smoke test. Start the API with `uvicorn app.api.main:app --reload`, the mock with `python mock/server.py`, and check shell changes with `bash -n scripts/vf`.

For a disposable vLLM/proxy serving pod, install the separately locked direct
stack with `python -m pip install -r requirements-serve.txt`. Reviewer-safe API
verification uses `VF_API_DATA_MODE=artifacts`; operational recovery context is
always `docs/p0-run-sheet.md`, while `docs/dev_doc_v0.md` is the external design
and evidence record.

## Coding Style & Naming Conventions

Target Python 3.11 and Pydantic v2. Use four-space indentation, public type hints, `snake_case` functions/variables, and `PascalCase` models/classes. Prefer small modules over new abstractions. Keep contracts additive: do not rename or remove fields consumed by the mock or frontend.

Metrics are append-only JSONL; checkpoints publish through a temporary directory and rename. Never commit model/checkpoint artifacts or `.env` secrets.

## Versioning & Worktree Merge Rules

Pre-1.0 scaffold: **`v0.1.0`**. `docs/dev_doc_v0.md` is the external v0 design/evidence record, not a product-release number; `docs/p0-run-sheet.md` is the sole live operational context. Every change needs one target version in its branch name, commit subject, and PR title. Increment the minor for a new capability (`v0.2.0`) and the patch for a bug, compatibility, infrastructure, or docs-only change (`v0.1.1`, then `v0.1.2`). Examples: `feature/v0.2.0-verifier-copilot`, `fix/v0.1.1-checkpoint-resume`, and `docs/v0.1.2-runbook`; commit subjects begin `v0.2.0 Verifier: ...`.

**Trunk-first default.** Develop on `main` by default. Branches are allowed only for a single purpose and must be created and merged the same day. Do not leave long-lived parallel feature branches.

**`core/` serialization.** Any change under `core/` is its own merge wave and must land first. Never allow two branches to edit `core/` (including `core/contracts.py`) at the same time. Contracts stay additive: do not rename or remove fields consumed by the mock or frontend. If a merge conflict touches `core/contracts.py`, stop and escalate both sides to a human—do not auto-resolve the contract.

**Merge gate and cleanup.** Before merging: rebase onto the latest `main`, resolve non-contract conflicts manually (never blindly choose ours/theirs), and require `pytest -q` fully green. After merging: delete the branch and any matching worktree immediately. Prefer trunk commits for same-day docs/infra patches; use a branch only when isolation is required.

Optional worktrees (when needed) come from current `origin/main`: `git fetch origin && git worktree add -b feature/v0.2.0-name ../vf-v0.2.0 origin/main`. Each active worktree owns one version and file area. Contract changes need explicit human approval, mock updates, and tests in the same wave. Remaining worktrees must `git pull --ff-only` before new edits.

## Documentation-First Delivery Gate

**No version document, no implementation.** Before any primary agent or sub-agent writes code, reserve its target version and create `docs/versions/v0.<minor>.x/v0.<minor>.<patch>-<slug>.md`. The document must state status, problem/scope, explicit non-goals, affected code and documentation areas, worktree/owner, contract impact, and validation plan. At the same time, create or update a matching versioned document in every affected area under `docs/` (for example, `docs/model-trainer/v0.2.0-remote-smoke.md` and `docs/infrastructure/v0.2.0-remote-smoke.md`).

The parent agent must give every sub-agent the target version and required documentation paths before delegating code. A sub-agent that cannot find those documents must stop and ask for them; it may not create implementation files first. Scope changes require the version and area documents to be updated before the corresponding code. A merge is blocked until the version document, all affected area documents, tests, and implementation changes agree; the integration owner verifies that set during conflict resolution.

Product versions continue to increment mechanically across stage boundaries;
do not restart or reinterpret the sequence for a new subsystem. Semantic tags
are separate: `v1.0-buildweek` is an owner-created submission freeze, while
system milestones follow `docs/design/` stage names such as
`db-N-complete`, `agent-gate-c-pass`, and `provisioner-pN-live`.

Every new subsystem is disabled by default behind a feature flag. A gate pass
does not enable it automatically: the owner must approve the deployment change,
and the reviewer path must remain independent while the flag is off. Production
work stays trunk-first; long-lived branches remain prohibited.

## Testing Guidelines

Use pytest and name tests `test_*.py`. Add focused failure and boundary coverage for changed contracts, storage, or verifier behavior. Run `pytest -q` plus the narrow smoke command relevant to the change before committing.

If you need to use the API to do the model testing, only use OpenRouter (and prefer the model GLM 5.2 - xhigh effort to do so). We want to save money during the tests. You should only use the real OpenAI models during production.

## Commit & Pull Request Guidelines

Keep commits focused and use the versioned prefix above. PRs state affected modules, validation results, contract/mock impact, and screenshots only for UI-visible changes. Do not stage generated `runs/`, `models/`, or cache files.

When recording an AI co-author, use `GPT` or `Codex`; never attribute the work
to a different editor or framework.
