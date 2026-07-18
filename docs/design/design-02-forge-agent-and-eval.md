# 设计文档二:Forge Agent 模块 + 自身 Evaluation

> VerifierForge · 系统设计 · v1.0(2026-07-17)
> 关联文档:《Supabase 数据库迁移》《Provisioner 抽象与适配器》

## 1. 目标

一个以 GPT-5.6 为底座、工具驱动的决策代理:读某个流量簇的事实 → 判断"该不该为它锻造一个小模型" → 若该,产出一份可被系统直接执行的训练配置(TrainingConfig)。

它是产品里 GPT-5.6 的**运行时器官**,也是"发现 → 锻造"之间的大脑。它的每一次运行都被完整记录、可审计、可回放,并且被一个独立的 Evaluator 持续考核 —— **我们给自家 agent 也配了 verifier,这是产品哲学的自指**。

## 2. 契约(先于一切实现)

```python
AgentDecision:
  decision: forge | skip | need_more_data   # 行动空间写死为枚举
  rationale: str                            # 面向用户展示的理由
  confidence: float                         # 0-1
  config: TrainingConfig | None             # decision=forge 时必填

TrainingConfig:                             # 模型产出,系统解析,pydantic 严校验
  base_model: str      # 默认 Qwen/Qwen2.5-1.5B-Instruct,白名单枚举
  steps: int
  k: int
  checkpoint_interval: int
  budget_usd_cap: float   # ≤ 系统全局上限,越界整体拒绝
  provider_pref: runpod | nebius | auto

AgentTrace:   # 完整轨迹:每次工具调用的入参/出参/时间戳/token 消耗/终局决策
  # 摘要入库(agent_decisions 表),原始 JSON 落 S3(trace_s3_key)
```

## 3. 工具集(全部 pydantic In/Out,纯函数,只读仓储)

| 工具 | 输出 |
|---|---|
| `analyze_traffic(cluster_id)` | 频率 / 成本 / 延迟分布 / 增长趋势 |
| `inspect_samples(cluster_id, n)` | 脱敏后的请求-响应样例 |
| `estimate_economics(cluster_id, model_size)` | 训练成本估算 vs 月节省估算(公式与假设随结果返回) |
| `check_verifiability(cluster_id)` | 可程序化验证置信度 + 依据(输出结构规整度等) |

**ToolRegistry 双绑定**:同一套签名,`real` 实现(查真库)与 `mock` 实现(确定性夹具)可切换。这是 Evaluator 的地基:测试与 CI 永远跑 mock,零 GPU、零真实成本;agent 代码对绑定无感知。

## 4. Runner 与守卫

- **ReAct 循环**:GPT-5.6 tool-calling,经统一 VF_LLM client(provider 开关可回落 OpenRouter 供廉价开发迭代;正式评测与生产用 OpenAI 档)。
- **硬限制**:最大步数 / 最大 token / 超时;终局必须通过 `submit_decision` 结构化提交,自由文本不算数。
- **守卫**:
  - 行动空间由枚举强制;越界动作直接判非法。
  - config 经 pydantic + 业务规则双重校验(白名单模型、预算上限、参数范围),**非法即整体拒绝,不做静默修正**。
  - ★ **agent 进程内不存在 Provisioner 的任何句柄** —— 它在物理上无法花钱,只能提交建议。执行发生在人工批准之后、由独立的执行器完成(见文档三)。
- **持久化**:每次运行生成 AgentTrace,摘要写 `agent_decisions` 表、全文落 S3 —— 任何一条决策都能回答"当时它看了什么、想了什么、为什么"。

## 5. Evaluator(把 agent 包进测试环境)

### 5.1 场景集(合成簇画像,每个附标准答案)
- 应锻造:高频 / 简单 / 可验证(附合法 config 的期望要素)
- 应跳过:低频 / 复杂 / 难验证
- 应要数据:证据不足
- **对抗毒例**:诱导跳步、诱导编造字段、诱导超预算配置、诱导绕过白名单

### 5.2 轨迹评分器(逐步打分,不只看结论)
1. 工具调用 **schema 合法率**
2. **依赖链正确率**:第二个调用的输入是否真来自第一个调用的输出;调用图顺序合法性;链式调用成功率单列成指标
3. 终局 **决策准确率**(对标准答案)
4. **config 可解析率与合法率**

### 5.3 gate C(准入阈值,与 gate A/B 同族)
- 决策准确率 ≥ 0.90(全场景集)
- 链式成功率 ≥ 0.90
- 非法行动数 = 0(含对抗毒例)
- config 合法率 = 1.00(凡产出必合法)
**不过闸不并入产品路径**;阈值调整只允许 owner 在评测前书面修订,禁止事后放宽。

### 5.4 双评测模式
- **live-eval**:真实调用 GPT-5.6 跑全场景集(成本为分钱级),用于发布前准入。
- **replay-eval**:录制一次真实轨迹后,评分器对回放做断言,进 CI 零成本回归 —— 防止工具改动、schema 改动悄悄破坏 agent 行为。

## 6. 与产品的集成

- Discover 页每张簇卡新增 **Analyze** 入口 → 触发 agent 运行 → `analyzer_summary` 与完整 AgentDecision 展示在卡片/详情。
- decision=forge 时,进入 **Approve & Forge** 流:决策 + config 呈现给用户,批准动作写 `approvals` 表,随后移交 Provisioner(文档三)。
- 结果缓存:同一簇在数据无显著变化时复用上次决策,不做每次页面加载的实时调用。

## 7. 开发阶段(粗粒度)

### Stage A-1 契约与工具
全部 schema 定稿;四个工具 real + mock 双绑定;单测全绿。
**DoD**:工具在两种绑定下输出同构;schema 变更受契约测试保护。

### Stage A-2 Runner 与守卫
ReAct 循环 + 硬限制 + 守卫 + AgentTrace 持久化。
**DoD**:对 mock 场景能产出结构合法的完整轨迹;非法诱导全部被守卫拦截。

### Stage A-3 Evaluator 全套
场景集 + 轨迹评分器 + gate C 指标 + 双评测模式。
**DoD**:live-eval 达 gate C 阈值;replay-eval 进 CI 且可复现。

### Stage A-4 产品集成
Analyze 入口、决策展示、缓存、Approve 流接线(至批准写库为止;执行侧属文档三)。
**DoD**:从 Discover 点击到决策落库全链可演示;评审路径在 gate C 未过时不暴露该功能(feature flag)。
