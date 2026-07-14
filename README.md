# VerifierForge

VerifierForge is a developer tool for RL-fine-tuning small open models against programmatic verifiers.

## Architecture

TODO

## Quickstart

TODO

## How we worked with Codex

### 2026-07-14

The human-authored specification defined the framework scope: shared Pydantic contracts, file storage, a tiered NL2SQL verifier, a GPU-free trainer, local and mock APIs, and a RunPod control plane; this session implemented and tested that scope.
Codex chose `pytest.ini` for reliable root imports, declared `sqlparse` as the verifier runtime dependency, added a final-step checkpoint so a 10-step run can resume, and used port 8010 for the local API smoke test because port 8000 was occupied by the host.
Those are implementation choices rather than changes to the product intent; real training remains intentionally out of scope.
