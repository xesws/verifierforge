# VerifierForge 开发文档 v0

> OpenAI Build Week 2026 · Developer Tools 赛道 文档基准:2026-07-13(周一)23:58 PT 提交截止:**2026-07-21 17:00 PT**(提交窗口已于 7/13 09:00 PT 开启) 状态:定稿,执行中。任何 scope 变更必须先改本文档第 12 节。

**v2.0 → v2.1 变更摘要**

- 新增 **§6.5 Storage 抽象层**:训练层按「无状态计算节点 + 可插拔持久层」设计;本周实现 LocalStorage,S3Storage 只在 V1 主线上验证一次,完整抢占恢复明确 out of scope
- 新增 **§6.6 远程控制面**:`vf` 脚本(SSH + tmux + rsync),笔记本单向指挥 RunPod,全程不开 RunPod 网页
- 新增 **§6.7 Checkpoint 纪律** 与数据分流规则(git / rsync / S3 各管什么;checkpoint 永不进 git)
- §5 架构图与组件表更新(Storage 层入图);§7 目录结构加 `core/storage/` 与 `scripts/vf`
- §13 计划更新(D1 加 Storage 接口定稿;D4 加 S3 验证);§12 / §15 / 附 B 相应更新

---

## 1. 产品定义

**对外一句话** Describe your task in plain English — GPT-5.6 writes the grader, we RL-train a small model you own, and prove the gains are real.

**对内一句话** 把 Reinforcement Fine-Tuning 做成一个 10 分钟即插即用的 dev tool:用户带着「任务描述 + 少量样例」进来,拿着「微调后的专属小模型 + 可信度报告 + OpenAI-compatible 端点」离开。

**定位话术(统一口径,任何场合不得偏离)**

- ✅ "GPT-5.6 designs and supervises the training of a small model you own."(GPT-5.6 是大脑,小模型是产出物)
- ✅ 成本表述:"for high-volume narrow tasks, serve at a fraction of the cost."
- ✅ 架构表述:"training runs on stateless, disposable GPU workers — any node can die and the job survives."(§6.5 给了这句话底气)
- ❌ 不说「OpenAI RFT 的开源平替」——在人家的场子里不打人家的脸
- ❌ 不说「beat GPT-5.6」——产品叙事是"蒸馏 + 强化",不是"取代"

**赛道:** Developer Tools(规则原文覆盖 testing / DevOps / agentic workflows)

---

## 2. 竞品地图与差异化(评委必问)

### 2.1 竞品地图

|类别|代表|它们的隐含前提|
|---|---|---|
|开源 RL 框架|verl / TRL GRPOTrainer / OpenRLHF / Oat|**你已经会写 reward 函数、会调 YAML、会诊断不收敛**|
|官方 RFT|OpenAI Reinforcement Fine-Tuning|只服务闭源模型;grader 你自己写;产出模型你不拥有|
|商业 RFT 服务|Predibase / Fireworks 等|**你已经会写 grader**,带过来我帮你跑|
|Agent RL|OpenPipe ART(RULER)|reward 走 LLM-as-judge,不是程序化 verifier 路线|

**诚实的自我认知(对内,不进 pitch):** 训练循环本身零算法创新。如果产品停在"给 verifier 就帮你跑 RL",那就是 verl 的一层壳,壁垒极薄。

### 2.2 三个真正的差异化(全部不在训练循环里)

**D1 — verifier 的「生成」,而不是「执行」** 所有竞品的隐含前提都是"你会写一个好的 reward 函数"。现实是:大多数后端工程师写出来的 verifier 要么全 0/1(太稀疏训不动),要么有漏洞(被模型 reward hack)。 **Verifier Copilot 把「分层给分怎么设计、边界怎么堵、要不要加长度惩罚」这些 RL 工程师的隐性知识,变成 GPT-5.6 自动生成 + 沙箱试跑的产品功能。**

> 别人假设你会写 reward;我们假设你不会。产品的瓶颈从来不是 RL 循环,是 reward 工程。

**D2 — 可信度层(无竞品产品化)** Spurious 对照组 + 熵坍塌监控 + 自动早停,合起来回答用户最怕的问题:"我烧的钱换来的涨点是真的吗?" 框架给你曲线,商业服务给你 checkpoint,**没人给你打假报告**。

**D3 — 闭环终点不同** 框架的终点是 checkpoint;我们的终点是「生产代码改一行 `base_url`」。从自然语言 → verifier → 数据扩增 → 训练 → 评测 → 端点,全线不需要用户懂 RL。单看每一步都不新,但没人把这条线焊死过。

### 2.3 给评委的标准答案(背下来)

> "Frameworks like verl assume you can write a reward function. Commercial RFT assumes you can write a grader. We assume you can't — you describe the task, GPT-5.6 engineers the verifier, and we prove the gains are real before you ship. The unit of work changes from 'an ML project' to 'a 10-minute setup'."

---

## 3. 目标用户与三个垂直模板

