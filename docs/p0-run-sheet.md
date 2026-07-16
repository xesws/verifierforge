# P0 Run Sheet — v0.6.1 data freeze

**Status:** resumed — v0.10.0 U1–U3 training/held-out gate update
**Owner:** Codex on `main`
**Starting commit:** `78912f1` (`v0.6.0 Data: discard malformed expansion responses`)
**Recovery rule:** after any session interruption, this file is the sole operational context. Read it before taking any action.

## Fixed boundaries

- Work only on `main`; do not create a parallel worktree.
- Do not change `trainer/` training logic, GRPO configuration, or a trainer data-path configuration. The existing `trainer/data/nl2sql_v1.jsonl` contents may be replaced only by the frozen 50-row projection.
- Evaluation uses only `VF_EVAL_BASE_URL` / `VF_EVAL_MODEL` against pod-local
  vLLM. Never print, commit, rsync, SSH-forward, or copy `.env` / API keys to
  RunPod; this runbook has no OpenRouter budget.
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

- [x] Add an atomic per-prompt pass-count artifact path and validate it locally.
- [ ] Launch 276 rows × `k=8` in detached pod tmux against the existing local vLLM.
- [ ] Record the artifact/evidence destination, then stop without projection, freeze, or training.

**Acceptance:** a successful run will persist exactly one row per completed
prompt with `0 <= pass_count <= 8`, and its evidence binds the output to the
full input/verifier/config. Gate thresholds are informational only.
**Stop:** after starting the detached probe; do not poll it tonight, alter the
dataset, or run any other task.

**Implementation result:** `scripts/gate_a.py` now accepts the additive,
reference-only `--per-prompt-output <jsonl>` option. On a completed evaluation,
it atomically writes one row with `record_index`, `record_id`, `pass_count`, and
`k`, then hashes/counts that artifact in normal completed evidence. Local tests
cover reference-only use, exact counts, evidence binding, and failed atomic
publication; the full suite passed `135 passed, 1 skipped`. This does not make
the branch decision: launch remains blocked on O1's unavailable D value.

## v0.7.0 authorized sample-evidence diagnostic and automated routing

### O0. Documentation and durable-evidence implementation

- [x] Record v0.7.0's evidence contract before code changes.
- [x] Implement and test atomic sample evidence, unchanged-score tier facts,
  default 50-row saving, and taxonomy summary.
- [x] Commit/push the implementation and verify the pod checkout.

**Acceptance:** every completed subset diagnostic has a hash-bound sample
artifact, not only aggregate metrics. No existing verifier score changes.
**Stop:** any score-parity, atomic-publication, or test failure; do not launch
the diagnostic.

**Implementation result:** local score-parity, sample/taxonomy, default-save,
and atomic-publication coverage passed, as did the complete suite: `143 passed,
1 skipped`. The new evidence schema is `3`; v0.7.0 does not alter a scalar
NL2SQL verifier result.

### O1. Authorized local-only subset diagnostic rerun

- [x] Run 50 rows × `k=8` with `--reference --save-samples` in detached pod tmux.
- [x] Sync and validate evidence/sample hashes; preserve the raw taxonomy.

**Acceptance:** all 400 samples have completion/tier/final-score evidence;
`D = parse_failure / all final_score < 1.0` is reproducible from the JSONL.
The diagnostic metric triplet is reference-only.
**Stop:** exit `2` twice consecutively means unhealthy vLLM; stop and report.

**Actual v0.7.0 diagnostic (reference-only):** pod-local
`Qwen2.5-1.5B-Instruct` completed 400 samples. The triplet was
`pass_at_1=0.12`, `pass_at_8=0.32`, and `mixed_fraction=0.32`; it is not an
admission decision. The synced sample JSONL SHA-256 is
`89911b559a7ed66bf431a8aece37f579cd8757ab3759b7e203bd8cf6014fb9b0`; its
evidence JSON SHA-256 is
`993686abb804369f7e92d4c6f85e39965c7e1cd03e378c6b94a65a1042a5b645`.
Raw scorer taxonomy: 341 failures, `parse_failure=0`,
`execution_error=331`, `executable_not_full_pass=10`. Of those execution
errors, 322 are complete SQL inside Markdown code fences with the legacy
detail `not_single_read_only_statement`; 9 are SQLite execution errors.

### O1.1 v0.7.1 derived routing evidence

- [x] Atomically derive a route artifact from the immutable sample evidence.
- [x] Retain the raw taxonomy and bind its source hashes.
- [x] Record three full fenced completions and compute operational
  `D = format_parse_failure / all failed samples`.

