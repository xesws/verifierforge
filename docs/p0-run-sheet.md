# P0 Run Sheet — v0.6.1 data freeze

**Status:** in progress — v0.6.2 pod-local Gate A recovery
**Owner:** Codex on `main`
**Starting commit:** `78912f1` (`v0.6.0 Data: discard malformed expansion responses`)
**Recovery rule:** after any session interruption, this file is the sole operational context. Read it before taking any action.

## Fixed boundaries

- Work only on `main`; do not create a parallel worktree.
- Do not change `trainer/` training logic, GRPO configuration, or a trainer data-path configuration. The existing `trainer/data/nl2sql_v1.jsonl` contents may be replaced only by the frozen 50-row projection.
- Use the shared environment-configured OpenRouter client only. Never print, commit, rsync, SSH-forward, or copy `.env` / API keys to RunPod.
- Keep the pre-existing unstaged `AGENTS.md` change out of every P0 commit.
- Every completed numbered step: tick it here, record factual results, commit only its scoped files, then report one concise line. A stop condition ends the run; no threshold, budget, or trainer change is allowed without human direction.

## Fixed artifacts

| Purpose | Path |
| --- | --- |
| Full verifier-screened candidates | `data/nl2sql/v0.6.1-p0-full.jsonl` |
| S1 count-only summary | `data/nl2sql/v0.6.1-p0-augmentation-summary.json` |
| 50-row trainer/Gate-A subset | `trainer/data/nl2sql_v1.jsonl` |
| Gate A evidence | `data/nl2sql/v0.6.1-p0-gate-a-{full,subset}.json` |
| Freeze manifest | `data/nl2sql/v0.6.1-p0-freeze-manifest.json` |
| Freeze tag | `v0.6.1-p0-data-freeze` |
| Gate B job | `p0-gateb-v061` / `grpo_v1_0p5b` |

## Execution checklist

### 0. Documentation gate

- [x] Create this run-sheet, the v0.6.1 version document, and matching GPT, evaluation, verifier, infrastructure, and model-trainer documents.
- [x] Commit: `v0.6.1 Docs: add P0 data freeze run sheet`.

**Acceptance:** all implementation decisions below are documented before code changes.
**Stop:** documentation or its scoped commit cannot be completed.

### 1. S1 — bounded real augmentation

- [x] Add a fixed one-retry malformed-JSON policy and `--timebox-minutes` / atomic summary support to the augmentation tool, with tests.
- [x] Run 50 reviewed seeds with `--variants-per-seed 6` and a 30-minute budget. At the deadline, launch no new request; let an already-started request finish.
- [x] Retain only records whose independent `NL2SQLVerifier` score is exactly `1.0`.
- [x] Record `accepted_count`, `processed_seed_count`, `unprocessed_seed_count`, malformed/retry counts, main yield `accepted / (processed × 6)`, and reference yield `accepted / (50 × 6)`.

**Acceptance:** atomic full JSONL and summary exist; no secret/raw provider response is written; focused tests and `pytest -q` pass.
**Stop:** provider/configuration/transport failure, zero processed seeds, or main yield below `50%`. Commit the factual record and do not request more budget.

**Result:** completed. `276` candidates accepted from `300` requested slots; all 276 independently re-scored at `1.0`. Processed/unprocessed: `50` / `0`; retry/malformed: `0` / `0`; rejected expected-results variants: `24`; main yield: `0.92`; full-capacity reference yield: `0.92`. Candidates cover 46 seeds, so S2 will have four processed-no-eligible-candidate fallbacks if independent recheck agrees.

### 2. S2 — deterministic 50-row projection

- [x] Add/test a projection tool. For every `v1-001` through `v1-050`, re-verify candidates and choose the lexicographically smallest candidate `id`.
- [x] A seed with no eligible candidate—whether unprocessed or processed with zero accepted variants—uses its original verifier-checked seed row. Preserve `seed_id`, `source_candidate_id` (`null` for fallback), and selection reason.
- [x] Count all fallback rows together; if the count exceeds 10, do not overwrite the trainer fixture.