**目标用户:** 有高频、窄域、结果可程序化验证任务的工程团队——每天大量调用大模型 API 做同一件小事,烧钱且延迟高的那批人。

|#|模板|输入|输出|Verifier(程序化)|
|---|---|---|---|---|
|V1|NL → SQL|自然语言查询 + schema|SQL|SQLite 实际执行,结果集比对|
|V2|日志 → 结构化 JSON|原始日志行|JSON 对象|JSON Schema 校验 + 关键字段比对|
|V3|正则生成|需求描述 + 正反例|正则表达式|正反例测试用例通过率|

共性:reward 完全客观、执行毫秒级、错误可分层给分。**这是 demo 不翻车的根基。** 优先级:V1 是主案例(必须成功);V2/V3 是加分项。

---

## 4. 用户旅程(5 步)

1. **描述任务** — 自然语言 + 粘贴 5–100 条样例
2. **GPT-5.6 生成 verifier** — 产出 verifier 代码 + 分层给分模板 + 单元测试;用户可改,点「验证」在沙箱里对样例试跑
3. **任务扩增** — GPT-5.6 把样例扩增成数百条训练 prompt;扩增结果先过用户自己的 verifier 预筛
4. **发起训练** — 选模型(0.5B / 1.5B),后台跑 GRPO;**同时自动跑随机 reward 对照 job**;页面实时显示 reward / 熵 / pass rate 曲线
5. **交付** — before/after 报告(GPT-5.6 生成自然语言诊断)+ 一键部署 OpenAI-compatible 端点(改 `base_url` 即可接入现有代码)

---

## 5. 系统架构

```
┌────────────┐   ┌──────────────┐   ┌────────────────────┐
│  Web UI    │──▶│  API Server  │──▶│ Job Queue(单队列)  │
│  Next.js   │   │  FastAPI     │   │ SQLite + 单 worker  │
└────────────┘   └──────┬───────┘   └─────────┬──────────┘
                        │                     │ 派发 job spec
              ┌─────────▼──────────┐  ┌───────▼──────────┐
              │  GPT-5.6 服务层     │  │ Training Worker  │
              │  · Verifier Copilot│  │ verl(GRPO/FSDP)  │
              │  · 任务扩增         │  │ + vLLM rollout   │
              │  · 报告叙事         │  │ 【无状态·可销毁】 │
              └────────────────────┘  │ 【RunPod 上执行】 │
                        ▲             └───────┬──────────┘
              ┌─────────┴──────────┐          │ 只经 Storage 读写
              │  Eval Runner       │  ┌───────▼──────────┐
              │  before/after      │◀─│  Storage 抽象层★  │
              └─────────┬──────────┘  │ ckpt/metrics/final│
                        │             │ Local ⇄ S3 可插拔 │
              ┌─────────▼──────────┐  └───────┬──────────┘
              │ Model Registry +   │          │
              │ Serving(vLLM,      │  ┌───────▼──────────┐
              │ OpenAI-compatible) │  │ Verifier Sandbox │
              └────────────────────┘  │ Docker 无网络     │
                                      │ CPU/内存/5s 超时  │
                                      └──────────────────┘
```

|组件|选型|备注|
|---|---|---|
|前端|Next.js + Tailwind|4 个页面:新建任务 / job 列表 / job 详情(曲线)/ 报告页|
|后端|FastAPI|单体,不拆微服务|
|队列|SQLite 表 + 单 worker|**明确不用** Celery/Redis。单队列是纪律|
|训练|verl(GRPO)+ FSDP + vLLM rollout|权重同步走 verl 内置机制;**worker 无状态,状态全在 Storage**|
|**Storage**|抽象接口 + 双实现:LocalStorage(本周默认)/ S3Storage(V1 主线验证)|见 §6.5,**worker 与持久层之间唯一的门**|
|沙箱|Docker `--network=none` + CPU/内存限额 + 5s 超时 + 只读挂载|用户 verifier 一律视为不可信代码|
|推理服务|vLLM OpenAI-compatible server|评委 demo 端点跑量化小模型;托管方案 D5 前定(§15)|
|GPT-5.6|OpenAI API|三个集成点见 §10|

---

## 6. 工程拓扑(决定整周节奏)

### 6.1 两台机器的角色

```
┌────────────────────────────┐        ┌──────────────────────────┐
│  笔记本(开发主机)          │        │  RunPod(纯算力执行器)    │
│  · Codex 主 session ★      │        │  · git pull(只跑 trainer)│
│  · 写全部产品代码           │  git   │  · GRPO 训练              │
│  · 写训练脚本(不运行)      │ ─────▶ │  · 产出 ckpt/指标 → Storage│
│  · 前端 / 后端 / 沙箱       │  SSH   │  · 【无状态,可随时销毁】  │
│  · 会话历史永久保存 ★       │ ─────▶ │                          │
└────────────────────────────┘        └──────────────────────────┘
```

**核心认知:写代码 ≠ 运行代码。** 真正需要摸到 GPU 的只有两项:

