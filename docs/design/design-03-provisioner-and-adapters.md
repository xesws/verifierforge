# 设计文档三:Provisioner 抽象 + RunPod / Nebius 适配器

> VerifierForge · 系统设计 · v1.0(2026-07-17)
> 关联文档:《Supabase 数据库迁移》《Forge Agent 模块与 Evaluation》

## 1. 目标

把"为一个已批准的训练任务弄到一台 GPU、跑完、收货、还钱"做成**平台无关的自动化层**:用户自带云厂商凭证(BYO),系统在**用户自己的账户**上开机、训练、销毁。首发支持 RunPod 与 Nebius 两个适配器,新增厂商 = 新增一个适配器文件 + 过一套一致性测试。

> 事实澄清:RunPod 具备完整的编程接口 —— REST API(POST /v1/pods 等)、官方 Python SDK(create_pod / stop_pod / terminate_pod)与 CLI,官方文档明确支持"任务排队时自动拉起指定 GPU、训练完自动释放"的 MLOps 场景。"RunPod 无 API"不成立;选择双适配器是产品的平台无关战略,不是被迫换平台。

## 2. 接口契约

```python
class Provisioner(Protocol):
    def provision(spec: ProvisionSpec) -> ProvisionHandle
    def status(handle) -> ProvisionStatus
    def terminate(handle) -> None
    def list_active() -> list[ProvisionHandle]

ProvisionSpec:
  provider: runpod | nebius
  gpu_class: 抽象枚举(如 small_ada / mid_ampere / h100)
             # 适配器负责映射到厂商具体型号;
             # 默认映射表内 Blackwell 全系拉黑(sm_120 教训固化)
  image: str
  container_disk_gb: int
  region_pref: list[str] | None
  env: dict            # 注入的环境变量(不含明文凭证)
  ports: list[int]
  ssh_pubkey: str
  budget_usd_cap: float      # 硬保险丝之一
  max_runtime_min: int       # 硬保险丝之二

ProvisionStatus:
  state: REQUESTED|PROVISIONING|BOOTSTRAPPING|RUNNING|
         COLLECTING|TERMINATED|FAILED
  ssh: str | None
  cost_accrued_usd: float
  uptime_min: int
  detail: str
```

## 3. 生命周期编排器(适配器之上,平台无关)

状态机:`REQUESTED → PROVISIONING → BOOTSTRAPPING → RUNNING → COLLECTING → TERMINATED / FAILED`

各阶段职责:

1. **PROVISIONING**:调适配器创建实例;拿到 SSH 端点;超时未就绪 → 终止并 FAILED。
2. **BOOTSTRAPPING**:注入引导脚本 —— clone 仓库(只读 deploy key)、装训练依赖(锁文件)、写入训练配置。**关键设计:被 provision 的机器一律 `VF_STORAGE_BACKEND=s3`、不挂任何网络卷** —— 状态零本地化,任何厂商、任何区域、任何机器都同构。
3. **RUNNING**:训练执行;**进度监控不依赖 SSH** —— 因为 checkpoint/metrics 全在 S3,编排器轮询 S3 即可获得步数与曲线;SSH 仅作诊断通道。
4. **COLLECTING**:确认最终产物已在 S3(SHA 对账),更新 jobs 表。
5. **TERMINATED**:调适配器销毁实例;**回读厂商侧状态确认计费已停**,写入审计。

## 4. 安全保险丝(不可谈判项)

| 保险丝 | 行为 |
|---|---|
| 单 job 预算上限 | cost_accrued ≥ cap → 立即终止 + 告警 |
| 最大并发实例数 | 超限的 provision 请求直接拒绝 |
| 最大运行时长 | 超时 → 终止(checkpoint 在 S3,损失有限) |
| 全局 kill-switch | 一条命令终止全部活跃实例 |
| 孤儿收割器 | 周期对账:厂商侧存在、但 DB 无对应活跃 job 的实例 → 告警并终止 |
| 审计日志 | 一切对厂商的变更操作(创建/终止)落 DB,含发起者与关联 approval |

## 5. 凭证(BYO)

- 用户在 settings 提交 provider + API key → 应用层加密后入 `provider_credentials`(见文档一)。
- 适配器仅在调用瞬间获得解密后的 key,**不缓存、不落日志、不入异常文本**;密钥扫描进 CI。
- 系统自身不垫付任何算力费用 —— 花的每一分钱都发生在用户自己的云账户里,批准记录与厂商审计一一对应。

## 6. 适配器

### 6.1 RunPodAdapter(首发)
- 通道:REST API(pods 资源)或官方 Python SDK,二选一实现,另一个留作诊断。
- 职责映射:gpu_class → RunPod gpuTypeIds(默认表拉黑 Blackwell 全系);ports/env/container_disk 直传;创建时启用 SSH。
- 已知语义:直连 SSH 端口在实例迁移/重启后会变 —— 编排器在每次状态轮询时刷新连接信息,不缓存旧端点(本周实战教训固化)。

### 6.2 NebiusAdapter(第二适配器)
- 按 Nebius 官方 compute API 实现同一契约;具体端点与鉴权以其当前文档为准,实现期核对,不在本设计文档预写细节。
- 与 RunPodAdapter 的唯一差异应当只存在于适配器文件内部;若发现需要改编排器才能接入,视为抽象泄漏,先修抽象再接。

### 6.3 一致性测试套件(每个适配器必须通过同一套)
1. **dry-run 模式**:适配器对厂商 HTTP 全 mock,状态机全路径可测,CI 零成本。
2. **金路径集成**:真实开"最小可计费实例" → status → terminate → 回读计费已停。
3. **故障注入**:创建超时 / SSH 不可达 / 中途终止 —— 状态机必须收敛到 FAILED/TERMINATED,不允许悬挂计费。

## 7. 与 Agent / 批准流的接线

```
AgentDecision(forge, config) → 用户点 Approve
  → approvals 落库
  → 编排器将 TrainingConfig 翻译为 ProvisionSpec
    (预算上限取 config.budget_usd_cap 与系统上限的较小值)
  → provision → … → COLLECTING → jobs 表更新
  → 报告管线照常从 S3 装配
```

批准是唯一的人工动作;批准之后到销毁实例,全程无人。

## 8. 开发阶段(粗粒度)

### Stage P-1 抽象与编排器
接口契约 + 状态机 + 全部保险丝 + dry-run 测试全绿。
**DoD**:不接任何真实厂商即可演示完整生命周期(mock 适配器);保险丝逐条有测试。

### Stage P-2 RunPodAdapter
真实金路径:最小实例 provision → terminate + 计费停确认;随后一次完整实战 —— 由一份真实 TrainingConfig 驱动,0.5B 短训全自动:开机 → 训练(S3 后端)→ 收货 → 销毁。
**DoD**:全程零人工介入(批准点击之后);审计与成本记录完整;孤儿收割器演练通过。

### Stage P-3 NebiusAdapter
同一契约、同一一致性套件过关(dry-run + 金路径)。
**DoD**:切换 provider 仅改 ProvisionSpec.provider 一个字段,其余体验同构。

### Stage P-4 BYO 凭证 + 批准流接线
settings 凭证入库(加密)、Approve 页 / 弹窗、决策 → 批准 → 执行全链。
**DoD**:一个从未接触过系统内部的用户,只凭"填自己的 key + 点批准"即可让系统在其账户上完成一次全自动锻造。
