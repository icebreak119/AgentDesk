# AgentDesk Agent Identity 清单

> 赛道：新智基座（Agent Infra）｜方向：智能客服自主闭环  
> 协同框架：AgentTeams（Manager–Team Leader–Worker）

## 1. 系统总览

| 项目 | 说明 |
|---|---|
| 系统名称 | 私域客服自治工作台（AgentDesk） |
| 业务场景 | 多渠道私域会话自动接待、分级处置、结果核验与知识沉淀 |
| 主渠道 | 抖音私信 |
| 扩展渠道 | 企业微信 |
| Agent 数量 | 5（Manager 1 + TL 1 + Worker 3） |
| 设计基点 | AgentTeams 角色编排、任务拆解、上下文传递、协同执行、状态追踪 |

## 2. Agent Identity 明细

### 2.1 DutyManager（值班长 Agent）

| 属性 | 内容 |
|---|---|
| 层级 | Manager |
| 身份 ID | `agent.duty_manager` |
| 身份定义 | 接收归一化会话任务，负责任务拆解、优先级判定、升级与审批决策 |
| 核心职责 | 创建 Task；拆分为 ingress→triage→act→verify→digest；决定自动/人工/挂起 |
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
| 核心职责 | Worker 编排；上下文组装；状态追踪；异常分支路由 |
| 能力边界 | 调度 ChannelIngress / TriageGuard / ActVerify |
| 禁止事项 | 不直接操作渠道底层 API |
| 输入 | TaskPlan、各 Worker 中间结果 |
| 输出 | Worker 调用指令、TaskContext、TaskStatus |
| 协作对象 | 全部 Worker；向 DutyManager 汇报 |
| 状态追踪 | pending → triaging → planning → acting → verifying → done/failed/escalated |
| 失败策略 | Worker 超时重试 1 次，仍失败则上报 DutyManager |

### 2.3 ChannelIngress（渠道接入 Worker）

| 属性 | 内容 |
|---|---|
| 层级 | Worker |
| 身份 ID | `agent.channel_ingress` |
| 身份定义 | 多渠道入站消息接收、去重、会话归一 |
| 核心职责 | 监听抖音/企微入站；去重；输出统一 SessionEvent |
| 能力边界 | 只读渠道入站；调用 SessionNormalizeSkill |
| 禁止事项 | 不做意图判断；不发送消息 |
| 输入 | 渠道原始事件（抖音 IM / 企微 Hook） |
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
| 核心职责 | 调用 ChannelSendSkill；调用 OutcomeVerifySkill；写证据 |
| 能力边界 | 在审批通过后执行发送；产出 verify evidence |
| 禁止事项 | 未审批不得执行高风险动作 |
| 输入 | ReplyDraft、ApprovalToken、channel_ref |
| 输出 | SendReceipt、VerifyResult、EvidenceBundle |
| 协作对象 | SessionTL |
| 安全边界 | 短文本必须 DOM/回执二次校验；失败不入库为成功 |
| 失败策略 | 发送失败有限重试；核验失败标记 failed 并告警 |

## 3. AgentTeams 映射

| AgentTeams 能力 | AgentDesk 映射 | 证据形态 |
|---|---|---|
| Manager 全局监管与拆解 | DutyManager | TaskPlan、审批记录 |
| Team Leader 团队调度 | SessionTL | TaskContext、状态流转日志 |
| Worker 执行单元 | ChannelIngress / TriageGuard / ActVerify | Skill 调用结果 |
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
      → ActVerify（执行+核验）
  → DutyManager（审批/升级决策）
  → SessionTL（闭环完成/复盘触发）
```

## 5. 与现有工程映射（真实性支撑）

| AgentDesk 角色 | 现有工程能力 |
|---|---|
| ChannelIngress | 抖音/企微入站监听与消息归一 |
| TriageGuard | AI 回复意图判断 + 重要咨询升级 |
| ActVerify | send_im_message + 发送结果校验日志 |
| SessionTL | 会话状态与任务编排（复赛 AgentTeams 落地） |
| DutyManager | 人工审批与高风险拦截（复赛完善） |