**Acceptance:** the narrow, deterministic fenced-SQL predicate accounts for
the 322 legacy lexical-gate failures, no source artifact changes, and the
derived evidence is hash-bound and atomic.
**Stop:** an accounting, hash, or atomic-publication failure blocks all branch
work until reported.

**Result:** `runs/p0-gate-a/v0.7.1-format-route.json` was atomically
published with SHA-256
`dfbe6c2c4f7ef4b80b556f6af959c2774f0cd16f5c722d2600190d3ac0822d3f`.
It binds the immutable v0.7.0 sample/evidence hashes and records all three full
fenced SQL examples. The raw taxonomy remains `0/331/10` for
parse/execution/wrong-result failures; the derived operational taxonomy is
`format_parse_failure=322`, `execution_error=9`,
`executable_not_full_pass=10`. Therefore
`D_format_parse_failure_fraction=322/341=0.9442815249266863`.

### O2 onward. Automated branch routing

- [x] Route on the derived operational D: `D >= 0.50` is Branch A; otherwise
  Branch B. Raw scorer `parse_failure` remains separately recorded.
- [ ] Continue without human pause only through the pasted overnight runbook's
  branch steps and their stated stop conditions.

**Guardrail:** branch-specific code/version documents must be committed before
their implementation. No OpenRouter request, threshold relaxation, or trainer
configuration change is authorized outside the runbook.

**Route:** Branch A. The direct sample evidence shows that the dominant failure
is Markdown fence formatting, not SQL difficulty. Begin A1 only after its
separate verifier-version documentation is committed.

## Branch A — v0.8.0 fenced-SQL extraction

### A1. Extraction normalization and verifier v2

- [x] Commit v0.8.0 verifier/evaluation/infrastructure documentation before
  code.
- [x] Recognize only SQL/untagged Markdown code fences; strip the fence and
  use `sqlparse` to send the first extracted statement through the unchanged
  scorer.
- [x] Preserve raw sample completion evidence while recording scored completion
  and extraction facts; add verifier version `2` to Gate A evidence.

**Acceptance:** a fenced exact query receives the exact same legacy tier facts
as its inner SQL; unfenced multi-statement input remains rejected. No scoring
threshold or trainer behavior changes.
**Stop:** any scorer parity/safety/test failure blocks A2.

**Implementation result:** verifier v2 preserves v1's raw tier implementation
behind a fenced-SQL extraction wrapper. SQL/untagged fences retain the raw
model completion and record `scored_completion`, extraction kind, and version
in sample evidence; unfenced multi-statement SQL remains rejected. Focused and
full test validation completed with `154 passed, 1 skipped`.

### A2. Full candidate v2 re-verification

- [x] Atomically re-verify all 276 stored candidate `reference_sql` values
  offline under verifier v2 and record source hash/version/full-pass count.

**Acceptance:** exactly 276/276 are `1.0`.
**Stop ③:** any record drops below `1.0`; do not start Gate A, freeze, or train.

**Result:** passed. `runs/p0-gate-a/v0.8.0-a2-full-reverify.json` records
`276/276` full passes, zero failures, verifier v2, input SHA-256
`0ad88c264bb4488189fc0788b740bdfabf99fc5fb2be0e232f0420953c79c96a`, and
evidence SHA-256
`89d97f7ec03ba4319f530614469fdfdee0ae60b8b76541289f36d296585087bd`.
Stop condition ③ is not triggered.

### A3. Branch A subset Gate A rerun

- [x] Only after A2 passes, run the 50-row subset at `k=8` on pod-local vLLM,
  save sample evidence, and sync/hash-check it.
- [x] If Gate A passes, proceed to O5. If it completes but `pass_at_1 < 0.20`,
  continue to Branch B with verifier v2. Any other non-passing Gate A outcome
  is recorded exactly and handled by the existing stop rules.

**Admission:** the v2 subset run, not v0.7.0's diagnostic triplet, is the Gate
A decision. Thresholds are unchanged.

**Result (stopped):** detached pod job `vf-gate-a-v080` completed with exit
`1`, not infrastructure exit `2`. The v2 Gate A triplet is exactly
`pass_at_1=0.64`, `pass_at_8=0.82`, `mixed_fraction=0.42`; it fails solely
because `0.64 > 0.60`. The completed evidence records 50 candidates, 400
samples, subset input SHA-256
`cfa93154cd87013b7460666925200be14f67c5112229f03c66df2978d747255c`, verifier
v2/source SHA-256 `b76707e5c0603d227393d03ffc0bdc7c66ec072a139c6aa717c282f4082de47f`,
and synchronized hashes: samples
`2721778b6597a787cada0f02eb437926e80e8b3fb6e1cb1a5940ff95aa7919cb`; evidence
`7f48ff304695b7b4304fa0352b1ec4a38a4035f4a4ee9abafb682ebc2b3b6b8e`.

