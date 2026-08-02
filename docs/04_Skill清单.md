# AgentDesk 核心 Skill 清单

> **注册表（可执行索引）**：[`skills/registry.yaml`](../skills/registry.yaml)  
> **CLI 样例**：`python skills/run_skill.py intent_triage -i skills/intent_triage/v0.1/examples/consult.json`

## Skill 总览

| Skill | 版本 | 用途 | 调用 Agent | 包路径 | 复用价值 |
|---|---|---|---|---|---|
| SessionNormalize | v0.1 | 多渠道会话归一 | ChannelIngress | `skills/session_normalize/v0.1/` | 换渠道只换适配器 |
| IntentTriage | v0.1 | 意图识别与分级 | TriageGuard | `skills/intent_triage/v0.1/` ✅ 可运行 | 规则+模型可复用 |
| ReplyPlan | v0.1 | 回复/处置方案生成 | TriageGuard | `skills/reply_plan/v0.1/` ✅ 可运行 | 知识增强、风险标签 |
| ChannelSend | v0.1 | 渠道消息发送 | ActVerify | `skills/channel_send/v0.1/` + runtime | 幂等、防串号 |
| OutcomeVerify | v0.1 | 结果核验与证据 | ActVerify | `skills/outcome_verify/v0.1/` + runtime | 可审计证据输出 |
| CustomerConfirm | v0.1 | 客户反馈确认与升级判定 | ActVerify | `skills/customer_confirm/v0.1/` ✅ 可运行 | 处置闭环不止于发送 |
| CaseDigest | v0.1 | 脱敏案例归档与标签复用 | CaseLearning | `skills/case_digest/v0.1/` ✅ 可运行 | 结构化经验沉淀 |
| BusinessAction | v0.1 | 高风险退款动作请求校验与企业系统调用 | ActVerify | `skills/business_action/v0.1/` + `HttpBusinessActionAdapter` | 订单查询、审批、幂等、核验、补偿回滚 |

---

## 1. SessionNormalizeSkill

| 项 | 内容 |
|---|---|
| 用途 | 将抖音/企微原始入站事件转为统一 SessionEvent |
| 输入 Schema | `{ channel: string, raw_event: object, profile_id: string }` |
| 输出 Schema | `{ session_id, customer_ref, content, content_type, ts, dedupe_key }` |
| 调用条件 | 任意渠道入站消息到达 |
| 依赖工具 | 抖音 Runtime；企微统一事件契约仅用于离线演示 |
| 失败处理 | 记录日志并丢弃；不进入后续链路 |
| 验证方式 | 字段完整性校验；dedupe_key 去重 |
| 安全边界 | 不修改全局入口 URL；cid 仅作会话级标识 |
| 复用价值 | 新渠道只需实现 raw_event → SessionEvent 适配 |
| 与多 Agent 关系 | ChannelIngress 专属调用，结果交 SessionTL |

## 2. IntentTriageSkill

| 项 | 内容 |
|---|---|
| 用途 | 识别意图、评估优先级与风险、判定是否需审批 |
| 输入 Schema | `{ session_event, history: Message[] }` |
| 输出 Schema | `{ intent, priority, risk_tag, need_approval, confidence }` |
| 调用条件 | SessionEvent 归一成功后 |
| 依赖工具 | 规则引擎（敏感词/高风险模板，初赛参考实现）；后续可替换为 LLM |
| 失败处理 | confidence 低则默认升级人工 |
| 验证方式 | 规则命中与模型输出一致性检查 |
| 安全边界 | 不得触发任何外部写操作 |
| 复用价值 | 意图模板可跨行业迁移 |
| 与多 Agent 关系 | TriageGuard 调用，结论供 DutyManager 决策 |

## 3. ReplyPlanSkill

| 项 | 内容 |
|---|---|
| 用途 | 基于会话与知识生成回复/处置草案 |
| 输入 Schema | `{ session_event, triage_result, knowledge_hits[] }` |
| 输出 Schema | `{ draft_text, action_type, risk_tag, citations[] }` |
| 调用条件 | triage 完成且非立即升级 |
| 依赖工具 | 版本化回复模板与标签化历史案例检索（初赛参考实现）；后续可替换为 LLM / 完整 RAG |
| 失败处理 | 无有效草案 → 转人工 |
| 验证方式 | 草案非空；高风险不得附带执行令牌 |
| 安全边界 | 高风险仅出方案，不附带执行令牌 |
| 复用价值 | Prompt/知识库可版本化管理 |
| 与多 Agent 关系 | TriageGuard 生成草案，ActVerify 消费 |

## 4. ChannelSendSkill

| 项 | 内容 |
|---|---|
| 用途 | 向指定渠道发送消息，保证幂等与账号隔离 |
| 输入 Schema | `{ channel, profile_id, session_ref, content, idempotency_key }` |
| 输出 Schema | `{ send_id, status, receipt_raw, ts }` |
| 调用条件 | 低风险自动路径，或高风险已获 ApprovalToken |
| 依赖工具 | 抖音 send_im_message；企微发送仅保留工具契约，未声明真实适配器已接入 |
| 失败处理 | 有限重试；ticket 失效则清缓存重 resolve |
| 验证方式 | 回执 status=ok；profile_id 与目标会话一致 |
| 安全边界 | 必须带 profile_id；禁止跨账号发送 |
| 复用价值 | 发送契约统一，渠道差异下沉到适配器 |
| 与多 Agent 关系 | ActVerify 专属调用 |

## 5. OutcomeVerifySkill

