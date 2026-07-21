# Nora Frontend Branch Review

**Reviewed:** 2026-07-20

**Branch:** `feature/v0.18.0-static-frontend`

**Commit:** `642e3354457529d0c51d65a10bb2e2b4b36008ec` (`v0.18.0 Frontend: add static product demo`)

**Author recorded by Git:** `daaabing <hzwang@ucdavis.edu>`

**Review baseline:** `main` at `9ded4905ff7c04893215a6e45a4efa816829ee1c`

## Executive verdict

This is a polished, buildable **static product demo**, not an implementation of
the current frontend API contract. It is useful as the visual base for the real
frontend, but none of the 21 frozen operations is wired. The safe integration
path is to preserve Nora's commit by cherry-picking it onto a new branch from
current `origin/main`, then add the API/auth state layer there. Do not merge or
continue directly on the reviewed branch: it is one commit ahead of its base
but 102 commits behind current `main`.

The review was read-only with respect to Nora's branch. Its only delta from its
merge base (`f7b4403`) is 42 added files under `frontend/` plus its two v0.18.0
documents; it changes no backend, mock, contract, or existing test file.

## 1. Stack, engineering shape, and five pages

The frontend lives entirely under `frontend/`. It uses Vite 6.4.3, React
18.3.1, TypeScript 5.6.3 in strict mode, React Router 7.18.1, Recharts 2.15.4,
Lucide React 0.468.0, local Manrope/IBM Plex Mono font packages, and handwritten
CSS. npm and `package-lock.json` lockfile v3 are used. The source is divided
into `src/app`, `pages`, `components`, `data`, `styles`, `types`, and `utils`;
`scripts/generate-demo-data.mjs` converts committed training evidence into a
generated JSON fixture before each build.

The five routes are declared in `frontend/src/app/App.tsx:14-20`:

| Page | Completion | Evidence and limitation |
| --- | --- | --- |
| `/discover` | **UI present, data hard-coded** | Three responsive cluster cards are rendered from `productScenario.ts:13-47`. Volume and monthly cost are visible, but there is no Input, Analyze, analyzer decision, or Wake action. |
| `/forge/new` | **UI present, local simulation only** | The editable task/schema/examples form works. Submit writes a `LocalJob` to browser `localStorage` (`ForgePage.tsx:21-36`, `localJobs.ts:5-17`) and explicitly makes no backend request. |
| `/jobs/:jobId` | **Static flagship view; other jobs are a shell** | The known D4 job displays real committed main/control curves and metric tabs. Every other ID shows only a locally queued placeholder (`JobPage.tsx:19-24`). |
| `/reports/:jobId` | **UI present, data hard-coded** | The held-out chart and verdict are visible, but the component ignores `jobId` and always renders one report. Savings and arena values are product fixtures. |
| `/ship/data-pull-sql` | **UI present, local simulation/shell** | Routing switch, slider, and target select persist only to `localStorage`; Guardian is intentionally an empty “No samples” panel (`ShipPage.tsx:9-29`). |

No page is “complete and usable” against the current judge workflow. Visual
hierarchy and responsive presentation are substantially complete; runtime data,
authentication, error/loading states, and paid-action boundaries are not.

## 2. API wiring and frozen-contract differences

There is no API layer. A source scan found no `fetch`, Axios, XHR, WebSocket,
`import.meta.env`, `VITE_*`, or baseURL. Therefore the branch has **no current
baseURL environment variable**. The frozen integration guide specifies
`VITE_VF_API_BASE_URL` for the future wired frontend.

All 21 operations in `frontend-api-v1` are absent:

| # | Frozen operation | Nora branch behavior |
| ---: | --- | --- |
| 1 | `GET /jobs` | No call or jobs-list data source. |
| 2 | `POST /jobs` | Replaced by a different localStorage-only `LocalJob`. |
| 3 | `GET /jobs/{job_id}` | One hard-coded evidence job; other IDs read localStorage. |
| 4 | `GET /jobs/{job_id}/metrics` | Build-time `trainingMetrics.json`, no call. |
| 5 | `GET /clusters` | Three hard-coded product fixtures. |
| 6 | `GET /clusters/{cluster_id}` | No call or dedicated cluster-detail view. |
| 7 | `POST /clusters/{cluster_id}/agent/analyze` | Missing. |
| 8 | `GET /clusters/{cluster_id}/agent/decision` | Missing. |
| 9 | `POST /agent-decisions/{decision_id}/approvals` | Missing. |
| 10 | `GET /agent-decisions/{decision_id}/approval` | Missing. |
| 11 | `POST /approvals/{approval_id}/start-forge` | Missing; no second confirmation or spend boundary. |
| 12 | `GET /approvals/{approval_id}/forge-execution` | Missing; no execution polling. |
| 13 | `GET /clusters/{cluster_id}/routing` | Replaced by browser-local state. |
| 14 | `PUT /clusters/{cluster_id}/routing` | Replaced by browser-local state. |
| 15 | `GET /clusters/{cluster_id}/live-pass-rate` | Missing; Guardian is always empty. |
| 16 | `GET /clusters/{cluster_id}/sample-source` | Missing; there is no Input step. |
| 17 | `PUT /clusters/{cluster_id}/sample-source` | Missing; no source identity/SHA approval. |
| 18 | `GET /settings/provider-credentials/{provider}` | Missing. |
| 19 | `PUT /settings/provider-credentials/{provider}` | Missing. |
| 20 | `POST /serving/wake` | Missing; no literal spend confirmation. |
| 21 | `GET /serving/status` | Missing; no cold/provisioning/loading/ready feedback. |

Important shape differences:

- **Clusters:** local `id/callsPerMonth/spendPerMonth` differs from
  `cluster_id/monthly_calls/monthly_cost_usd`; local data lacks `trainable`,
  `job_id`, `routing`, `live_pass_rate`, `approved_sample_source`, and
  `analyzer_decision`.
- **Analyzer:** no decision, rationale, confidence, config, cache status, or
  decision ID is represented.
- **Approve / Start Forge:** local “Queue forge job” collapses advisory input,
  human approval, and paid execution into a non-network simulation. It does not
  implement `approved_by`, matching `requested_by`, or literal
  `confirm_provider_spend: true`.
- **Jobs:** the local status union is only `queued | done`; the contract also
  supports `running`, `failed`, and `early_stopped`.
- **Arena:** Nora has seven demo rows with `baseline/tuned/reason`; the contract
  has exactly ten held-out rows with `baseline_output`, `tuned_output`,
  `baseline_score`, and `tuned_score`, nested under an arena with `win_rate`.
- **Savings:** Nora hard-codes `$4,300`; the frozen flagship payload returns
  `$3,850` plus formula, assumptions, current/projected costs, and provenance.
- **Routing:** local `{enabled, canary, target}` differs from
  `{cluster_id, enabled, canary_percent, target_model}` and never reaches the
  API.
- **Guardian and serving:** LivePassRate, Wake, status polling, cold fallback,
  failure feedback, and endpoint state are absent.
- **Authentication:** there is no invitation gate or Basic Authorization
  handling. The reviewer invite code must be entered at runtime, never compiled
  into a Vite environment variable.

## 3. Security and dependency review

| Check | Result |
| --- | --- |
| Hard-coded URL/key/credential | No application URL, provider key, Basic/Bearer token, password, or private key found. Registry URLs occur only in `package-lock.json`. |
| `.env` or sensitive file committed | None found in the branch delta. Root `.gitignore` ignores exact `.env`, but `.env.local` and `.env.production` are **not** ignored; future integration should use `.env*` with an explicit `!.env.example` exception. |
| Dependency anomalies | No cloud, model, GPU, or backend SDK. `node_modules` is about 150 MB and `dist` about 3.9 MB. `npm audit` reports zero vulnerabilities. |
| Maintenance/performance | npm warns that Recharts 1.x/2.x is no longer active. Vite warns that the main minified JS chunk is 700.86 KB (199.59 KB gzip), above 500 KB. Neither blocks the static demo. |
| Backend/contract changes | Zero. No changed path under `app/`, `core/`, `mock/`, `tests/`, or `docs/frontend/api-contract-v1.md`. |

