# docs/design — 系统设计文档索引

> VerifierForge 生产化(v1 阶段)三大系统的设计母本。
> 一切相关工单引用本目录的 Stage 编号(DB-x / A-x / P-x)。

## 三系统咬合关系

```
Forge Agent 读【数据库】里的流量画像 → 产出决策 + 训练配置
     ↓(人点 Approve,唯一人工闸)
Provisioner 拿配置 + 用户自带云凭证 → 开 GPU → 训练
     ↓(训练全程状态只写 S3,机器无状态)
产物回 S3,过程与账目全部落【数据库】→ 前端与报告消费
```

## 三条共享设计原则

1. **决策与执行分离** — agent 只产出配置,永远无权直接花钱。
2. **worker 无状态** — 被 provision 的机器零本地依赖:状态在 S3、事实在数据库,机器随时可死。
3. **契约先行** — 一切模块间交互先定 pydantic schema,再写实现。

## 文档清单

| 文档 | 内容 | 开发阶段 |
|---|---|---|
| [design-01-supabase-migration.md](./design-01-supabase-migration.md) | 关系型事实迁入 Supabase;仓储抽象、表结构、割接与测试策略 | DB-1 ~ DB-3 |
| [design-02-forge-agent-and-eval.md](./design-02-forge-agent-and-eval.md) | GPT-5.6 决策代理:契约、工具集、Runner 守卫、Evaluator(gate C) | A-1 ~ A-4 |
| [design-03-provisioner-and-adapters.md](./design-03-provisioner-and-adapters.md) | 平台无关的 GPU 供给层:接口、生命周期编排、保险丝、RunPod / Nebius 适配器 | P-1 ~ P-4 |

## 版本纪律(摘要,全文见 AGENTS.md)

- 流水版本号机械递增,不承载语义。
- 语义由 tag 承载:提交物冻结 `v1.0-buildweek`;系统里程碑 tag 与本目录 Stage 同名(`db-2-complete` / `agent-gate-c-pass` / `provisioner-p2-live`)。
- 新系统一律 feature flag 默认关(`VF_DB_BACKEND` / `VF_AGENT_ENABLED` / `VF_AUTOPROVISION`),过对应 gate 并经 owner 确认后翻开;评审路径禁止依赖 flag 关闭状态下的组件。
- 长寿分支禁令有效:生产化在主干进行。