|组件|需要 GPU?|
|---|---|
|Verifier Copilot / 任务扩增 / 报告叙事(GPT-5.6)|❌|
|沙箱 / Job 队列 / API server / 前端 / 报告逻辑|❌|
|GRPO 训练脚本(verl config + reward 适配层)|✅ **仅运行时**|
|端点部署(vLLM serve)|✅ 仅部署时|

### 6.2 为什么主 session 必须在笔记本上

Codex 的会话历史存在**本地磁盘**(`~/.codex/sessions/`),不在云端。RunPod 实例会被销毁——把主 session 放上面,实例一没,那条 thread 永远 `resume` 不了。笔记本不会被销毁。

> Session ID 只是一串字符,记下来永久有效(评委查 OpenAI 后台)。但**能继续推进的那条 thread** 必须活在不会消失的机器上。

### 6.3 与 RunPod 的交互:统一走 `vf` 控制面(§6.6)

日常操作不打开 RunPod 网页、不手敲裸 ssh。想在 RunPod 上装 Codex 调训练 bug?可以——但那是**辅助 session,一次性,断了不心疼**。

### 6.4 D1 硬性任务:打通链路

**D1 必须用一个 5 分钟的假 job 把 `vf train → tmux 训练 → 指标经 Storage 回到本地` 全链路跑通一次。** SSH key、CUDA 版本、依赖冲突、权重下载——这些坑必须在 D1 吃掉,不能留到 D4 主训练日。

### 6.5 Storage 抽象层(v2.1 核心新增)

**设计原则:计算节点无状态,持久层可插拔。** 训练 worker 不假设"自己这台机器明天还在"——所有跨越 worker 生命周期的东西(checkpoint、指标、最终权重)只经 Storage 接口读写。这既是产品级架构(开源版靠它做可抢占训练),也是本周叙事的一部分(§1 第三句话术)。

**接口(D1 定稿,进 `core/storage/base.py`):**

```python
class Storage(ABC):
    def save_checkpoint(self, job_id: str, step: int, path: Path) -> None: ...
    def load_latest_checkpoint(self, job_id: str) -> Path | None: ...
    def append_metrics(self, job_id: str, record: dict) -> None: ...   # append-only JSONL
    def put_artifact(self, job_id: str, name: str, path: Path) -> None: ...   # final 权重/报告
    def get_artifact(self, job_id: str, name: str, dest: Path) -> Path: ...
```

**两个实现:**

|实现|本周角色|说明|
|---|---|---|
|`LocalStorage`|**默认,所有 job 用它**|pod 的 `/workspace` 持久卷 + `vf watch`(rsync)回传本地;零新依赖|
|`S3Storage`|**只在 V1 主线验证一次**|boto3 ~80 行;证明"worker 无状态"不是吹的,README/demo 可以理直气壮地讲|

**实现纪律(不管哪个后端都必须遵守):**

- checkpoint 必须含 **model + optimizer state + RNG state + step 计数**——只存权重是假恢复
- 上传原子化:先写 `.tmp`,完成后 rename——绝不在截断的 checkpoint 上恢复
- metrics 一律 **append-only JSONL**(每行一个 JSON),禁止 read-modify-write
- S3 布局:`s3://vf/jobs/{job_id}/{ckpt/,metrics.jsonl,final/}`;同 step 重传 = 幂等覆盖
- checkpoint 上传异步化(后台线程),不阻塞训练步

**明确 out of scope(进 `ideas-post-hackathon.md`,本周一行不写):** 自动重调度、抢占检测与自动恢复、FSDP sharded checkpoint 的跨卡数恢复、spot 实例编排。

> 理由:这一整块对评委可见度为零(评分是 Idea/Design/Impact/Codex 实现),而 FSDP sharded ckpt 恢复是能单独吃掉两天的深坑。**本周做「接口层面的正确」,不做「实现层面的完整」。**

### 6.6 远程控制面:`vf` 脚本(v2.1 新增)

一个 ~40 行 bash 脚本包住 SSH,笔记本单向指挥 RunPod。**不引入 Ray / Slurm / 任何 orchestration。**

**`~/.ssh/config`(配置一次;pod 重建后只改前两行,10 秒):**

```
Host runpod
    HostName <pod-ip>
    Port <pod-port>
    User root
    IdentityFile ~/.ssh/id_ed25519
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m
    ServerAliveInterval 30
```

**`scripts/vf` 子命令一览:**

|命令|作用|
|---|---|
|`vf bootstrap`|新 pod 一次性装环境(幂等):clone 仓库、`pip install -r requirements-trainer.txt`、装 tmux/rsync、`HF_HOME=/workspace/hf-cache`|
|`vf train <job> <cfg>`|`git pull --ff-only` → **tmux detach** 起训练 → 秒返回(笔记本可合盖)|
|`vf watch <job>`|循环 rsync 指标/日志回本地 `runs/<job>/`(`--exclude='*.safetensors' --exclude='ckpt/'`),本地 API server 直接读文件 → 前端曲线动起来|
|`vf logs <job>`|`tail -f` 远端训练日志|
|`vf status`|`tmux ls` + `nvidia-smi` 一屏|
|`vf kill <job>`|杀 tmux session(曲线不动当场杀,§9 纪律)|
|`vf model <job>`|仅在部署端点时单独拉 final 权重|

