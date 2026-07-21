# VerifierForge user-story opening

**Target:** 25 seconds maximum. **Version:** v0.38.1. **Evidence date:**
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
- The real proxy path in `app/proxy/upstream.py` forwards an OpenAI-compatible
  request to an operator-configured external API. `app/proxy/traffic.py`
  records token counts and estimates cost from an editable price table.

The narrative therefore describes the legacy path as a token-priced external
large-model API. OpenRouter is one possible configured upstream, but the
$5,500 Discover baseline is not derived from the repository's current proxy
price table and must not be presented as an OpenRouter invoice.

## 中文旁白

> 员工用数据助手查询部门、人员、项目和工时。后台每月把9.5万次请求发给按Token收费的外部大模型API，账单达$5,500。问题重复、简单且SQL可验真；VerifierForge训练专属小模型，验证后替换昂贵调用链。

三句，共 110 个字符，符合中文不超过 110 字的限制。

## English voiceover

> Employees across the company use an internal data assistant to ask routine questions about departments, people, projects, and work hours. Behind the scenes, all 95,000 monthly requests go to a token-priced third-party large-model API, creating a $5,500 bill. Because these SQL tasks repeat and can be checked automatically, VerifierForge trains and validates a specialist small model to replace that expensive path.

Three sentences, 61 whitespace-delimited words, within the 70-word limit.

## Demo consistency check

| Script noun or claim | Demo source of truth | Check |
| --- | --- | --- |
| Industry background | No industry is named; the script says only “company” | ✓ |
| Internal data assistant | Concrete framing for the demonstrated Data Pull SQL workflow | ✓ |
| Employees and departments | Frozen `employees` and `departments` tables | ✓ |
| Projects and assignment hours | Frozen `projects` and `employee_projects.hours` | ✓ |
| Natural language to SQL | Discover description, system prompt, and all frozen records | ✓ |
| Read-only, programmatic verification | Single `SELECT`/`WITH` constraint and frozen SQLite execution | ✓ |
| External paid model API | Proxy real mode forwards to a configurable OpenAI-compatible upstream | ✓ |
| Token-priced usage | Proxy records input/output tokens and applies a configured price table | ✓ |
| 95,000 monthly questions | Data Pull SQL `monthly_calls=95_000` | ✓ |
| $5,500 monthly bill | Discover baseline `monthly_cost_usd=5_500.0`; not a provider invoice | ✓ |
| Arena compatibility | All ten samples concern the same employee/department/project schema | ✓ |
| Legacy persona exclusion | No fund, trading, portfolio, invoice, or customer-order claim appears | ✓ |

## Teammate handoff — copy for WeChat

> 队友你好，这是开场 avatar 的最终旁白：成片不超过 25 秒，16:9、1080p，中英文任选一版，不要改业务对象和数字。请在明早 9:00 前回传；若迟到，主片会直接用同文案标题卡兜底，不阻塞剪辑。
