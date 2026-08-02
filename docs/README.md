# 私域客服自治工作台（AgentDesk）

> GOAI 2026 · 新智基座（Agent Infra）赛道 · 方向二：智能客服自主闭环

## 项目简介

AgentDesk 以 **AgentTeams** 作为多 Agent 协同设计基点，面向企业私域运营中的多渠道会话场景。初赛参考编排器验证「接入与去重 → 分级 → 方案 → 执行 → 核验 → 客户确认 → 匿名案例沉淀 → 审批」闭环；完整 RAG 为复赛项。

- **主渠道**：抖音私信
- **扩展渠道**：企业微信本地 Webhook 适配器（统一 `SessionEvent`；生产验签与队列为复赛项）
- **协同框架**：AgentTeams（Manager–Team Leader–Worker）

## 初赛材料（docs/）

| 文件 | 说明 |
|---|---|
| [01_作品简介.txt](docs/01_作品简介.txt) | 作品简介 |
| [02_方案PPT.pdf](docs/02_方案PPT.pdf) | 方案 PPT |
| [03_Agent_Identity清单.md](docs/03_Agent_Identity清单.md) | Agent Identity |
| [04_Skill清单.md](docs/04_Skill清单.md) | Skill 清单 |
| [05_多Agent闭环说明.md](docs/05_多Agent闭环说明.md) | 闭环说明 |
| [06_架构图.png](docs/06_架构图.png) | 架构图 |
| [08_Demo演示脚本.md](docs/08_Demo演示脚本.md) | Demo 录屏剧本 |
| [09_代码仓库说明.md](docs/09_代码仓库说明.md) | 仓库与独立交付说明 |
| [10_运行说明.md](docs/10_运行说明.md) | 初赛可验证范围 |
| [11_当前完成度与复赛计划.md](docs/11_当前完成度与复赛计划.md) | 诚实完成度说明 |
| [12_附件索引.md](docs/12_附件索引.md) | 附件索引 |
| [17_PPT视觉设计说明.md](docs/17_PPT视觉设计说明.md) | PDF 视觉参考与原创素材说明 |

## 重要说明

- **Demo 录屏来源**：`demo_runtime` 实时编排页（`8780`），调用 `8770` 企业动作 API 与 `8771` 企微 Webhook
- 本提交不依赖其他本地项目、历史桌面宿主或其端口
- **Trace 工作台 / 生产 Task DB / 完整 RAG**：复赛实现项；初赛已提供实时编排、Trace JSONL、客户确认、HTTP 退款核验/回滚和匿名 CaseDigest

## 当前进展

- ✅ 初赛方案设计与 AgentTeams 映射
- ✅ 抖音私信托管与发送核验（业务底座）
- ✅ 6 Agent、8 Skill、剧本 A/B/C（含退款动作核验/回滚、跨渠道去重、客户确认和案例复用）
- ✅ 本地企业微信 Webhook 与独立 HTTP 企业业务模拟器
- 🔄 企业微信生产验签/队列、AgentTeams 官方运行时、完整 RAG（复赛）

## License

[MIT](../LICENSE)（仓库根目录）
