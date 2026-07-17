# VerifierForge — three-minute video script

**Runtime:** 3:00 exactly.  
**Rule:** show terminal/API output and committed evidence, never secrets,
weights, provider dashboards, or an unverified public endpoint.

| Time | Visual | Voiceover / on-screen copy |
| --- | --- | --- |
| 0:00–0:12 | Title, one NL request and one SQL result. | “VerifierForge turns a narrow, checkable task into a small-model training run — and asks whether the gain is real before shipping it.” |
| 0:12–0:32 | `NL2SQLVerifier` tiers beside a SQLite schema. | “For NL to SQL, success is programmatic: parseable SQL earns partial credit, executable SQL earns more, and the correct result set earns one. That makes every generated answer testable.” |
| 0:32–0:50 | Frozen-data / held-out diagram, then `gate_a` evidence. | “We freeze prompts and the verifier before training. A held-out set stays separate, and a random-reward control runs beside the main job so one rising curve is not the whole story.” |
| 0:50–1:15 | Artifact API: `/jobs`, metrics endpoint, selected step label. | “Here is the committed D4 record. On 60 held-out rows, pass at one moves from 0.5833 to 0.7833. Pass at eight moves from 0.7667 to 0.9000. Step 350 wins by held-out pass at one, not by a hand-picked chart.” |
| 1:15–1:37 | Main and random-reward control curves side by side. | “The control is a 0.5B run with random reward. It is a falsification reference, not a marketing prop: this result is one NL-to-SQL task, not a general benchmark.” |
| 1:37–1:58 | `vf train`, tmux, Storage diagram, checkpoint publication. | “Training happens on a disposable GPU worker. The laptop owns the control plane; metrics and checkpoints go through a Storage contract. The worker can be replaced without treating it as the source of truth.” |
| 1:58–2:18 | S3 manifest-last animation and real-bucket evidence JSON. | “S3 publishing is manifest-last, so an interrupted upload is not a checkpoint. A real-bucket proof restored a checkpoint by SHA, recovered fifty ordered metrics, and left an interrupted upload unpublished.” |
| 2:18–2:39 | Proxy fake upstream, route switch and guardian overlay. | “The delivery side is OpenAI-compatible. A routing switch can send a canary to a tuned model, while a sampled verifier guardian scores SQL traffic off the request path. Development uses a zero-cost deterministic fake upstream.” |
| 2:39–2:52 | Limitations card. | “The public serving gateway timed out during this build, so we do not claim an internet-facing canary. Local vLLM serving passed; the honest reviewer path is this tracked artifact API.” |
| 2:52–3:00 | `JUDGES.md`, test command, closing title. | “Clone, run the artifact API, and inspect the evidence in under ten minutes. VerifierForge: train small models, but ship proof.” |

## Recording checklist

- Use `VF_API_DATA_MODE=artifacts` and the committed `d4-m3-1p5b-r1-v0125`
  job for all result screens.
- Show the response body and the manifest hash, not raw hidden data.
- Do not show `.env`, API headers, terminal process arguments, or a public
  endpoint that has not passed the recorded gateway test.
