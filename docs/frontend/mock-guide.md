# Mock Guide (v0.5.0)

How to run the frontend mock and what each field means in plain language.

## Start the mock

From the repo root:

```bash
python mock/server.py
```

Listens on `http://0.0.0.0:8001`. Interactive OpenAPI UI is at
`http://127.0.0.1:8001/docs` (FastAPI built-in).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/jobs` | List fixture jobs (queued / running / done / failed at least). |
| `GET` | `/jobs/{job_id}` | One job, full shared `Job` shape. |
| `GET` | `/jobs/{job_id}/metrics` | Just the training curves for charts. |
| `POST` | `/jobs` | Fake-create a queued job in memory (no `runs/` write). |
| `GET` | `/clusters` | Three task clusters for the product home. |
| `GET` | `/clusters/{id}` | One cluster; live cluster embeds routing + guardian curve. |

## Job fields (one-liners)

- `job_id` — stable id for URLs and lists.
- `template` — which vertical recipe (e.g. `nl2sql`).
- `status` — lifecycle: `queued`, `running`, `done`, `failed`, or `early_stopped`.
- `model` — base or tuned model name shown in UI.
- `created_at` — when the job was created (ISO time).
- `metrics.steps` — x-axis training steps.
- `metrics.reward_mean` — mean reward over the batch at each step.
- `metrics.pass_at_1` — held-out k=1 pass rate during training (eval, not live).
- `metrics.entropy` — policy entropy; collapse often shows a crash here.
- `control.pass_at_1` — spurious-control curve for comparison.
- `report` — post-run summary; null while running/queued.
- `report.baseline_pass_at_1` — score before tuning.
- `report.final_pass_at_1` — score after tuning.
- `report.control_final_pass_at_1` — control run final score.
- `report.verdict` — `real_gain`, `suspect_formatting`, or `collapsed`.
- `report.narrative` — short GPT-style explanation for the report page.
- `report.projected_monthly_savings_usd` — optional money story for the cluster.
- `report.arena` — optional side-by-side prompt battle samples + `win_rate`.
- `endpoint` — optional OpenAI-compatible `base_url` + `model_name` after deploy.

## Cluster fields (one-liners)

- `cluster_id` — slug id (e.g. `support-ticket-extraction`).
- `name` — human label in the UI.
- `monthly_calls` — estimated monthly call volume.
- `monthly_cost_usd` — estimated monthly LLM spend for this cluster.
- `trainable` — whether forging a verifier for this cluster is allowed.
- `status` — `discovered` (seen), `forging` (training), or `live` (routed).
- `job_id` — linked forge job if any; else null.
- `routing` — optional live routing knobs (`enabled`, `canary_percent`, `target_model`).
- `live_pass_rate` — optional guardian series; points use `pass_rate` (online
  rolling score), **not** `pass_at_1` (offline k-sample eval).

## Arena sample fields

- `prompt` — user / NL input shown in the arena.
- `baseline_output` / `tuned_output` — before vs after model text.
- `baseline_score` / `tuned_score` — judge or verifier scores for that pair.
