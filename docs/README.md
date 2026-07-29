# 私域客服自治工作台（AgentDesk）

> GOAI 2026 · 新智基座（Agent Infra）赛道 · 方向二：智能客服自主闭环

## 项目简介

AgentDesk 是基于 **AgentTeams** 构建的多 Agent 私域客服自治工作台，面向企业私域运营中的多渠道会话场景，完成「聚合 → 分级 → 方案 → 执行 → 核验 → 审批 → 沉淀」端到端闭环。

- **主渠道**：抖音私信
- **扩展渠道**：企业微信
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
| [09_代码仓库说明.md](docs/09_代码仓库说明.md) | 仓库与三项目关系 |
| [10_运行说明.md](docs/10_运行说明.md) | 初赛可验证范围 |
| [11_当前完成度与复赛计划.md](docs/11_当前完成度与复赛计划.md) | 诚实完成度说明 |
| [12_附件索引.md](docs/12_附件索引.md) | 附件索引 |

## 重要说明

- **Demo 录屏来源**：AgentDesk 抖音 Channel Runtime（`8765/console`），不是 `127.0.0.1:5173`
- `5173` 是独立的小红书项目 `xhs-ai-kefu`，不属于本次 AgentDesk 初赛主场景
- **Trace 工作台 / Task Runtime**：复赛实现项，初赛为设计稿

## 当前进展

- ✅ 初赛方案设计与 AgentTeams 映射
- ✅ 抖音私信托管与发送核验（业务底座）
- 🔄 企微适配、AgentTeams 可执行代码包（复赛）

## License

[MIT](../LICENSE)（仓库根目录）