**三条铁律:**

1. **训练必须 detach(tmux)**——裸 `ssh "python train.py"` 会在笔记本合盖/断网时 SIGHUP 杀掉训练,这是远程训练第一大杀手
2. **pod 是 git 的只读消费者**:笔记本 push,pod 只 pull(`--ff-only`),pod 永不 push;私有仓库用**只读 Deploy Key**(可单独吊销,不沾账号权限)
3. **一切可缓存的东西放 `/workspace`**(RunPod 持久卷):仓库、HF 权重缓存、runs——卷外的东西 pod 重启即蒸发

### 6.7 数据分流规则(v2.1 新增,一张表定死)

|数据|体量|通道|终点|
|---|---|---|---|
|代码|KB–MB|git(笔记本 → pod 单向)|GitHub 私有仓库|
|训练指标 / 日志 / 报告 JSON|KB|Storage.append_metrics + `vf watch`(rsync)|本地 `runs/`,API server 直读|
|checkpoint(训练中)|GB|Storage(Local:/workspace;S3:异步上传)|**永不进 git,永不 rsync 回本地**|
|final 权重|GB|Storage.put_artifact;部署时 `vf model` 单独拉|端点机 / S3|

> checkpoint 进 GitHub 是硬性禁止:单文件 100MB 限制,LFS 在多 GB 权重上是配额和 clone 灾难。**rsync 传消息,Storage 管状态,git 只管代码。**

---

## 7. 仓库结构与依赖隔离

### 7.1 决策:单仓库 + 目录隔离(不拆仓、不用 worktree)

**为什么不拆两个仓库:** reward 定义 / verifier 接口 / 数据契约 / Storage 接口同时被四处使用——Verifier Copilot(生成)、沙箱(产品侧执行)、Training Worker(GPU 侧执行)、Eval Runner(算 pass rate)。拆仓 = 手工保持四方同步 = 一周内必然出现「产品侧沙箱判通过、训练侧判失败」这类地狱级 bug。

**worktree 不适用:** 那是同一仓库切多分支到多目录的工具,跟"本地/远程分离"无关。

### 7.2 目录结构

```
verifierforge/
├── core/                      # 共享血管:两侧都 import
│   ├── contracts.py           # 数据契约(§11.2)——最先写,最不能变
│   ├── verifier_base.py       # verifier 接口 + 分层给分基类
│   ├── rewards/               # 三个垂直的 reward 实现
│   └── storage/               # ★ v2.1:Storage 抽象
│       ├── base.py            #   接口(§6.5)
│       ├── local.py           #   LocalStorage(本周默认)
│       └── s3.py              #   S3Storage(~80 行,V1 验证)
├── app/                       # 产品侧(只在笔记本 / 部署机运行)
│   ├── api/                   # FastAPI
│   ├── web/                   # Next.js
│   ├── sandbox/               # Docker 沙箱
│   └── gpt/                   # GPT-5.6 三个集成点
├── trainer/                   # GPU 侧(只在 RunPod 运行)
│   ├── verl_configs/
│   ├── reward_adapter.py      # 把 core/rewards 接进 verl
│   ├── bootstrap.sh           # 新 pod 幂等初始化
│   └── launch.sh
├── scripts/
│   └── vf                     # ★ v2.1:远程控制面(§6.6)
├── mock/                      # 队友并行用:假数据生成器 + mock server
├── requirements-app.txt
├── requirements-trainer.txt   # ← 隔离的是【依赖】,不是【仓库】
├── ideas-post-hackathon.md    # scope 泄洪区
├── NOTES.md                   # Codex Session ID 记在这里
└── README.md                  # 含 "How we worked with Codex" 专节
```

RunPod 上:`git clone` 整个仓库,只 `pip install -r requirements-trainer.txt`,只跑 `trainer/`。**"代码在机器上" ≠ "代码被执行"。**

---

## 8. Codex 使用纪律与证据链(合规生死项)

### 8.1 规则原文要什么

> Provide **/feedback Codex Session ID** for your Project thread **where the majority of core functionality was built**

单数 ID、"大部分核心功能所在的那条线"。**规则没有禁止多条 session。**

### 8.2 纪律

|项|规则|
|---|---|
|**主干线 session**|笔记本上一条,命名 `VerifierForge — core`。核心功能(训练管线 / Storage / 沙箱 / GPT-5.6 集成 / 报告 / 后端)有意识地集中在这里推进|
|**辅助 session**|随便开:调 bug、查资料、README、前端、RunPod 上调训练脚本|
|**禁止**|核心功能散在 30 条 session 里,没有一条能代表主干——技术实现分(25% 权重)直接受损|
|**Session ID**|主干线建完立刻 `/feedback` 取 ID,写进 `NOTES.md`(跟 git 走,永不丢)|

