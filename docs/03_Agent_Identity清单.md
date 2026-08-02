# AgentDesk Agent Identity 清单

> 赛道：新智基座（Agent Infra）｜方向：智能客服自主闭环  
> 协同框架：AgentTeams（Manager–Team Leader–Worker）

## 1. 系统总览

| 项目 | 说明 |
|---|---|
| 系统名称 | 私域客服自治工作台（AgentDesk） |
| 业务场景 | 多渠道私域会话自动接待、分级处置、结果核验与知识沉淀 |
| 主渠道 | 抖音私信 |
| 扩展渠道 | 企业微信（初赛统一契约设计，复赛接入适配器） |
| Agent 数量 | 6（Manager 1 + TL 1 + Worker 4） |
| 设计基点 | AgentTeams 角色编排、任务拆解、上下文传递、协同执行、状态追踪 |

## 2. Agent Identity 明细

### 2.1 DutyManager（值班长 Agent）

| 属性 | 内容 |
|---|---|
| 层级 | Manager |
| 身份 ID | `agent.duty_manager` |
| 身份定义 | 接收归一化会话任务，负责任务拆解、优先级判定、升级与审批决策 |
| 核心职责 | 创建 Task；拆分为 ingress→triage→act→verify→confirm→digest；决定自动/人工/挂起 |
| 能力边界 | 可读全局任务状态、触发审批、下发子任务 |
| 禁止事项 | 不直接调用渠道发送 API；不修改渠道全局配置 |
| 输入 | SessionEvent、TriageResult、VerifyResult |
| 输出 | TaskPlan、ApprovalRequest、EscalationDecision |
| 协作对象 | SessionTL（下发/回收任务） |
| 安全边界 | 高风险动作必须经审批闸门 |
| 失败策略 | 无法判定优先级时默认升级人工 |

### 2.2 SessionTL（会话编排 Team Leader）

| 属性 | 内容 |
|---|---|
| 层级 | Team Leader |
| 身份 ID | `agent.session_tl` |
| 身份定义 | 调度 Worker，维护任务状态机与上下文传递 |
| 核心职责 | Worker 编排；上下文组装；状态追踪；异常分支路由；跨渠道重复任务拦截 |
| 能力边界 | 调度 ChannelIngress / TriageGuard / ActVerify / CaseLearning |
| 禁止事项 | 不直接操作渠道底层 API |
| 输入 | TaskPlan、各 Worker 中间结果 |
| 输出 | Worker 调用指令、TaskContext、TaskStatus |
| 协作对象 | 全部 Worker；向 DutyManager 汇报 |
| 状态追踪 | pending → triaging → deduplicated 或 planning → acting → verifying → confirming → done / awaiting_customer_confirmation / failed / escalated |
| 失败策略 | Worker 超时重试 1 次，仍失败则上报 DutyManager |

### 2.3 ChannelIngress（渠道接入 Worker）

| 属性 | 内容 |
|---|---|
| 层级 | Worker |
| 身份 ID | `agent.channel_ingress` |
| 身份定义 | 多渠道入站消息接收、去重、会话归一 |
| 核心职责 | 抖音入站归一；依据统一 SessionEvent 生成跨渠道去重键；输出规范事件 |
| 能力边界 | 只读渠道入站；调用 SessionNormalizeSkill |
| 禁止事项 | 不做意图判断；不发送消息 |
| 输入 | 抖音 IM 原始事件；企微离线契约样例（不声明真实 Hook 已接入） |
| 输出 | SessionEvent（channel, profile_id, session_id, content, ts） |
| 协作对象 | SessionTL |
| 安全边界 | 禁止将会话级 conversation_id 污染全局入口配置 |
| 失败策略 | 归一失败记录日志并终止任务 |

### 2.4 TriageGuard（意图风控 Worker）

| 属性 | 内容 |
|---|---|
| 层级 | Worker |
| 身份 ID | `agent.triage_guard` |
| 身份定义 | 意图识别、工单分级、风险判定 |
| 核心职责 | 分类意图；评估优先级；标记 need_approval |
| 能力边界 | 调用 IntentTriageSkill、ReplyPlanSkill（仅草案） |
| 禁止事项 | 不执行发送；不修改知识库 |
| 输入 | SessionEvent、历史上下文 |
| 输出 | TriageResult（intent, priority, risk_tag, need_approval） |
| 协作对象 | SessionTL |
| 安全边界 | 退款/改账户/敏感信息默认 need_approval=true |
| 失败策略 | confidence < 0.6 时升级人工 |

