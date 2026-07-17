# VerifierForge judge path (under ten minutes)

This path inspects the real FastAPI routes against committed D4 evidence. It
needs Python 3.11+ but no GPU, model weights, cloud account, or API key.

## 1. Install and verify (about 3 minutes)

```bash
python -m pip install -r requirements-app.txt
pytest -q
```

Expected current result: `246 passed, 1 skipped` (the optional live-S3 test is
skipped without explicit credentials).

## 2. Start the one-command reviewer sandbox (about 1 minute)

```bash
bash scripts/start_reviewer_sandbox.sh
```

It opens only loopback endpoints and keeps running until you press Ctrl-C:

```text
API:   http://127.0.0.1:8012/docs
Proxy: http://127.0.0.1:8013/v1/chat/completions
```

The API serves committed immutable evidence; the proxy is deterministic fake
mode, so this path needs no API key or model-provider request.

## 3. Inspect the immutable evidence (about 1 minute)

In one terminal:

```bash
curl http://127.0.0.1:8012/jobs
curl http://127.0.0.1:8012/jobs/d4-m3-1p5b-r1-v0125/metrics
curl http://127.0.0.1:8012/jobs/d4-m3-1p5b-r1-v0125
```

The selected main job is `d4-m3-1p5b-r1-v0125`; the control is
`d4-m4-0p5b-random-v0126`. Artifact mode validates the same Pydantic shapes as
the local-runs API and rejects route mutation rather than pretending the demo
is live state.

## 4. Check the result and provenance (about 3 minutes)

```bash
cat data/demo-artifacts/manifest.json
shasum -a 256 data/demo-artifacts/jobs/d4-m3-1p5b-r1-v0125/metrics.jsonl
```

The manifest records held-out pass@1 `0.5833 → 0.7833`, pass@8
`0.7667 → 0.9000`, and the main metrics SHA-256
`be3fdb965dc72a2333761a8f50181053af3c4b5355e83624c3784b6be30cd433`.
The 0.5B random-reward control JSONL is included next to it.

## 5. Inspect the engineering claims (about 3 minutes)

- [`README.md`](README.md) explains the system boundary and limitations.
- [`docs/dev_doc_v0.md`](docs/dev_doc_v0.md) is the external design/evidence
  record; [`docs/p0-run-sheet.md`](docs/p0-run-sheet.md) is the detailed live
  operational history.
- [`core/rewards/nl2sql.py`](core/rewards/nl2sql.py) contains the executable
  tiered verifier.
- [`core/storage/s3.py`](core/storage/s3.py) and
  [`tests/test_s3_storage.py`](tests/test_s3_storage.py) show manifest-last
  S3 publication and its contract tests.

## Honest boundary

The repository intentionally does not include weights, credentials, or a paid
provider dependency. Local vLLM loading of the selected export passed; the
public RunPod proxy timed out during delivery verification, so no public
canary/guardian result is claimed. The artifact route above is the supported
review surface.