### 8.3 三重证据链

1. **Codex Session ID** — 有时间戳、在 OpenAI 后台、无法伪造
2. **Commit 历史** — 第一个 commit 落在 **7/13 09:00 PT 之后**,每天有推进痕迹
3. **README 的 Codex 协作专节** — 被打分的交付物,**从 D1 起每天写一段,禁止 D7 补**

### 8.4 唯一的真实风险:代码复用

> **红线:VerifierForge 的 repo 从零起,一行旧代码都不拷。** PiPlan / Engram / GraphJudge 的思路可以复用,**文件不能复制**。一旦拷进来,项目从"全新"变"已有",凭空背上文档化举证义务。

Devpost 的 Project Overview / Details 只是**草稿**,随时可改,**不承载任何时间证明功能**。

---

## 9. 训练管线规格

**算法:** GRPO(critic-free)。配方基于 1-shot RLVR(arXiv 2504.20571)+ nano-aha-moment 的工程简化。

**为什么是 GRPO:** PPO 要额外养一个 critic,显存翻倍、超参翻倍。GRPO 用「同一 prompt 采 k 个答案的组内均值」当基线——组本身就是 critic。少养一个模型,这是 1.5B 能在几张卡上几小时跑完、产品经济性成立的根源。

**默认超参(1.5B 主力配置):**

|项|值|说明|
|---|---|---|
|模型|Qwen2.5-1.5B-Instruct(主)/ 0.5B(冒烟 & live demo)|Instruct 起点,跳过冷启动|
|rollout k|8|组内基线|
|batch(prompt/步)|32–64|按显存调|
|步数|300–500,熵早停|见刹车机制|
|KL 系数 β|1e-3 起|复读机保险|
|rollout 温度|1.0|保证组内多样性|
|上下文 / 生成长度|1024 / 512|三个垂直任务都短,压成本|
|**checkpoint 间隔**|**每 50 步,经 Storage 落盘**|v2.1:含 optimizer/RNG/step,原子写|

**Reward 分层给分(V1 SQL 示例,写进 Copilot 模板):**

- 能被解析(sqlparse 通过):0.2
- 能被执行(不报错):0.5
- 结果集正确:1.0
- 超长输出:−0.05(防长度 hack)

> 纯 0/1 reward 太稀疏,是"训不动"的头号原因。分层给分模板是 Verifier Copilot 的核心产出物,不是附属品。

**两个内置刹车:**

1. **KL 惩罚** — 防止模型为刷分退化成复读机
2. **熵监控** — 组内 8 条 rollout 去重后 < 2 种 → 判定熵坍塌 → 自动早停并在报告标注

**Spurious 体检(差异化,必须做):** 每个正式 job 附带一个对照 job——同管线、verifier 换成 Bernoulli(0.5) 随机打分、步数减半、模型固定 0.5B。报告并排两条 pass@1 曲线:真 reward 显著高于随机 → 「涨点为真」绿标;两者接近 → 黄色警告「提升可能是格式效应」。

**产品边界(诚实条款,写进 landing page):** RL 不教新知识,它只把模型「已经会但不稳定」的能力压实。**模型完全不会的任务,RL 救不了。** 这也是为什么 5–100 条样例就够。

**Stretch(不阻塞主线):** gpt-oss-20b LoRA GRPO 一次。成了进 demo,不成不提。

**GPU 预算(单次连续窗口 ≤24h,分日使用):**

|用途|配置|时长|
|---|---|---|
|链路冒烟(D1,假 job,走完整 Storage 路径)|任意单卡|5 min|
|管线冒烟(D2)|0.5B 单卡|2–3h|
|主力 job × 3 垂直(D4)|1.5B 多卡,串行|每个 3–5h,共 ~12h|
|Spurious 对照 × 3(D4 穿插)|0.5B 单卡|每个 ~1h|
|S3Storage 验证(D4,复用 V1 主 run)|—|+0(同一 run 换后端)|
|gpt-oss-20b LoRA(D5,stretch)|多卡|≤6h,超时即杀|
|Live demo 小 run(录视频)|0.5B 单卡|20–30 min|

> **纪律:数据和 verifier 没冻结前,一秒 GPU 都不烧;任何 run 曲线 100 步不动就杀(`vf kill`)。**

---

## 10. GPT-5.6 集成点(Stage One 生死项)

规则第一轮是 **pass/fail**:不合赛道、没实质使用指定工具 → 直接淘汰。GPT-5.6 必须是产品**运行时**的器官,不能只是"造它的工具"。

1. **Verifier Copilot** — 任务描述 + 样例 → verifier 代码 + 分层给分模板 + 单元测试
2. **任务扩增引擎** — 5 条样例 → 数百条训练 prompt;扩增结果先过用户自己的 verifier 预筛
    
    > demo 台词:"答案可验证的任务,连扩增数据的质量都是可验证的。"
    