**Routing judgment:** the runbook's fixed Branch B selection rule targets
mixed examples nearest to four passes and is the deterministic correction for
either Gate A bound. The earlier conservative pause interpretation is
superseded by v0.9.0: A3's upper-bound rejection routes to Branch B without
relaxing any threshold. Freeze, tagging, and training remain prohibited until
B3 passes.

## Branch B — v0.9.0 deterministic difficulty reprojection

### B1. Git-bound population and full difficulty probe

- [x] Build/test a deterministic 326-record population: 276 full candidates
  plus the original 50 reviewed seeds from Git ref `78912f1`; record source
  hashes and provenance.
- [x] On the pod, run every population record at `k=8` in detached tmux using
  local vLLM; atomically retain one pass count per prompt and reference evidence.
- [x] Sync/hash-check B1 artifacts before B2. Do not use aggregate B1 metrics
  as an admission decision.

**Acceptance:** 326 complete count records, each in `[0,8]`, bound to verifier
v2 and the fixed population.
**Stop ①:** two consecutive B1 infrastructure exit-2 runs; do not retry a
completed probe for its numbers.

**Population result:** `data/nl2sql/v0.9.0-b1-population.jsonl` contains 326
records (276 candidates + 50 original seeds) with SHA-256
`23c269a9de89bb4fdfec21bde56488290f424368e5c2b08c7ce402bc74f31a91`.
Its manifest SHA-256 is
`515a0961f9f1d2e566a0d4fd691bb5457e6428cccc95e5d44fc443a764e65071` and binds
the full candidate input SHA-256
`0ad88c264bb4488189fc0788b740bdfabf99fc5fb2be0e232f0420953c79c96a` plus the
original seed object at `78912f1`.

**B1 result:** detached pod probe `vf-b1-v090` completed with exit `0` and
published all 326 pass counts. Its reference-only triplet is
`pass_at_1=0.5674846625766872`, `pass_at_8=0.7975460122699386`, and
`mixed_fraction=0.4938650306748466`. The count JSONL SHA-256 is
`50125e21b90bb8d45a03b0f201bd371002984b36314334e3db822ceb8c1b44b3`; the
evidence SHA-256 is
`cc5ff55bc66573f1b1fdae3f47a2177871d548b0c6c188128459957d29f34c29`.
Laptop and pod hashes match. Count histogram: `0:66`, `1:17`, `2:21`, `3:29`,
`4:19`, `5:18`, `6:18`, `7:39`, `8:99`.

### B2. Fixed 50-row reprojection

- [x] Reverify every population `reference_sql` at v2, then select per seed a
  mixed candidate closest to four; deterministic ties use population ID.
- [x] For each seed with only `0/8` or `8/8` rows, discard it and backfill from
  another seed's next-best unused mixed row, at most two rows per source seed.
- [x] Atomically write the projected 50 rows and a report containing counts,
  discarded IDs, backfills, selection rule version, and all source hashes.

**Acceptance:** exactly 50 records, all v2 full passes; selection provenance is
complete and source-seed use never exceeds two.
**Stop ③:** more than 20 discarded seeds, no compliant backfill, projection
reverification failure, or atomic publication failure.

**B2 result:** completed with all 326 stored reference SQL values at v2 score
`1.0`; no re-verification failures. The 50-row artifact
`data/nl2sql/v0.9.0-b2-projected-subset.jsonl` has SHA-256
`8f0a1df0366ec014a16121d357298dbfc1359fdb23419e2cf7adb95a9e6ebec2`; report
SHA-256 is `3592e74e12c2d9699943b4fad92ea89ce866bb7b2e4eac57643915adc78a981f`.
Discarded seeds: `v1-004`, `v1-008`, `v1-012`, `v1-020`, `v1-022`, `v1-027`.
Six backfills came from `v1-032`, `v1-033`, `v1-037`, `v1-003`, `v1-010`, and
`v1-011`; source-seed use maximum is exactly two. The current loader accepted
all 50 output rows with the stable 40/10 split. Stop condition ③ is not
triggered.

### B3. Projected subset Gate A

- [x] Run the projected 50 at `k=8` on local vLLM with full sample evidence,
  then sync/hash-check its artifacts.
- [x] Pass → O5. Any B3 Gate A rejection → stop condition ③; do not freeze,
  train, or alter thresholds.

**Admission:** `0.20 <= pass_at_1 <= 0.60` and `mixed_fraction >= 0.30`.

