# VerifierForge judge path (under ten minutes)

This path inspects committed evidence and the real product surface with
deterministic local bindings. It needs Python 3.11+ but no GPU, model weights,
cloud account, API key, or paid request.

## 1. Install and verify (about 3 minutes)

```bash
python -m pip install -r requirements-app.txt
pytest -q
```

Expected at this revision: `478 passed, 1 skipped`. The skip is the explicitly
credential-gated live S3 test.

## 2. Start the reviewer sandbox (about 1 minute)

The primary product UI is the fixed Vercel frontend:

```text
https://verifierforge-web.vercel.app
```

Enter the invitation code shared separately. The browser keeps it only in
session storage; it is never part of the URL or frontend build. The page calls
the Railway control plane below and follows the same frozen 22-operation API.

The UI enforces this visual path: **Discover** → inspect three real cluster
cards, Input, Analyze, and the Agent decision → **Forge** → review the proposed
config, record approval, then explicitly continue with the completed flagship
run → **Runs** → inspect the 400/200 curves → **Proof** → inspect
`58.3% → 78.3%`, arena, savings, and verdict → **Ship** → inspect canary,
Guardian, and scale-to-zero serving. Locked steps cannot be deep-linked around.

For a clone-only fallback, start the local reviewer sandbox:

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

### Hosted API control plane

The accepted public reviewer is:

```text
https://verifierforge-production.up.railway.app
```

It uses Supabase, a Gate-C-qualified live `gpt-5.6-luna` Forge Agent, and the
same 22-operation contract. Clicking **Analyze** explicitly requests a fresh
run and displays its provider/model, trace ID, timestamps, token counts,
read-only tool inputs/outputs, and validated terminal decision. The panel is an
audit receipt—not hidden chain-of-thought—and labels mock/cached results rather
than passing them off as live.
Tuned inference is scale-to-zero rather than a permanently rented endpoint. It
requires HTTP Basic Auth: username `judge`,
invitation code shared separately. A request without auth returns 401;
`/healthz` remains public. `VF_AUTOPROVISION=false`, so Start Forge returns an
explicit disabled response and cannot create a paid resource.

Start a live-inference walkthrough only after reaching **Ship**, then click
**Wake model**. The action
permits only one session and has a `$5` cap. While its visible state advances
through `provisioning` and `loading`, inspect the flagship Job report: the two
curves, held-out arena, and `0.5833 → 0.7833` result do not need a live GPU.
The Ship activity window shows only real registry state/detail changes, elapsed
time, and the measured 267–282 second estimate; it does not fabricate pod logs.
`ready` is shown before the tuned-only SQL generation probe is offered, while a
failed wake shows a readable reason and leaves reports available. After SQL is
generated, click **Run SQL on frozen demo data**. A local Web Worker creates a
fresh SQLite/WASM database, loads the same synthetic frozen schema used by the
verifier, and displays the actual columns and rows (or the real SQLite error),
execution ID, hashes, and timing. This second action makes no API, GPU, or LLM
request and remains available if the serving session returns to cold.
After 30 idle minutes the production reaper deletes the pod. Do not confuse
this with Start Forge: training autoprovision remains disabled.

If the hosted service is unavailable, the owner may recreate the local full
fallback with:

```bash
bash scripts/start_reviewer_sandbox.sh --mode full
```

This publishes an ephemeral `trycloudflare.com` reviewer URL backed by
Supabase, the dynamic serving registry, mock Agent and mock Start Forge lifecycle. It
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

## 4. Demo Discover → Agent → approval (about 2 minutes)

In a separate terminal, run the real API/UI with the deterministic Agent
binding and an isolated SQLite file:

```bash
VF_AGENT_ENABLED=true \
VF_AGENT_BINDING=mock \
VF_DB_BACKEND=sqlite \
VF_PROXY_DB_PATH=./runs/judges-agent.sqlite3 \
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8014
```

Prefer `https://verifierforge-web.vercel.app/discover`. For local API-only
inspection, open `http://127.0.0.1:8014/discover`. On **Data Pull SQL**:

1. inspect `95,000 SQL queries/month` and `$5,500/month`;
2. click **Input**, keep the default repository source, and confirm;
3. click **Analyze** to run the hosted Agent and inspect the fresh run receipt,
   rationale, config, and four-step read-only tool trace;
4. continue to **Forge**, click **Approve & Forge**, and observe the durable
   approval receipt;
5. confirm the separate Start spend boundary remains disabled, then choose the
   explicitly labelled completed flagship run to continue without spending.

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
The live SQL runner is deliberately not operation 23: it is browser-local and
keeps the frozen HTTP contract at 22 operations.

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