### 2.5 ActVerify（执行核验 Worker）

| 属性 | 内容 |
|---|---|
| 层级 | Worker |
| 身份 ID | `agent.act_verify` |
| 身份定义 | 方案执行、结果核验、执行证据沉淀 |
| 核心职责 | 调用 ChannelSendSkill、OutcomeVerifySkill、CustomerConfirmSkill；写执行与确认证据 |
| 能力边界 | 在审批通过后执行发送；产出 verify evidence |
| 禁止事项 | 未审批不得执行高风险动作 |
| 输入 | ReplyDraft、ApprovalToken、channel_ref |
| 输出 | SendReceipt、VerifyResult、CustomerConfirmResult、EvidenceBundle |
| 协作对象 | SessionTL |
| 安全边界 | 短文本必须 DOM/回执二次校验；失败不入库为成功 |
| 失败策略 | 发送失败有限重试；核验失败标记 failed 并告警 |

### 2.6 CaseLearning（案例学习 Worker）

| 属性 | 内容 |
|---|---|
| 层级 | Worker |
| 身份 ID | `agent.case_learning` |
| 身份定义 | 在终态后沉淀脱敏案例，并在同意图/风险标签的后续任务中提供可复用历史片段 |
| 核心职责 | 标签检索；调用 CaseDigestSkill；写入匿名 JSONL CaseDigest；返回 `case://` 引用 |
| 能力边界 | 只处理意图、风险、处置结果、核验和确认状态，不读取或写入客户原文、身份与凭据 |
| 禁止事项 | 不直接发送消息；不把 CaseDigest 夸大为完整向量 RAG；不保存 PII |
| 输入 | SessionEvent 的匿名标识、TriageResult、VerifyResult、CustomerConfirmResult、任务终态 |
| 输出 | `knowledge_hits[]`、CaseDigest、`case://case_id` 引用 |
| 协作对象 | SessionTL、TriageGuard（经 `knowledge_hits` 注入 ReplyPlan） |
| 安全边界 | 仅终态 `done/failed/escalated` 可归档；隐私字段必须为 false |
| 失败策略 | 归档失败不伪造成功，保留主任务终态并在 Trace 标记异常 |

## 3. AgentTeams 映射

| AgentTeams 能力 | AgentDesk 映射 | 证据形态 |
|---|---|---|
| Manager 全局监管与拆解 | DutyManager | TaskPlan、审批记录 |
| Team Leader 团队调度 | SessionTL | TaskContext、状态流转日志 |
| Worker 执行单元 | ChannelIngress / TriageGuard / ActVerify / CaseLearning | Skill 调用结果、CaseDigest |
| 任务拆解 | DutyManager → SessionTL 状态机 | task_id 维度 Trace |
| 上下文传递 | TaskContext 共享对象 | 中间结论 JSON |
| 协同执行 | SessionTL 按状态调度 Worker | agent/skill/tool 链路日志 |
| 状态追踪 | TaskStatus + Trace/Log | Metrics：成功率、升级率 |

## 4. 协同关系图

```
客户消息
  → ChannelIngress（归一）
  → SessionTL
      → TriageGuard（分级）
      → ActVerify（执行+核验+客户确认）
      → CaseLearning（案例检索+脱敏沉淀）
  → DutyManager（审批/升级决策）
  → SessionTL（闭环完成/复盘触发）
```

## 5. 与现有工程映射（真实性支撑）

| AgentDesk 角色 | 现有工程能力 |
|---|---|
| ChannelIngress | 抖音入站监听与消息归一；企微仅按统一 SessionEvent 契约作离线演示 |
| TriageGuard | 初赛规则化意图判断、风险分级与升级；可替换为 LLM |
| ActVerify | send_im_message + 发送结果校验日志 |
| CaseLearning | 标签化案例检索 + 匿名 JSONL CaseDigest；非完整 RAG |
| SessionTL | 初赛参考编排器的会话状态与任务编排；复赛替换为 AgentTeams 官方运行时 |
| DutyManager | 初赛参考编排器的审批闸门与高风险拦截；复赛产品化审批 API |
