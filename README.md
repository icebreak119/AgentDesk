# 私域客服自治闭环（AgentDesk）

> GOAI 2026 · 新智基座（Agent Infra）赛道 · 方向二：智能客服自主闭环

## 项目简介

AgentDesk 是基于 **AgentTeams** 构建的多 Agent 私域客服自治基础设施，面向企业私域运营中的多渠道会话场景，完成「聚合 → 分级 → 方案 → 执行 → 核验 → 审批 → 沉淀」端到端闭环。

- **主渠道**：抖音私信
- **扩展渠道**：企业微信
- **协同框架**：AgentTeams（Manager–Team Leader–Worker）

## 初赛材料

| 文件 | 说明 |
|---|---|
| [docs/01_作品简介.txt](docs/01_作品简介.txt) | 作品简介（≤500 字） |
| [docs/02_方案PPT大纲.md](docs/02_方案PPT大纲.md) | 12 页 PPT 大纲（导出 PDF 后用于 zip 提交） |
| [docs/03_Agent_Identity清单.md](docs/03_Agent_Identity清单.md) | Agent Identity 清单 |
| [docs/04_Skill清单.md](docs/04_Skill清单.md) | 核心 Skill 清单 |
| [docs/05_多Agent闭环说明.md](docs/05_多Agent闭环说明.md) | 多 Agent 闭环说明 |
| [docs/README.md](docs/README.md) | 打包与提交说明 |

## 架构概览

```
客户消息（抖音/企微）
  → ChannelIngress（归一）
  → SessionTL（编排）
      → TriageGuard（分级）
      → ActVerify（执行+核验）
  → DutyManager（审批/升级）
  → 证据沉淀 / 案例复盘
```

## 核心 Agent

| Agent | 层级 | 职责 |
|---|---|---|
| DutyManager | Manager | 任务拆解、优先级、审批决策 |
| SessionTL | Team Leader | Worker 调度、上下文传递、状态追踪 |
| ChannelIngress | Worker | 多渠道入站、去重、会话归一 |
| TriageGuard | Worker | 意图识别、分级、风险判定 |
| ActVerify | Worker | 发送执行、结果核验、证据沉淀 |

## 核心 Skill

- `SessionNormalize` — 多渠道会话归一
- `IntentTriage` — 意图识别与分级
- `ReplyPlan` — 回复/处置方案生成
- `ChannelSend` — 渠道消息发送（幂等、防串号）
- `OutcomeVerify` — 结果核验与执行证据

## 当前进展

- ✅ 初赛方案设计与 AgentTeams 映射
- ✅ 抖音私信托管与发送核验能力（业务底座）
- 🔄 企微收发适配
- 📋 复赛：可执行 AgentTeams 代码包 + Demo

## License

TBD