## 4. Local run and visual evidence

Environment: Node `v22.22.3`, npm `10.9.8`.

```bash
cd frontend
npm ci
npm run lint
npm run build
npm run dev -- --host 127.0.0.1 --port 5179
npx vite preview --host 127.0.0.1 --port 4180
```

Install, lint, and build passed. The build regenerated the fixture as “400
main, 200 control,” transformed 2,235 modules, and left the reviewed branch's
tracked files clean. `npm run preview` is not defined; the successful production
check used the local `npx vite preview` binary.

Both dev and production preview returned HTTP 200 for `/`, `/discover`,
`/forge/new`, `/jobs/d4-m3-1p5b-r1-v0125`,
`/reports/d4-m3-1p5b-r1-v0125`, and `/ship/data-pull-sql`. An unknown path also
returns the SPA shell and is redirected client-side to Discover.

The current environment exposed no controllable browser, so a fresh click and
console pass was not possible. The four inspected images in
`assets/frontend-review/` are byte-identical copies of Nora's committed
screenshots: desktop/mobile Discover, desktop Job, and desktop Report. They
show a polished responsive UI but are not independent runtime captures. No
Forge or Ship screenshot exists on the branch.

## 5. Gap list for the full judge path

| Work item | Estimate | Suggested owner |
| --- | ---: | --- |
| Cherry-pick visual base onto current main and establish v0.35.0 integration branch/tests | 1 h | Agent can do |
| Typed API client, `VITE_VF_API_BASE_URL`, runtime Basic Auth, errors/loading/cache policy | 3–4 h | Agent; owner decides invite-code persistence UX |
| Discover real three-card data, Input/sample-source approval, Analyze and decision display | 3–5 h | Agent can do |
| Approve record, explicit Start Forge second confirmation, execution-state polling | 3–5 h | Agent; owner approves spend-warning copy |
| Jobs list/detail and real main/control curves; report arena 10, savings assumptions, verdict/provenance | 4–6 h | Agent can do |
| Routing GET/PUT and real LivePassRate chart with cold/error states | 2–3 h | Agent can do |
| Wake model button, spend confirmation, status polling, retry/failure feedback | 3–4 h | Agent; owner decides cost presentation |
| Cross-page empty/offline/degraded behavior so reports remain usable while serving is cold | 2–3 h | Agent plus design judgment |
| Vercel SPA config, backend CORS origin, preview/final environment setup | 2–3 h | Agent; owner supplies Vercel project access |
| Component/API tests, judge-path E2E, accessibility, mobile verification, chunk splitting | 3–5 h | Agent can do |

Expected implementation effort is approximately **26–39 hours**, excluding
owner response time and visual redesign. The existing UI saves substantial
layout work; most remaining effort is stateful integration and failure safety.

## 6. Merge and Vercel recommendation

**Recommendation: cherry-pick into a new short-lived branch.** Create
`feature/v0.35.0-frontend-api-integration` from current `origin/main`, then
cherry-pick `642e335`. This preserves Nora's Git authorship and the complete
visual base while avoiding development atop a branch 102 commits behind the
API, Supabase, Agent, Provisioner, and scale-to-zero work. Do not merge the old
branch directly, and do not delete it until the owner accepts the integration.

Vercel settings for the current Vite layout:

| Setting | Value |
| --- | --- |
| Framework preset | Vite |
| Root Directory | `frontend` |
| Install Command | `npm ci` |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| Node | 22.x |
| Frontend environment | `VITE_VF_API_BASE_URL=https://verifierforge-production.up.railway.app` (no trailing slash) |

Add an SPA rewrite from all paths to `/index.html` before deploying
`BrowserRouter`; Vite's local fallback does not prove Vercel deep-link behavior.
The reviewer invite code must be typed at runtime and held only in memory or
session storage. Railway must allow the final Vercel origin and preview pattern
through `VF_CORS_ORIGINS` / `VF_CORS_ORIGIN_REGEX`. The current `prebuild`
reads `../data/demo-artifacts`; retain repository-parent access during the
static transition, then remove the generator dependency when the page consumes
the API directly.