**Acceptance:** exactly 50 unique canonical IDs, one per seed; every selected/fallback row scores `1.0`; fallback count is at most 10; the existing loader produces its stable 40/10 split.
**Stop:** missing/duplicate/invalid seed, re-verification failure, or fallback count above 10. Record counts and do not run Gate A.

**Selection rule:** candidate rows are grouped by `seed_id`; sort eligible rows by `id` ascending and choose the first. Original seed fallback is used only when that group is empty.
**Result:** completed. `276` candidates were independently rechecked; `276` remained eligible and none were discarded. The projection contains exactly `50` canonical rows, all independently re-scored at `1.0`; `46` use lexicographically smallest candidate IDs and `4` use `fallback_processed_no_eligible_candidate`. Total fallbacks: `4` (`0` unprocessed, `4` processed), below the limit of `10`. The existing loader accepts all 50 rows and preserves its 40/10 split.

### 3. S3 — real Gate A

- [x] Harden Gate A evidence/input validation without changing its thresholds.
- [ ] Run full-candidate reference Gate A first, then the 50-row subset, both at `k=8`; save structured evidence for both.
- [ ] Record each raw triplet exactly: `pass_at_1`, `pass_at_8`, `mixed_fraction`.

**Acceptance:** both evaluations complete; the subset has `0.20 <= pass_at_1 <= 0.60` and `mixed_fraction >= 0.30`. Full-set values are reference only.
**Stop:** either evaluation errors, or the subset misses either threshold. Commit raw figures/evidence; do not relax thresholds or train.

**STOP:** the full-candidate reference command exited `2` before producing a metric or evidence file, with the deliberately redacted diagnostic `gate_a evaluation error: completion request failed`. Full and subset evidence files are absent; the subset was not started. No thresholds were changed, and S4/S5 are prohibited until a human directs the next action.

**Prepared-only worktree state:** `scripts/freeze_nl2sql.py` and `tests/test_freeze_nl2sql.py` exist locally but are uncommitted and unused; no freeze artifact, tag, or remote action exists. Do not use or commit them unless the human restarts the workflow after resolving S3.

**Full result:** unavailable due to provider completion failure.
**Subset result:** not started.

### 4. S4 — immutable freeze

- [ ] Produce a manifest containing hashes/counts for both datasets, the S1 summary and Gate A evidence, the deterministic selection rule/fallback count, and the NL2SQL verifier source SHA-256 plus Git blob identity.
- [ ] Commit the manifest and this completed step, create annotated tag `v0.6.1-p0-data-freeze`, then push `main` and the tag.

**Acceptance:** every manifest hash recomputes, the tag points to the commit containing both data files and verifier evidence, and origin has that exact tag.
**Stop:** any hash, commit, tag, or push failure. Do not start Gate B.

**Freeze commit/tag:** pending.

### 5. S5 — detached 0.5B Gate B

- [ ] Confirm the pod checkout equals the freeze-tag commit; start `bash scripts/vf train p0-gateb-v061 grpo_v1_0p5b` and verify tmux detachment.
- [ ] Check external job status no more frequently than every 120 seconds; after completion sync non-checkpoint artifacts and inspect `runs/p0-gateb-v061/metrics.jsonl` and the curve.
- [ ] Append the measured pass@1 series/summary and a factual smoke-only conclusion to the remote training log and to this run-sheet; commit the run-sheet result.

**Acceptance:** the job completes, metrics JSONL/curve/final artifact synchronize locally, and the conclusion makes no claim of real gain without held-out evaluation.
**Stop:** nonzero exit, missing metrics/final artifact, or sync failure. Preserve logs/checkpoints; do not auto-retry or alter training settings.

**Result:** pending.

## v0.6.2 recovery — F1 through F5

### F0. Documentation gate

- [x] Create the v0.6.2 version/area documents and record the recovery decisions here before implementation.

**Acceptance:** documentation names the eval-only environment contract, failure-evidence contract, local pod server command, and all stop gates.
**Stop:** no F1 code change before this documentation commit.

### F1. Eval configuration separation

- [x] Add `EvalSettings`: require only `VF_EVAL_BASE_URL` and `VF_EVAL_MODEL`; optionally honor `VF_EVAL_API_KEY`; otherwise use the non-secret local SDK placeholder `vf-local-eval`.
- [x] Gate A uses only `EvalSettings`, does not load `.env`, and never reads/falls back to `VF_LLM_*` or `VF_AUGMENT_MODEL`.
- [x] Remove the Gate A `--model` override. Require `--report`; print resolved sanitized base URL and model before any request and store both in all evidence.

