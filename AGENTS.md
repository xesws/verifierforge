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

## Coding Style & Naming Conventions

Target Python 3.11 and Pydantic v2. Use four-space indentation, public type hints, `snake_case` functions/variables, and `PascalCase` models/classes. Prefer small modules over new abstractions. Keep contracts additive: do not rename or remove fields consumed by the mock or frontend.

Metrics are append-only JSONL; checkpoints publish through a temporary directory and rename. Never commit model/checkpoint artifacts or `.env` secrets.

## Versioning & Worktree Merge Rules

Pre-1.0 scaffold: **`v0.1.0`**. `docs/dev_doc_v0.md`'s `v0` and `v2.1` are document revisions, not product releases. Every change needs one target version in its branch name, commit subject, and PR title. Increment the minor for a new capability (`v0.2.0`) and the patch for a bug, compatibility, infrastructure, or docs-only change (`v0.1.1`, then `v0.1.2`). Examples: `feature/v0.2.0-verifier-copilot`, `fix/v0.1.1-checkpoint-resume`, and `docs/v0.1.2-runbook`; commit subjects begin `v0.2.0 Verifier: ...`.

Create each worktree from current `origin/main`: `git fetch origin && git worktree add -b feature/v0.2.0-name ../vf-v0.2.0 origin/main`. Each active worktree owns one version and file area. Never concurrently edit `core/contracts.py` or the same source file; contract changes need explicit human approval, mock updates, and tests in the same branch. Before merge, rebase on `origin/main`, resolve conflicts manually (never blindly choose ours/theirs), run `pytest -q` and relevant smoke checks, then only the integration worktree merges and pushes `main`. Remaining worktrees must `git pull --ff-only` before new edits.

## Documentation-First Delivery Gate

**No version document, no implementation.** Before any primary agent or sub-agent writes code, reserve its target version and create `docs/versions/v0.<minor>.x/v0.<minor>.<patch>-<slug>.md`. The document must state status, problem/scope, explicit non-goals, affected code and documentation areas, worktree/owner, contract impact, and validation plan. At the same time, create or update a matching versioned document in every affected area under `docs/` (for example, `docs/model-trainer/v0.2.0-remote-smoke.md` and `docs/infrastructure/v0.2.0-remote-smoke.md`).

The parent agent must give every sub-agent the target version and required documentation paths before delegating code. A sub-agent that cannot find those documents must stop and ask for them; it may not create implementation files first. Scope changes require the version and area documents to be updated before the corresponding code. A merge is blocked until the version document, all affected area documents, tests, and implementation changes agree; the integration owner verifies that set during conflict resolution.

## Testing Guidelines

Use pytest and name tests `test_*.py`. Add focused failure and boundary coverage for changed contracts, storage, or verifier behavior. Run `pytest -q` plus the narrow smoke command relevant to the change before committing.

## Commit & Pull Request Guidelines

Keep commits focused and use the versioned prefix above. PRs state affected modules, validation results, contract/mock impact, and screenshots only for UI-visible changes. Do not stage generated `runs/`, `models/`, or cache files.
