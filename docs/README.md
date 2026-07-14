# Documentation Map

`dev_doc_v0.md` is the product and scope baseline. It governs product decisions; change its §12 scope boundary before expanding scope. Release-specific implementation plans live in `versions/`, while the remaining folders own documentation for a technical area.

| Path | Owns |
| --- | --- |
| `versions/` | Version plans, status, ownership, scope, and release validation |
| `model-trainer/` | GRPO/verl, configs, checkpoints, reward adapters, and resume behavior |
| `infrastructure/` | RunPod, SSH, `vf`, persistent storage, bootstrap, and sandbox operations |
| `backend-api/` | FastAPI, jobs, shared contracts, queues, and API compatibility |
| `frontend/` | Web UI, mock-data consumption, charts, and user-facing flows |
| `verifiers/` | Verifier contracts, reward tiers, sandboxed execution, and Copilot output |
| `gpt-integrations/` | Provider clients, prompts, model selection, and GPT runtime configuration |
| `evaluation-serving/` | Baselines, control jobs, reports, artifacts, endpoints, and serving |

## Required Workflow

Before implementation, reserve a target version and write its plan in `versions/v0.<minor>.x/`. In the same change, write or update a versioned document in every affected technical area. Use filenames such as `v0.2.0-d1-remote-smoke.md`; do not create unversioned feature notes. The plan must exist before code, delegation, or a worktree is created. Update it before changing scope, and require it during merge review.

The current scaffold is `v0.1.0`. `v0.1.1` establishes this documentation governance, `v0.2.0` completed the D1 remote-control smoke test, and `v0.3.0` is the planned RunPod runtime-provisioning release.
