Codex Session ID: 019f5f9e-9bbb-7730-9ecd-5db8c83f1e13

## v0.15.2 serving record

- Model: canonical step-350 `verifierforge-step-350` export, served by vLLM
  0.10.2 on an L4 24GB pod.
- Redacted launch shape: `vllm serve <step-350-hf-dir> --host 0.0.0.0 --port
  8000 --dtype bfloat16 --max-model-len 2048 --disable-log-stats --api-key
  <ignored-env-secret>` in detached tmux at warning log level.
- Local acceptance passed: `/v1/models` listed the model and a real NL→SQL
  completion returned `SELECT name FROM users;`.
- Intended public base URL:
  `https://e5bae2au0f867m-8000.proxy.runpod.net/v1`. The public route timed
  out after 30 seconds with zero bytes, so it is not considered deployed and
  no proxy canary or LivePassRate range is claimed.
- The RunPod console image did not record an hourly price; it is intentionally
  left unknown rather than inferred.

## v0.16.0 S3 record

- A real-bucket round trip restored a checkpoint with SHA-256
  `a60b4cf7a2129129e6cf8a181b435ebf04f1be49037a1e1c76d936bf958a64e9`,
  recovered 50 ordered metrics, and left a simulated interrupted upload
  unpublished. See `docs/p0-run-sheet.md` for the evidence hash and the
  in-progress GPU kill/resume proof.