3. **报告叙事** — 训练指标 + spurious 对照 → 自然语言诊断

README 与视频必须显式讲清这三点 + Codex 的构建过程。

---

## 11. 团队分工与并行策略

### 11.1 一个队友,职责边界

||你|队友|
|---|---|---|
|负责|训练管线、Storage、后端、沙箱、GPT-5.6 集成、RunPod|**Demo 视频(剪辑/节奏/叙事/小动画)** + 前端界面|
|红线|不改队友的前端|不碰 `trainer/`、不碰 `core/storage/`|
|交界面|**只有 `core/contracts.py` 那份 JSON 契约**|同左|

### 11.2 并行的前提:D1 交付数据契约 + mock server

D1 晚上前必须交付:

- `core/contracts.py` — job 状态、训练指标、报告结构的 JSON schema
- `mock/server.py` — 二十行 FastAPI,返回假 job / 假曲线 / 假报告
- 契约变更每天同步一次;**D1 定死,之后只加字段不改字段**

**最小契约草案:**

```json
{
  "job_id": "job_abc123",
  "template": "nl2sql",
  "status": "running",          // queued | running | done | failed | early_stopped
  "model": "Qwen2.5-1.5B-Instruct",
  "created_at": "2026-07-14T10:00:00Z",
  "metrics": {
    "steps": [1, 2, 3],
    "reward_mean": [0.21, 0.34, 0.48],
    "pass_at_1": [0.32, 0.41, 0.55],
    "entropy": [1.82, 1.61, 1.44]
  },
  "control": {                   // spurious 对照组
    "pass_at_1": [0.31, 0.32, 0.34]
  },
  "report": {
    "baseline_pass_at_1": 0.32,
    "final_pass_at_1": 0.71,
    "control_final_pass_at_1": 0.35,
    "verdict": "real_gain",      // real_gain | suspect_formatting | collapsed
    "narrative": "……(GPT-5.6 生成)"
  },
  "endpoint": {
    "base_url": "https://…/v1",
    "model_name": "vf-nl2sql-1.5b"
  }
}
```

> 注意:契约里没有 storage 细节——前端不需要知道 checkpoint 在哪。Storage 是你和 trainer 之间的事,契约是你和队友之间的事,**两个接口不要混**。

### 11.3 队友的工作依赖度

|他的任务|依赖你吗|何时可开工|
|---|---|---|
|概念动画(描述任务 → 写评分器 → RL 训练 → 换 base_url)|❌ 完全不依赖|**D1 即可**|
|视频脚本、分镜、配音、节奏(数字留占位符)|❌|**D1 即可**|
|UI 三个页面(对着 mock server 开发)|❌ 只依赖契约|**D1 晚上起**|
|图表样式校准(用 D2 冒烟真曲线)|部分|D2|
|占位符换真数字 + 真实操作录屏|✅|D5|
|视频合成|✅|D6–D7|

> **约 70% 的队友工作量不依赖你的训练结果。** D7 视频日是**你等他**,不是他等你。

---

## 12. Scope 边界(纪律条款)

**In scope(全部,做完即封版):**

- 三个预置垂直模板(V1 必成,V2/V3 加分)
- 单机单队列;简单账号体系(邀请码 + 评委测试账号)
- 训练管线 + spurious 对照;报告页;OpenAI-compatible 端点
- **Storage 抽象 + LocalStorage 全量使用 + S3Storage 在 V1 主线验证一次**
- `vf` 远程控制面(bootstrap/train/watch/logs/status/kill/model)
- 评委用 hosted sandbox(预烤 3 个完成态 job + 1 个可发起的 0.5B live 流程)

**Out of scope(本周一律不做,谁提砍谁):**

- 多租户隔离 / 计费 / 配额;自定义模型上传;多节点训练
- verifier 之外的 reward(LLM judge、偏好对);数据集管理系统
- **自动重调度 / 抢占检测与自动恢复 / FSDP sharded ckpt 跨卡数恢复 / spot 编排 / RunPod API 自动开关机**(全部进 `ideas-post-hackathon.md`)
- 任何 PiPlan 集成(同时是 §8.4 合规红线);移动端适配

---

## 13. 执行计划(D0 = 今夜 7/13,截止 7/21 17:00 PT)

### D0 · 7/13(周一夜,90 分钟)

- [ ] 确认 **Devpost** 注册(openai.devpost.com 点过 "Join Hackathon";Luma 本地活动不算数)
- [ ] 提交 **$100 credits 表**(7/17 12:00 PT 截止,"while supplies last" = 先到先得)
- [ ] **验证 GPT-5.6 API 能真的调通**(唯一会推倒重来的依赖)
- [ ] 发小红书招募帖
- [ ] **笔记本上开 Codex 主 session** → repo 骨架 → 第一个 commit → `/feedback` 取 Session ID 写进 `NOTES.md`
- [ ] 挂通宵下载:Qwen2.5-0.5B / 1.5B-Instruct、gpt-oss-20b(stretch)、vLLM + verl、Docker 镜像