| 项 | 内容 |
|---|---|
| 用途 | 校验发送结果与客户侧实际展示是否一致 |
| 输入 Schema | `{ expected_content, send_receipt, session_ref }` |
| 输出 Schema | `{ pass, actual_content, evidence_type, evidence_ref }` |
| 调用条件 | ChannelSend 完成后 |
| 依赖工具 | 回执解析；DOM 二次读取（短文本场景） |
| 失败处理 | verify 失败 → 标记 failed，不记为成功回复 |
| 验证方式 | expected/actual 一致；短文本唯一候选 DOM 校验 |
| 安全边界 | 候选必须唯一；不一致则失败 |
| 复用价值 | 核验协议可独立开源，适用于多渠道客服 |
| 与多 Agent 关系 | ActVerify 调用，结果回传 DutyManager/SessionTL |

---

## 6. CustomerConfirmSkill

| 项 | 内容 |
|---|---|
| 用途 | 对处置后的客户反馈做确认、待跟进或升级判定 |
| 输入 Schema | `{ task_id, customer_feedback }` |
| 输出 Schema | `{ confirmation_state, needs_follow_up, feedback_summary, evidence_ref }` |
| 调用条件 | OutcomeVerify 通过后收到客户反馈；无反馈时返回 `awaiting_feedback` |
| 依赖工具 | 无外部写操作；初赛剧本使用脱敏反馈文本 |
| 失败处理 | 反馈不明确时保持等待确认，不将任务伪造为 done |
| 验证方式 | 正向、负向和空反馈样例；pytest 覆盖 |
| 安全边界 | 不写入客户身份、原文或凭据到案例档案 |
| 复用价值 | 可复用于售后、工单回访和满意度确认流程 |
| 与多 Agent 关系 | ActVerify 调用，结果决定 SessionTL 的 done / escalated / awaiting_customer_confirmation |

## 7. CaseDigestSkill

| 项 | 内容 |
|---|---|
| 用途 | 生成隐私安全的结构化案例摘要，并以标签供后续同类任务检索 |
| 输入 Schema | `{ task_id, channel, triage_result, verify_result, customer_confirm_result, resolution }` |
| 输出 Schema | `{ case_id, intent, risk_tag, resolution, verification, customer_confirmation, reusable_tags, privacy }` |
| 调用条件 | 任务进入 `done`、`failed` 或 `escalated` 终态后 |
| 依赖工具 | `CaseLearning` Worker 的匿名 JSONL 档案 |
| 失败处理 | 不影响已确认的主任务终态；Trace 记录归档异常 |
| 验证方式 | pytest 校验隐私字段；剧本 C 检验后续任务命中 `case://` 引用 |
| 安全边界 | `contains_customer_identity/content/credential` 必须均为 false |
| 复用价值 | 以意图/风险/处置标签复用经验；当前为结构化标签检索，非完整 RAG |
| 与多 Agent 关系 | CaseLearning 调用，`knowledge_hits` 传给 TriageGuard 的 ReplyPlan |

## 8. BusinessActionSkill

| 项 | 内容 |
|---|---|
| 用途 | 在审批通过后调用企业退款动作，并输出可核验的操作证据 |
| 输入 Schema | `{ task_id, profile_id, action_type, order_id, amount, currency, reason, idempotency_key, approval_token }` |
| 输出 Schema | `{ operation_id, action_type, status, idempotency_key, evidence_ref, error_code, rollback_of }` |
| 调用条件 | `IntentTriage.requested_action=refund` 且任务已获审批 |
| 依赖工具 | 初赛 `HttpBusinessActionAdapter` → `enterprise_simulator`；`JsonlBusinessActionAdapter` 保留为离线测试后端；复赛替换为订单/支付系统 Adapter |
| 失败处理 | 执行失败不发送成功通知；核验失败触发补偿回滚，回滚失败升级人工 |
| 验证方式 | 操作 ID、请求指纹、状态和回滚关联关系校验 |
| 安全边界 | 必须校验绑定 `task_id + profile_id + action_type + order_id + amount + currency + reason + idempotency_key` 的短时效 ApprovalToken；未审批、令牌缺失/过期/范围不匹配、参数缺失、幂等冲突或币种不支持均拒绝执行 |
| 复用价值 | ERP、订单、支付系统只需替换适配器，Agent/Skill/Trace 接口不变 |
| 与多 Agent 关系 | ActVerify 在 DutyManager 放行后调用，结果决定通知、回滚和案例终态 |

---

## MCP / 等价工具契约

### channel.send_message

```json
{
  "name": "channel.send_message",
  "input": {
    "channel": "douyin|qywx",
    "profile_id": "string",
    "session_ref": "string",
    "content": "string",
    "idempotency_key": "string"
  },
  "output": {
    "send_id": "string",
    "status": "ok|failed",
    "receipt_raw": "object",
    "ts": "iso8601"
  },
  "auth": "profile scoped token",
  "retry": "max 2 with backoff",
  "audit": "task_id + profile_id + tool_name"
}
```

### channel.query_session / channel.fetch_history

- 鉴权：profile 级 token
- 幂等：读操作天然幂等
- 降级：渠道不可用时返回 `degraded=true`，触发人工升级
- 迁移成本：仅需 MCP Server 协议适配，Skill 与 Agent 编排无需重写

## Skill 版本与发布策略

| 策略 | 说明 |
|---|---|
| 版本号 | SemVer，破坏性变更升 major |
| 发布 | Skill 清单 + Schema 随仓库 tag 发布 |
| 回滚 | 按版本号回退 Skill 实现 |
| 质量评估 | 基于 verify 通过率、人工升级率离线评估 |