**Acceptance:** missing eval base/model fails closed before client creation; generic augmentation/copilot client behavior remains unchanged.
**Stop:** any observed `VF_LLM_*` fallback, key/log leak, or failed focused/full test.

**Result:** complete locally; focused and full pytest validation recorded in the F1 commit.

### F2. Failure evidence and bounded execution

- [x] Preserve provider exception chains, HTTP status, request ordinal, record/sample, two-attempt history, and a redacted 4 KiB provider-body cap.
- [x] Replace all-at-once submission with at most eight in-flight logical samples. Retry each failed sample once; after ten consecutive terminal failures in request-ordinal order, stop submitting new jobs and record circuit-open state.
- [x] Any terminal sample failure makes the run exit `2` with failure evidence and invalid metrics; it never becomes verifier score `0`. No automatic whole-run retry occurs.

**Acceptance:** failure evidence is atomically persisted on every configuration/input/completion failure; stdout/stderr remain secret-free.
**Stop:** failure evidence cannot be written, retry/circuit tests fail, or a partial run produces Gate A metrics.

**Result:** complete locally; tests cover retry success, terminal metadata/cause/body redaction, ordered circuit opening, bounded concurrency, config/input/completion evidence, and reference-only completion.

### F3. Pod-local vLLM exam server

- [x] Pull the verified implementation to RunPod and start detached tmux session `vf-eval-vllm` using `/workspace/verifierforge/.venv/bin/vllm`.
- [x] Serve the local snapshot at `127.0.0.1:8000` as `Qwen2.5-1.5B-Instruct`, with offline cache flags, BF16, 0.70 GPU memory utilization, and 4096 max model length.
- [x] Record tmux session, port, log path, and raw `/v1/models` response.

**Acceptance:** port 8000 was free before launch; tmux remains alive; `/v1/models` contains `Qwen2.5-1.5B-Instruct`.
**Stop:** cache, vLLM startup, GPU, tmux, or health check fails. No Gate A.

**Result:** complete on RunPod at pod checkout `70c109b`. Initial port check was empty. The service is detached in tmux session `vf-eval-vllm`, listens only on `127.0.0.1:8000`, and logs to `/workspace/verifierforge/runs/p0-eval-vllm/vllm.log`. It uses the cached snapshot, BF16, `--gpu-memory-utilization 0.70`, and `--max-model-len 4096`; `nvidia-smi` reported its worker at 16,692 MiB.

Raw pod-local `curl http://127.0.0.1:8000/v1/models` response:

```json
{"object":"list","data":[{"id":"Qwen2.5-1.5B-Instruct","object":"model","created":1784186815,"owned_by":"vllm","root":"/workspace/hf-cache/models--Qwen--Qwen2.5-1.5B-Instruct/snapshots/989aa7980e4cf806f80c7fef2b1adb7bc71aa306","parent":null,"max_model_len":4096,"permission":[{"id":"modelperm-a10c416ac38a44fa9998891853ba1501","object":"model_permission","created":1784186815,"allow_create_engine":false,"allow_sampling":true,"allow_logprobs":true,"allow_search_indices":false,"allow_view":true,"allow_fine_tuning":false,"organization":"*","group":null,"is_blocking":false}]}]}
```

### F4a. Pod subset Gate A

- [x] Run the 50-row subset inside detached tmux on the pod with `VF_EVAL_BASE_URL=http://127.0.0.1:8000/v1` and `VF_EVAL_MODEL=Qwen2.5-1.5B-Instruct`; do not set `VF_LLM_*`.
- [x] Sync the evidence/log, validate the local input SHA-256, and record the raw `pass_at_1`, `pass_at_8`, and `mixed_fraction`.

**Acceptance:** completed subset evidence has matching input/verifier hashes and passes `0.20 <= pass_at_1 <= 0.60`, `mixed_fraction >= 0.30`.
**Stop:** exit 2/circuit/terminal failure, evidence mismatch, or threshold failure. Do not run full reference or train.

