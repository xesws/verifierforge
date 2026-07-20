# VerifierForge judge path (under ten minutes)

This path inspects committed evidence and the real product surface with
deterministic local bindings. It needs Python 3.11+ but no GPU, model weights,
cloud account, API key, or paid request.

## 1. Install and verify (about 3 minutes)

```bash
python -m pip install -r requirements-app.txt
pytest -q
```

Expected at this revision: `474 passed, 1 skipped`. The skip is the explicitly
credential-gated live S3 test.

## 2. Start the reviewer sandbox (about 1 minute)

```bash
bash scripts/start_reviewer_sandbox.sh
```

It binds only loopback and runs until Ctrl-C:

```text
API:   http://127.0.0.1:8012/docs
Proxy: http://127.0.0.1:8013/v1/chat/completions
```

The API reads immutable committed evidence; the proxy uses a deterministic fake
upstream. This fallback remains the no-secret path for a fresh clone.

### Hosted full reviewer

The accepted public reviewer is:

```text
https://verifierforge-production.up.railway.app
```

It uses Supabase, deterministic mock Agent, and the same 21-operation contract.
Tuned inference is scale-to-zero rather than a permanently rented endpoint. It
requires HTTP Basic Auth: username `judge`,
invitation code shared separately. A request without auth returns 401;
`/healthz` remains public. `VF_AUTOPROVISION=false`, so Start Forge returns an
explicit disabled response and cannot create a paid resource.

On Discover, **Wake model** is a separate, explicitly confirmed serving action.
It permits only one session, has a `$5` session cap, and normally reaches ready
in about 4.5 minutes. The page polls `cold → provisioning → loading → ready`;
after 30 idle minutes the production reaper deletes the pod. Reports and arena
remain usable throughout cold start. Do not confuse this with Start Forge:
training autoprovision remains disabled.

If the hosted service is unavailable, the owner may recreate the local full
fallback with:

```bash
bash scripts/start_reviewer_sandbox.sh --mode full
```

This publishes an ephemeral `trycloudflare.com` URL backed by Supabase, the
configured real tuned endpoint, mock Agent and mock Start Forge lifecycle. It
requires HTTP Basic Auth: username `judge`, invitation code shared separately.
The launcher never prints the code; it records it only in the ignored runtime
path it reports. A request without auth returns 401. This full path calls no
paid LLM and provisions no GPU. It is a fallback, not the current public URL.

## 3. Inspect the training result (about 2 minutes)

```bash
curl http://127.0.0.1:8012/jobs
curl http://127.0.0.1:8012/jobs/d4-m3-1p5b-r1-v0125/metrics
curl http://127.0.0.1:8012/jobs/d4-m3-1p5b-r1-v0125
cat data/demo-artifacts/manifest.json
```

The 60-row held-out report records pass@1 `0.5833 → 0.7833`, pass@8
`0.7667 → 0.9000`, and selected step 350. The companion job
`d4-m4-0p5b-random-v0126` is the 0.5B random-reward control.

Verify the main public metric bytes:

```bash
shasum -a 256 data/demo-artifacts/jobs/d4-m3-1p5b-r1-v0125/metrics.jsonl
```

Expected SHA-256:
`be3fdb965dc72a2333761a8f50181053af3c4b5355e83624c3784b6be30cd433`.

## 4. Demo Discover → Agent → approval locally (about 2 minutes)

In a separate terminal, run the real API/UI with the deterministic Agent
binding and an isolated SQLite file:

```bash
VF_AGENT_ENABLED=true \
VF_AGENT_BINDING=mock \
VF_DB_BACKEND=sqlite \
VF_PROXY_DB_PATH=./runs/judges-agent.sqlite3 \
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8014
```

Open `http://127.0.0.1:8014/discover`. On **Data Pull SQL**:

1. inspect `95,000 SQL queries/month` and `$5,500/month`;
2. click **Input**, keep the default repository source, and confirm;
3. click **Analyze** to see the mock-bound decision, rationale and config;
4. click **Approve & Forge** and observe the durable approval receipt;
5. on the hosted reviewer, confirm the separate spend boundary and click
   **Start Forge**; the default-off flag visibly rejects it without spending.

This UI path is structurally real but intentionally zero-cost. The separate
live Gate C evidence is `1.0 / 1.0 / 0 / 1.0` under tag
`agent-gate-c-pass`; the production source/decision/approval record is in
[`docs/p0-run-sheet.md`](docs/p0-run-sheet.md). Approval writes intent only—it
does not launch a GPU from the browser. Start is a distinct action; the public
reviewer is configured with the mock provisioner but keeps the autoprovision
gate closed.

Frontend implementers can use the frozen request/response examples in
[`docs/frontend/api-contract-v1.md`](docs/frontend/api-contract-v1.md); the
real and mock OpenAPI schemas are parity-tested for all listed operations.

## 5. Inspect delivery and persistence evidence (about 2 minutes)

```bash
cat assets/lane-a-v0.22.5-public-proof.json
cat assets/lane-a-v0.22.5-canary-summary.json
git tag --list '*complete' 'agent-gate-c-pass' 'lane-a-closeout'
```

The ephemeral public proof returned `SELECT name FROM users;`. The 200-request
run produced 120 default / 80 tuned, Guardian final `0.85`; canary zero then
produced 20 default / 0 tuned. `db-1-complete`, `db-2-complete`, and
`db-3-complete` mark repository extraction, Supabase cutover, and credential
hardening.

## Honest boundary

- One NL→SQL vertical is not a broad benchmark.
- The reviewer has a fixed Railway URL. Each on-demand GPU inference session
  uses a temporary Cloudflare quick tunnel and is not an SLA; the registry
  drops back to cold and report reads remain safe when a session is absent.
- Forge Agent remains default-off despite passing Gate C.
- P-2 completed the separately authorized RunPod path: orphan cleanup, a
  0.5B/100-step S3 run, post-training vLLM models/completion gate, 137-object
  SHA collection, and target-absent/raw-prefix-zero deletion. Tag
  `provisioner-p2-live` records it; billing reconciliation remains asynchronous.
- P-4 separately proved the product approval→Start→real RunPod readiness→delete
  wiring at a `$0.000623` provider estimate. `VF_AUTOPROVISION` remains
  default-off; the reviewer full sandbox explicitly uses the mock adapter.
- Nebius is the next adapter on the roadmap and is not implemented.
- Live scale-to-zero acceptance ran twice on RTX 4000 Ada at `$0.20/hr`:
  282.14s and 266.68s cold starts, 200/200 traffic with 111 default / 89 tuned,
  Guardian 0.95, and provider inventory zero after both idle reaps. Evidence is
  `docs/evidence/serving/v0.34.0-sv5-live.json`.
- The repository contains no weights, credentials, raw traffic bodies, or
  requirement for a paid provider during review.
