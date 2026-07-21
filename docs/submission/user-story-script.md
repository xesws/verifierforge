# VerifierForge user-story opening

**Target:** 25 seconds maximum. **Version:** v0.38.0. **Evidence date:**
2026-07-21.

## What the demo data actually depicts

The frozen dataset does not name an industry. It depicts an internal data and
operations analytics team at a multi-department company, turning natural-language
questions about departments, employees, projects, and assignment hours into one
read-only SQLite query.

Repository evidence:

- The frozen 50-row training pool and 60-row held-out set are identified in
  `data/nl2sql/v0.10.2-u3-freeze-manifest.json`.
- The shared schema defines `departments`, `employees`, `projects`, and
  `employee_projects` in
  `frontend/src/data/generated/nl2sql-review-schema.sql`; the matching fixture
  rows live in `frontend/src/data/generated/nl2sql-review-sandbox.sql`.
- The ten arena prompts in
  `data/demo-artifacts/jobs/d4-m3-1p5b-r1-v0125/job.json` ask about planned or
  active projects, salaries, employee headcount, and department location.
- The six reviewer prompts in `frontend/src/data/presentation.ts` query top
  earners, planned projects, active Engineering employees, salary thresholds,
  aggregate project hours, and top earners by department.
- `app/proxy/clusters.py` fixes Data Pull SQL at 95,000 monthly calls and
  $5,500 monthly model cost. The UI describes it as natural language to
  production-safe analytical SQL.

## 中文旁白

> 企业数据团队每月让大模型把9.5万条员工、部门、项目和工时问题写成SQL，账单达$5,500/月。这些请求高频、简单、可程序化验真，正适合专属小模型。VerifierForge自动发现、训练、验证，再替换昂贵大模型。

三句，共 108 个字符，符合中文不超过 110 字的限制。

## English voiceover

> An enterprise data team asks a large model to turn 95,000 monthly questions about employees, departments, projects, and assignment hours into SQL—costing $5,500 a month. These requests are frequent, simple, and programmatically verifiable, making them ideal for a dedicated small model. VerifierForge automatically discovers, trains, and validates that specialist, then safely replaces the larger model.

Three sentences, 55 whitespace-delimited words, within the 70-word limit.

## Demo consistency check

| Script noun or claim | Demo source of truth | Check |
| --- | --- | --- |
| Industry background | No industry is named; the script says only “enterprise” | ✓ |
| Team | Internal data/operations analytics implied by the Data Pull SQL workflow | ✓ |
| Employees and departments | Frozen `employees` and `departments` tables | ✓ |
| Projects and assignment hours | Frozen `projects` and `employee_projects.hours` | ✓ |
| Natural language to SQL | Discover description, system prompt, and all frozen records | ✓ |
| Read-only, programmatic verification | Single `SELECT`/`WITH` constraint and frozen SQLite execution | ✓ |
| 95,000 monthly questions | Data Pull SQL `monthly_calls=95_000` | ✓ |
| $5,500 monthly model cost | Data Pull SQL `monthly_cost_usd=5_500.0` | ✓ |
| Arena compatibility | All ten samples concern the same employee/department/project schema | ✓ |
| Legacy persona exclusion | No fund, trading, portfolio, invoice, or customer-order claim appears | ✓ |

## Teammate handoff — copy for WeChat

> 队友你好，这是开场 avatar 的最终旁白：成片不超过 25 秒，16:9、1080p，中英文任选一版，不要改业务对象和数字。请在明早 9:00 前回传；若迟到，主片会直接用同文案标题卡兜底，不阻塞剪辑。