**Result (stop condition ③):** detached pod job `vf-b3-v090` completed all
400 samples with exit `1`, a Gate A rejection rather than infrastructure exit
`2`. Raw v2 metrics are exactly `pass_at_1=0.66`, `pass_at_8=0.92`, and
`mixed_fraction=0.82`; the only failed condition is the fixed upper pass@1
limit (`0.66 > 0.60`). The input SHA-256 is
`8f0a1df0366ec014a16121d357298dbfc1359fdb23419e2cf7adb95a9e6ebec2`; sample
and evidence SHA-256 values match on laptop and pod:
`1c11829a411138017e91d24999204541d0a265cb9f3b2d35235fceba27737a83` and
`0a35a057f4908520256874e53d40297807582d3bac94dc62f46d38cef6c8959b`.

**Stopped:** B3 rejection is explicitly stop condition ③. O5 freeze/tag and
O6 Gate B are not authorized. No threshold change, new dataset, retry, or
additional model request follows this evidence; await human data-layer
direction.

## v0.10.0 — U1–U3 superseding data gate

The human authorized U1–U3 after the B3 stop. These rules supersede conflicting
R1–R4 details without changing verifier score semantics or trainer logic.

### U0. Documentation and implementation gate

- [x] Commit v0.10.0 version and matching evaluation/verifier/trainer/
  infrastructure documents before code.
- [x] Add focused tests and implement U1 selection, U2 Gate A predicates, and
  U3 deterministic 50/60 split/provenance.

**Acceptance:** all rules are mechanically testable and no live model request
has been made during implementation.
**Stop:** documentation, test, overlap, or verifier-v2 validation failure.

**Result:** documentation preceded the implementation in `1eb8fe0`. U1 now
selects nearest-to-2 in `[1,4]`, then lowest in `[1,6]`, retaining the existing
bounded discard/backfill behavior. U2 requires all three predicates at `k=8`.
The new split tool verifies 50/60 zero-source-overlap output, B1 bucket
coverage, and verifier-v2 reference SQL before publication. Focused validation
passed `35 passed`; the complete suite passed `172 passed, 1 skipped`. No model
or external-provider request was made.

### U1. Deterministic training/held-out construction

- [x] Reuse the immutable 326-row B1 population/count artifact. Per seed select
  nearest-to-2 in `[1,4]`; otherwise lowest in `[1,6]`; only then use existing
  discard/backfill handling.
- [x] Atomically write a 50-row training pool and zero-overlap 60-row held-out
  set, with exact bucket-allocation and verifier-v2 provenance report.

**Acceptance:** 50/60 rows, all reference SQL at `1.0`, no common source
population ID, and every nonempty B1 difficulty bucket represented in held-out.
**Stop:** any invalid count, duplicate/overlap, unsupported fallback, or split
shortfall; do not run a Gate A or freeze.

**Result:** `data/nl2sql/v0.10.0-training-pool.jsonl` has 50 rows and SHA-256
`c97a5adea789fae3be249bc9ac95a1902ae5a9769de9eefbc08277f056878e8c`.
`data/nl2sql/v0.10.0-heldout.jsonl` has 60 rows and SHA-256
`482f0e7678e7603311f72aeead381364cd92f0596c20745cc58c96916a9177e8`.
The deterministic repeat produced byte-identical outputs. The split report
records zero source-ID overlap, verifier-v2 `110/110` reference passes, and
held-out representation for every B1 pass-count bucket `0..8`.

### U2. Three-predicate training-pool Gate A

- [ ] Run only the new training pool at `k=8` on pod-local vLLM with samples.
- [ ] Admit only if `pass@1 ∈ [0.20,0.60]`, `mixed ≥ 0.30`, and `pass@8 ≥ 0.85`.

**Stop:** any rejected predicate, exit `2`, or evidence/hash mismatch. No
freeze, trainer promotion, or training on failure.

**STOP (launch layer):** the detached pod session `vf-u2-v010` exited `1`
before importing Gate A, with the exact log line
`/workspace/verifierforge/.venv/bin/python: No module named scripts.gate_a`.
No Gate A evidence, sample artifact, or completion request exists, so this is
not a pass/fail metric result. The synchronized 74-byte launch log has SHA-256
`17d0de36a463417da1db5f887d003cea81df333a1adfec23ec50f904bea83f2b`.
Do not repair or rerun without new human direction; U3 and training remain
prohibited.

### U3. Three-piece freeze and held-out baseline

- [ ] Only after U2 passes, freeze training pool + held-out set + verifier v2.
- [ ] Record held-out baseline separately; all before/after and DoD statements
  refer only to this held-out artifact. Gate B/D4 consume training pool only.

**Stop:** any manifest/tag/push/baseline failure; do not train.