### D1 · 7/14(二)· 骨架 + 链路 + 两份接口定稿

- [ ] **`core/contracts.py` + mock server**(队友解锁的前提,最高优先级)
- [ ] **`core/storage/base.py` 接口定稿 + LocalStorage 实现**(v2.1:和契约同级的第二份 D1 接口)
- [ ] `scripts/vf` + `trainer/bootstrap.sh`;`~/.ssh/config` 配好;GitHub 加只读 Deploy Key
- [ ] **RunPod 链路冒烟:`vf bootstrap` → `vf train`(5 分钟假 job,tmux)→ 指标经 LocalStorage + `vf watch` 回到本地目录**
- [ ] 沙箱 MVP(Docker `--network=none` + 5s 超时)
- [ ] FastAPI + Next.js 脚手架(API server 读 `runs/` 目录 → 前端曲线)
- [ ] V1 SQL 种子数据 50 条(GPT-5.6 生成,顺便验证第一个集成点)
- [ ] 队友到位 → 发契约 + mock server + 动画脚本方向

### D2 · 7/15(三)· 管线冒烟

- [ ] verl GRPO 在 0.5B + V1 上端到端跑通(2–3h,checkpoint 每 50 步经 Storage 落盘)
- [ ] **断点恢复冒烟:`vf kill` 杀掉 → 从 latest checkpoint 续起 → 曲线接上**(LocalStorage 路径,10 分钟,证明 ckpt 纪律是真的)
- [ ] Verifier Copilot(GPT-5.6)第一版
- [ ] 真实曲线截图给队友校准图表样式

### D3 · 7/16(四)· 数据与评测冻结

- [ ] 任务扩增引擎
- [ ] Eval Runner:三个垂直的 baseline(before)全部跑完
- [ ] 报告数据模型定型
- [ ] **当晚冻结 verifier 与数据(不冻结不开训)**

### D4 · 7/17(五)· 主训练日

- [ ] 1.5B × 3 垂直串行主 run + 3 个 spurious 对照(全程 `vf` 指挥,不开 RunPod 网页)
- [ ] **V1 主 run 用 S3Storage 跑(其余 LocalStorage)——一次 run 同时产出主结果和"无状态 worker"验证**
- [ ] 每小时 `vf status` 看曲线;失败任务当场重开,不过夜
- [ ] (行政)确认 credits 已到账

### D5 · 7/18(六)· 交付链路

- [ ] 报告页(曲线 + GPT-5.6 诊断)
- [ ] 端点部署 + `base_url` 一行切换实测;**端点托管方案定稿(§15 三选一)**
- [ ] gpt-oss-20b LoRA stretch(≤6h,超时即杀)
- [ ] 给队友真数字,他开始换占位符

### D6 · 7/19(日)· 产品打磨

- [ ] Onboarding 顺滑化(掐表:粘贴任务 → 发起训练 ≤10 分钟)
- [ ] 评委测试账号 + 3 个预烤 job
- [ ] README 成稿(Codex 协作专节 + Storage 架构一段)
- [ ] 私有 repo 共享给 `testing@devpost.com`、`build-week-event@openai.com`

### D7 · 7/20(一)· 视频日

- [ ] 3 分钟视频:录制 → 剪辑 → 合成 → YouTube 公开
- [ ] Devpost 全部字段填好存草稿
- [ ] Codex Session ID 最终确认

### D8 · 7/21(二)· 缓冲 + 提交

- [ ] 上午最后回归测试
- [ ] **12:00 PT 前提交,绝不卡 17:00**

**关键依赖:** D1 两份接口(契约 + Storage)→ 队友并行 & 训练侧解耦;D1 链路冒烟 → D4 不翻车;D2 断点恢复冒烟 → D4 敢杀敢重开;D3 冻结 → D4 开训;D4 V1 成功 → D5 报告页 + S3 叙事成立。

---

## 14. 提交合规清单

- [ ] 项目用 Codex + GPT-5.6 构建;README 有 "How we worked with Codex" 专节
- [ ] `/feedback` Codex Session ID(主干线 thread)
- [ ] 视频 < 3 分钟、有声、公开 YouTube、覆盖「建了什么 + Codex/GPT-5.6 怎么用」、无第三方音乐/商标
- [ ] repo 私有 + 共享 `testing@devpost.com`、`build-week-event@openai.com`;license 说明(verl / vLLM / boto3 均为宽松许可,合规引用)
- [ ] Dev Tools 附加要求:安装说明 + 无需重建即可测试的方式(hosted sandbox 测试账号)
- [ ] 产品免费可测试保持到 **8/5 评审期结束**(端点托管预算记入)
- [ ] 所有 commit 落在 7/13 09:00 PT 之后;**无一行旧项目代码**
- [ ] 类别 = Developer Tools;文字描述 + 截图齐全
- [ ] credits 已申请(7/17 12:00 PT 前)且 7/31 前用完
- [ ] (若组队)Devpost 上以 Team 形式登记,指定 Representative

