# VerifierForge

VerifierForge is a developer tool for RL-fine-tuning small open models against programmatic verifiers.

## Architecture

TODO

## Quickstart

TODO

## How we worked with Codex

### 2026-07-14 — v0.2.0 / v0.3.0 infrastructure log

1. **Laptop/GPU split — problem:** a rented GPU pod can disappear, so it could not be the owner of the development session or durable training state. **Diagnosis:** the human established that the laptop holds the main Codex session and acts as the development host; RunPod is a stateless SSH-driven compute executor. All persistent training state lives on the `/workspace` network volume and crosses a worker lifetime through the pluggable `Storage` contract. **Decision and ownership:** this architecture and its failure assumption came from the human specification. Codex implemented the corresponding control plane in `a41cc0c` (`scripts/vf` subcommands `bootstrap`, `train`, `watch`, `logs`, `status`, `kill`, and `model`) and the tmux-detach discipline, so a job survives the initiating SSH connection rather than the pod being treated as a long-lived workstation.

2. **CUDA dependency conflict — problem:** the initial v0.3 pin in `8a6c9a3` used `vllm==0.25.1`. **Diagnosis:** it installed but failed to import with `ImportError: libcudart.so.13: cannot open shared object file`; its CUDA 13 runtime did not match the L4's CUDA 12.8 environment, and 0.25.1 was outside verl 0.8's declared vLLM range (`>=0.8.5, <=0.12.0`). Codex retained the failed environment and log instead of hiding the evidence. **Decision and ownership:** the human set a 45-minute timebox before the attempt so dependency debugging could not consume the day. Within that limit, Codex made the compatibility judgment to replace the pin in `5488578` with `vllm==0.10.2` and the pinned `verl[vllm]` v0.8 source revision. The detached `vf-runtime-install-v2` session completed in about 13 minutes; the verified stack was `torch 2.8.0+cu128`, `vllm 0.10.2`, `verl 0.8.0`, and `ray 2.56.0` with CUDA available. `f34fcd6` records the outcome.

3. **SSH key permissions on the network volume — problem:** `/workspace` exposed the persisted deploy private key as mode `0666`, which OpenSSH rejects as too permissive. **Diagnosis:** a direct key use failed because the network volume does not preserve enforceable POSIX private-key permissions, so a naive `chmod 600 /workspace/.ssh/id_ed25519` would not stick across the volume boundary/restart. **Decision and ownership:** persisting the read-only deploy identity under `/workspace` followed the human requirement that Pod state survive restart; Codex chose the secure bridge in `523d7e8`: before any Git clone or pull, `scripts/vf` and `trainer/bootstrap.sh` copy it to an ephemeral `/root/.ssh` file with mode `0600`. A forced restore followed by two idempotent bootstraps passed; no key material entered Git.

4. **D1 acceptance gate — problem:** before writing any D1 implementation code, the human required proof of the full laptop → pod → laptop loop, not merely a successful SSH login. **Diagnosis:** the pre-code D1 gate produced a GPU-free fake-trainer run of 150 steps in detached tmux on the pod; `vf watch` rsynced its metrics to the laptop and the local/remote JSONL SHA-256 values matched (`a30b250de8932c6ffef67ab14ade294d51d881df201a2640eafe37e11295ab00`). The local API then served the synchronized curve from `GET /jobs/demo1/metrics` (on port 8010 because port 8000 was occupied). To close an evidence gap in the initial record, Codex also ran an isolated `resumecheck`: it checkpointed at step 20, was stopped with `vf kill`, then restarted with the log `Resuming resumecheck from step 20` followed by step 21; its prior metrics remained append-only. **Decision and ownership:** the human set the gate and the no-real-training boundary; Codex implemented the fake trainer, atomic checkpoint path, rsync exclusions, and evidence capture in `a41cc0c` and `d76a219`. That gave us a tested control plane before provisioning the real runtime.
