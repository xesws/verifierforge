# P0 Run Sheet — v0.6.1 data freeze

**Status:** in progress
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

- [ ] Add/test a projection tool. For every `v1-001` through `v1-050`, re-verify candidates and choose the lexicographically smallest candidate `id`.
- [ ] A seed with no eligible candidate—whether unprocessed or processed with zero accepted variants—uses its original verifier-checked seed row. Preserve `seed_id`, `source_candidate_id` (`null` for fallback), and selection reason.
- [ ] Count all fallback rows together; if the count exceeds 10, do not overwrite the trainer fixture.

**Acceptance:** exactly 50 unique canonical IDs, one per seed; every selected/fallback row scores `1.0`; fallback count is at most 10; the existing loader produces its stable 40/10 split.
**Stop:** missing/duplicate/invalid seed, re-verification failure, or fallback count above 10. Record counts and do not run Gate A.

**Selection rule:** candidate rows are grouped by `seed_id`; sort eligible rows by `id` ascending and choose the first. Original seed fallback is used only when that group is empty.
**Result:** pending.

### 3. S3 — real Gate A

- [ ] Harden Gate A evidence/input validation without changing its thresholds.
- [ ] Run full-candidate reference Gate A first, then the 50-row subset, both at `k=8`; save structured evidence for both.
- [ ] Record each raw triplet exactly: `pass_at_1`, `pass_at_8`, `mixed_fraction`.

**Acceptance:** both evaluations complete; the subset has `0.20 <= pass_at_1 <= 0.60` and `mixed_fraction >= 0.30`. Full-set values are reference only.
**Stop:** either evaluation errors, or the subset misses either threshold. Commit raw figures/evidence; do not relax thresholds or train.

**Full result:** pending.
**Subset result:** pending.

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