**Result (stopped at gate):** the pod job `vf-gate-a-subset` completed with exit `1` (measured threshold rejection, not an execution error). Raw metrics: `pass_at_1=0.08`, `pass_at_8=0.36`, `mixed_fraction=0.36`. The completed v2 evidence records `candidate_count=50`, `sample_count=400`, `input_sha256=cfa93154cd87013b7460666925200be14f67c5112229f03c66df2978d747255c`, and verifier source SHA-256 `34764efba707d6bf44142a75624b26342686e817e9ea0dcda1603222930f2fd2`.

The local and pod subset input hashes matched exactly: `cfa93154cd87013b7460666925200be14f67c5112229f03c66df2978d747255c`; the synced evidence hashes also matched exactly: `5981a9f92254cc2631603077292f2e4a2751102f607f9b5c8ff929e82dd59b21`. Pass@1 is below the fixed `0.20` lower bound, so F4b, F5, tagging, and Gate B are prohibited pending human direction. No threshold or training configuration was changed.

### F4b. Pod full Gate A reference

- [ ] Only after F4a passes, run the full candidate set with `--reference`, preserving the same eval config and evidence contract.
- [ ] Sync/validate evidence and record the same raw metric triplet. Full-set threshold status is reference-only.

**Acceptance:** completed evidence with matching full-dataset/verifier hashes.
**Stop:** exit 2/circuit/terminal failure or evidence mismatch. No freeze or train.

### F5. Freeze and Gate B

- [ ] Update the freeze manifest helper to bind v2 completed evidence and the shared eval model/base URL. Generate and verify the manifest.
- [ ] Commit freeze artifacts; create annotated `v0.6.2-p0-data-freeze` tag whose message names `eval_model=Qwen2.5-1.5B-Instruct`; push `main` and tag.
- [ ] Stop `vf-eval-vllm`, verify port/GPU release, then launch `p0-gateb-v062` through `vf train` with `grpo_v1_0p5b`.
- [ ] Sync final metrics/curve/artifact, append the measured pass@1 series and smoke-only conclusion to the remote log and this sheet, then commit.

**Acceptance:** the tag binds full/subset/evidence/verifier hashes and eval model; Gate B runs against that tagged checkout and completes with synchronized artifacts.
**Stop:** any manifest/tag/push/server-release/train/sync failure; preserve evidence and do not auto-tune/retry.

## Recovery assumptions

- `VF_EVAL_API_KEY` is an optional override only; absent it uses `vf-local-eval`, never a laptop or OpenRouter key.
- The scheduler permits at most seven already-in-flight logical samples beyond a circuit-open event; no new samples are submitted after the tenth consecutive terminal failure.
- An observed terminal failure invalidates that Gate A run. A human may request one later whole-run retry; two consecutive whole-run invalidations due to isolated terminal failures mean the vLLM service is unhealthy and require a stop/report.
- The earlier OpenRouter key must be rotated before any future external-provider call. This recovery path does not use it.

## v0.6.3 limited overnight difficulty probe

### T1. Read-only subset-evidence audit

- [x] Inspect local synced evidence/log and pod-side artifacts without a model request or file mutation.

**Result:** the completed subset evidence (`schema_version=2`) contains only
aggregate metrics, candidate/input/verifier identities, and no per-sample
scores, groups, or completions. The pod artifact directory contains only the
949-byte JSON evidence, a 183-byte metrics log, and an exit file; vLLM retained
HTTP 200 access lines but no response bodies. Therefore the requested parse /
wrong-result / execution-error taxonomy and three completion excerpts cannot be
reconstructed honestly without rerunning the subset, which is prohibited.

### T2. Full reference-mode difficulty probe

- [ ] Add an atomic per-prompt pass-count artifact path and validate it locally.
- [ ] Launch 276 rows × `k=8` in detached pod tmux against the existing local vLLM.
- [ ] Record the artifact/evidence destination, then stop without projection, freeze, or training.

**Acceptance:** a successful run will persist exactly one row per completed
prompt with `0 <= pass_count <= 8`, and its evidence binds the output to the
full input/verifier/config. Gate thresholds are informational only.
**Stop:** after starting the detached probe; do not poll it tonight, alter the
dataset, or run any other task.
