# 设计文档一:Supabase 数据库迁移

> VerifierForge · 系统设计 · v1.0(2026-07-17)
> 关联文档:《Forge Agent 模块与 Evaluation》《Provisioner 抽象与适配器》

## 0. 三系统咬合关系(全局视角)

```
Forge Agent 读【数据库】里的流量画像 → 产出决策 + 训练配置
     ↓(人点 Approve,唯一人工闸)
Provisioner 拿配置 + 用户自带云凭证 → 开 GPU → 训练
     ↓(训练全程状态只写 S3,机器无状态)
产物回 S3,过程与账目全部落【数据库】→ 前端与报告消费
```

三条共享设计原则(三份文档共同遵守):

1. **决策与执行分离** — agent 只产出配置,永远无权直接花钱。
2. **worker 无状态** — 任何被 provision 的机器零本地依赖,状态在 S3、事实在数据库,机器随时可死。
3. **契约先行** — 一切模块间交互先定 pydantic schema,再写实现。

---

## 1. 目标与边界

把系统的一切**关系型事实**迁入 Supabase Postgres,成为唯一真源;S3 继续持有一切**大对象**。

| Postgres(Supabase) | S3(职责不变) |
|---|---|
| traffic_requests 流量账单 | checkpoint(native / HF) |
| clusters 簇快照与统计 | evidence 证据包 |
| routing_state 路由态 | 曲线 PNG / assets |
| live_pass_rate 守护点位 | metrics.jsonl 原始序列 |
| jobs 任务台账 | |
| agent_decisions 决策审计(新) | |
| provider_credentials 云凭证(新,加密) | |
| approvals 批准记录(新) | |

**分工纪律**:S3 不当数据库用(不可查询、不可聚合);数据库不存大二进制(metrics 原始序列留在 S3 经 Storage 抽象读写,库里只存任务摘要与供 UI 使用的末 N 点缓存)。

## 2. 架构决策

1. **不写两套后端。** 用 SQLAlchemy 2.0(async)实现**一层**仓储;后端由 `DATABASE_URL` 决定:`sqlite+aiosqlite://`(回退阀)或 `postgresql+asyncpg://`(默认,指向 Supabase 连接池端口)。现有 SQLite 代码收编进该层后自动成为回退实现;**默认值 = Supabase**。
2. **模式演进只走 Alembic**,禁止手写 DDL 漂移。
3. **仓储接口是上层唯一入口**(API / proxy / agent / provisioner 都只认接口,不认方言):
   `TrafficStore / ClusterStore / RoutingStore / LivePassRateStore / JobStore / AgentDecisionStore / CredentialStore / ApprovalStore`
4. **凭证安全**:应用层加密(Fernet,密钥来自环境变量)后入库;Supabase service key 仅存在于服务端环境;任何日志、异常、trace 中出现明文凭证,视为测试失败项(密钥扫描进 CI)。
5. **连接卫生**:走 Supabase 连接池;仓储层统一超时与重试;断库时显式报错,禁止静默降级写坏数据。

## 3. 核心表(DDL 骨架)

```sql
traffic_requests(id, ts, prompt_hash, model, tokens_in,
  tokens_out, latency_ms, cost_usd, route_taken)

clusters(cluster_id PK, name, status, monthly_calls,
  monthly_cost_usd, trainable, job_id, analyzer_summary,
  updated_at)

routing_state(cluster_id PK, enabled, canary_percent,
  target_model, updated_at)

live_pass_rate(cluster_id, ts, pass_rate)

jobs(job_id PK, template, status, config_json,
  created_at, s3_prefix, summary_json)

agent_decisions(id PK, cluster_id, decision, rationale,
  confidence, config_json, trace_s3_key, model_name,
  created_at)

provider_credentials(id PK, user_id, provider,
  encrypted_key, created_at)

approvals(id PK, decision_id, approved_by, approved_at,
  provision_handle)
```

## 4. 迁移与测试策略

- **一次性割接,不做双写**:Alembic 建表 → 幂等导入脚本(SQLite → Postgres,自然键去重,可反复执行)→ 翻 `DATABASE_URL` → 旧 SQLite 文件归档为只读。
- **同一套仓储测试对两个 URL 参数化各跑一遍**(SQLite 为 CI 快车道,Postgres 为集成车道)。
- **API 层零改动是硬验收**:既有的 mock / 真 API 同形测试一行不改仍全绿 —— 换库对上层不可见,才算迁移干净。

## 5. 开发阶段(粗粒度)

### Stage DB-1 仓储统一
抽仓储接口 + SQLAlchemy 层 + Alembic 初版模式;全部现有功能在 SQLite URL 下回归全绿。
**DoD**:上层代码零直连 SQLite 残留(grep 校验);测试参数化框架就位。

### Stage DB-2 Supabase 落地
项目开通、环境接线、历史数据导入、集成测试对真 Postgres 全绿、默认 URL 切换。
**DoD**:全产品(proxy / discover / 报告 / 路由 / 守护 / agent 决策落库)在 Supabase 上端到端跑通;历史流量完整迁入且行数对账一致。

### Stage DB-3 生产卫生
凭证加密、连接池参数、备份说明、README 运维一节。
**DoD**:密钥扫描零命中;断库演练(拔 URL)得到明确报错而非静默坏数据;回退阀演练(切回 SQLite URL)一次通过。