---

## 15. 风险与降级路径

|风险|概率|缓解|降级路径|
|---|---|---|---|
|GPT-5.6 API 调不通 / 需额外认证|低|**D0 就验证**|立即走支持渠道;7 天缓冲|
|RunPod 链路(SSH/CUDA/依赖)吃掉半天|高|**D1 `vf bootstrap` + 假 job 跑通全链路**|—|
|裸 SSH 跑训练被 SIGHUP 杀掉|—|**tmux detach 是 `vf train` 内置行为,不存在裸跑路径**|—|
|checkpoint 假恢复(只存权重)|中|§6.5 纪律:optimizer/RNG/step + 原子写;**D2 断点恢复冒烟验证**|—|
|S3 验证吃掉主训练时间|低|复用 V1 主 run(换后端,零额外 GPU);S3Storage 仅 ~80 行|S3 验证挪到 D5;叙事降级为"接口已就绪"|
|1.5B 训练不收敛|中|分层 reward + Instruct 起点 + 熵早停|降 0.5B;仍不行则以收敛的垂直为唯一主案例|
|队友找不到|中|D0 发帖|视频自己做:动画砍掉,纯录屏 + 旁白|
|契约中途大改|中|D1 定死,只加字段不改字段|—|
|沙箱逃逸 / 滥用|低|无网络 Docker + 资源限额 + 只读|demo 期只开放预置模板的 verifier 编辑|
|Scope 膨胀|**高**|§12 即法律|新想法进 `ideas-post-hackathon.md`|
|评委不实测(规则允许)|高|视频承载全部说服力|—|
|RunPod 实例销毁|—|主 session 在笔记本;状态全在 Storage;pod 无状态|新 pod:`vf bootstrap` 10 分钟满血复活|
|**评委端点托管断供(到 8/5)**|中|**D5 定稿三选一:A. RunPod Serverless(scale-to-zero,冷启动 ~30s,最省)/ B. 常驻小卡跑量化模型(简单但持续烧钱)/ C. 0.5B 走 CPU llama.cpp(几乎免费,demo 够用)**|预烤 job + 视频兜底(规则允许评委只看视频)|

---

## 16. 验收标准(Definition of Done)

1. 三个垂直中 **≥1 个(V1 必须)**:pass@1 相对 baseline 提升 ≥20 个百分点,且 spurious 对照差距显著
2. 「粘贴任务描述 → 发起训练」全流程 ≤10 分钟(掐表,录进视频)
3. 交付端点用官方 `openai` SDK **只改 `base_url`** 即可调用(视频演示)
4. 评委测试账号登录后 3 分钟内能看懂一份完成态报告
5. **`vf kill` 杀掉训练后能从 latest checkpoint 续起(D2 验证过);V1 主 run 的状态完整存在 S3**
6. §14 清单全绿

---

## 附 A · Demo 视频叙事骨架(3 分钟)

|时间|内容|
|---|---|
|0:00–0:20|痛点:高频窄任务烧 API 钱,但小模型开箱即用不可靠|
|0:20–1:20|主流程实录:描述任务 → GPT-5.6 写 verifier → 发起训练(加速播放)|
|1:20–2:10|**报告页:before/after 曲线 + spurious 打假报告**(差异化高光)|
|2:10–2:40|端点切换实测:改一行 `base_url`,原代码直接跑,成本对比|
|2:40–3:00|Codex 协作快剪 + 收尾一句定位话术|

---

## 附 B · 评委问答备忘

**Q: 这和 verl / TRL 有什么区别?**

> 框架假设你会写 reward 函数。我们假设你不会——你描述任务,GPT-5.6 把 verifier 工程化,我们在你上线前证明涨点是真的。工作单位从「一个 ML 项目」变成「10 分钟配置」。

**Q: 这和 OpenAI 的 RFT 有什么区别?**

> 我们不是替代,是延伸。GPT-5.6 在这里是设计者和监督者——它写评分器、造训练数据、解读结果;产出的是一个你自己拥有、可以低成本大量调用的小模型。

**Q: 算法上有什么创新?**

> 训练循环是标准 GRPO,我们不假装它是新的。创新在三个别人没做的地方:reward 工程的自动化、训练结果的可信度证明、以及从自然语言到生产端点的完整闭环。

**Q: 用户为什么不自己跑 verl?**

> 能自己跑 verl 的人不是我们的用户。我们的用户是每天被同一个窄任务的 API 账单折磨、但没有 ML 团队的后端工程师。

**Q: 训练跑在哪?挂了怎么办?(v2.1 新增)**

> GPU worker 是无状态的:checkpoint、指标、最终权重全部走一个可插拔的 Storage 层,本地卷和 S3 都是它的后端——V1 那条主线就是全程存在 S3 上跑完的。节点死了,换一台从最新 checkpoint 续。完整的自动重调度在路线图上,但架构从第一天就是为它设计的。