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
| OutcomeVerify | v0.1 | 结果核验与证据 | ActVerify | `skills/outcome_verify/v0.1/` + runtime | 生产级可审计 |

---

## 1. SessionNormalizeSkill

| 项 | 内容 |
|---|---|
| 用途 | 将抖音/企微原始入站事件转为统一 SessionEvent |
| 输入 Schema | `{ channel: string, raw_event: object, profile_id: string }` |
| 输出 Schema | `{ session_id, customer_ref, content, content_type, ts, dedupe_key }` |
| 调用条件 | 任意渠道入站消息到达 |
| 依赖工具 | 抖音 Runtime / 企微 Hook 入站适配器 |
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
| 依赖工具 | LLM；规则引擎（敏感词/高风险模板） |
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
| 依赖工具 | LLM；知识检索（FAQ/历史案例） |
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
| 依赖工具 | 抖音 send_im_message；企微 send_text_message |
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

## Skill 版本与发布策略（复赛）

| 策略 | 说明 |
|---|---|
| 版本号 | SemVer，破坏性变更升 major |
| 发布 | Skill 清单 + Schema 随仓库 tag 发布 |
| 回滚 | 按版本号回退 Skill 实现 |
| 质量评估 | 基于 verify 通过率、人工升级率离线评估 |
