# AgentTeams 映射与接口契约

> 初赛阶段：`orchestrator/` 是 AgentTeams Manager–Team Leader–Worker 能力映射的可运行参考实现；复赛替换为官方 Runtime，任务语义、Skill Schema、工具契约和 Trace 字段保持不变。

## 1. 角色映射

| AgentTeams 概念 | AgentDesk 实现 | 代码入口 | 责任边界 |
|---|---|---|---|
| Manager | `DutyManager` | `orchestrator/agents/duty_manager.py` | 创建任务、审批挂起、批准/拒绝、人工升级 |
| Team Leader | `SessionTL` | `orchestrator/agents/session_tl.py` | 拆解任务、编排 Worker、传递上下文、推进状态 |
| Worker | `ChannelIngress` | `orchestrator/agents/workers/channel_ingress.py` | 渠道归一、SessionEvent、去重 |
| Worker | `TriageGuard` | `orchestrator/agents/workers/triage_guard.py` | 意图、优先级、风险和方案草案 |
| Worker | `ActVerify` | `orchestrator/agents/workers/act_verify.py` | BusinessAction、ChannelSend、结果核验、客户确认 |
| Worker | `CaseLearning` | `orchestrator/agents/workers/case_learning.py` | 案例检索、脱敏 CaseDigest、经验复用 |

## 2. 协同接口

各层之间传递的是任务上下文和结构化消息，不传递渠道凭据。官方 AgentTeams 运行时接入时，以下方法可分别映射为 Manager dispatch、Team Leader handoff 和 Worker result。

```json
{
  "message_id": "msg_task_002_03",
  "task_id": "task_002",
  "from_agent": "agent.session_tl",
  "to_agent": "agent.act_verify",
  "intent": "execute_approved_refund",
  "risk_tag": "high",
  "context_ref": "task://task_002",
  "payload": {
    "skill": "BusinessAction",
    "action_type": "refund",
    "approval_scope": "sha256:refund-request-scope"
  },
  "evidence_refs": ["trace://task_002/business_action_requested"],
  "expected_state": "acting"
}
```

| 协同动作 | 当前参考实现 | 输入 | 输出/证据 |
|---|---|---|---|
| 创建任务 | `DutyManager.create_task` | 原始渠道事件 | `TaskContext(state=pending)` |
| 拆解与首轮调度 | `SessionTL.run_until_gate` | `TaskContext` | `SessionEvent`、TriageResult、ReplyPlan |
| 高风险挂起 | `DutyManager.suspend_for_approval` | `need_approval=true` | `approval_required`、`state=suspended` |
| 审批恢复 | `DutyManager.grant_approval` + `SessionTL.resume_after_approval` | 审批令牌 | `approval_granted`、BusinessAction 请求 |
| Worker 交接 | `SessionTL` 调用 Worker helper | 上一步结构化结果 | `agent/skill/status` Trace；跨 Agent 传输使用 `to_wire_dict()` 脱敏 |
| 失败恢复 | `SessionTL.execute_business_action_and_notify` | 核验/回滚结果 | `failed` 或 `escalated` |
| 终态沉淀 | `SessionTL.publish_case` | 终态 TaskContext | 匿名 CaseDigest |

## 3. 状态与上下文

```text
pending
  -> triaging -> planning
  -> suspended --approval rejected--> failed
  -> approved -> acting -> business_action_verified
  -> verifying -> confirming -> done / awaiting_customer_confirmation
  -> business_action_verified --notification failure--> failed
  -> action verification failure -> rollback -> failed
  -> rollback failure -> escalated -> human_review
```

`TaskContext` 是参考实现的共享状态对象，包含 `task_id`、`profile_id`、`session_event`、`triage_result`、`reply_draft`、`business_action`、`verify_result`、`customer_confirm_result` 和 `case_digest`。原始客户内容只在入站到运行时内存链路使用；跨 Agent 或持久化传输必须使用 `TaskContext.to_wire_dict()`，审批令牌与客户字段会被脱敏。Trace 与 CaseDigest 只保留哈希、状态、操作 ID 和证据引用。

## 4. 官方 Runtime 迁移边界

| 保持不变 | 替换部分 |
|---|---|
| Agent Identity、Skill Schema、BusinessAction 输入输出 | Manager / Team Leader 的运行时注册方式 |
| `task_id`、`profile_id`、`idempotency_key` | 消息投递、并发调度和持久化 Task Store |
| `approval_required`、`business_action_verified`、`escalated` 事件 | AgentTeams 官方 handoff / team API 适配器 |
| MCP 等价工具契约、错误码和审计字段 | 本地 JSONL Trace 写入器替换为 Trace/Evidence 后端 |

因此复赛接入官方 AgentTeams 时，改动集中在 `orchestrator/` 调度适配层，不需要重写退款 Skill、企业动作 API、核验规则或安全不变量。

## 5. 必须保持的不变量

1. `approval_token` 缺失时不得创建退款操作。
2. `approval_token` 过期、签名无效或范围与当前任务/账号/动作/订单/金额/原因/幂等键不匹配时不得执行。
3. `business_action_verified=verified` 之前不得发送退款成功通知。
4. 回滚失败必须进入 `escalated`，Trace 必须包含 `next_action=human_review`。
5. 每个企业动作使用 `profile_id + idempotency_key` 幂等。
6. Trace、业务动作 JSONL 和案例档案不得写入客户原文、姓名或账号凭据。
