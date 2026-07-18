Codex Session ID: 019f5f9e-9bbb-7730-9ecd-5db8c83f1e13

## v0.22.5 Lane A closeout

- Replacement serving executor: NVIDIA L4 24GB; hourly price was not present
  in the supplied console evidence and is therefore recorded as unknown.
- Runtime: torch 2.8.0+cu128, vLLM 0.10.2, transformers 4.57.6,
  tokenizers 0.22.2, huggingface_hub 0.36.2.
- Canonical step-350 tree SHA-256:
  `7bde853af7c82405fd1356de9bad9b6c421de45a45ce747f63ea2f8a27eda658`.
- Redacted launch shape: `VLLM_API_KEY=<stdin-injected> vllm serve
  <step-350> --served-model-name vf-demo --host 0.0.0.0 --port 8000 --dtype
  bfloat16 --max-model-len 2048 --gpu-memory-utilization 0.45` in detached tmux.
- RunPod's undeclared port-8000 proxy returned 404. A detached Cloudflare quick
  tunnel exposed the same process; the endpoint URL is stored only in ignored
  `.env` because quick-tunnel hostnames are ephemeral.
- Public SDK result: `SELECT name FROM users;`. Canary: 120 default / 80 tuned
  over 200 requests, 13 new Guardian points, final LivePassRate 0.85. After
  reset, 20/20 requests used default and zero used tuned.

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
