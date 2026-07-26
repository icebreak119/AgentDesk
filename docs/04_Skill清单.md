# AgentDesk 核心 Skill 清单

## Skill 总览

| Skill | 用途 | 调用 Agent | 复用价值 |
|---|---|---|---|
| SessionNormalize | 多渠道会话归一 | ChannelIngress | 换渠道只换适配器 |
| IntentTriage | 意图识别与分级 | TriageGuard | 多行业可复用规则+模型 |
| ReplyPlan | 回复/处置方案生成 | TriageGuard | 知识增强、风险标签 |
| ChannelSend | 渠道消息发送 | ActVerify | 幂等、防串号 |
| OutcomeVerify | 结果核验与证据 | ActVerify | 生产级可审计 |

---

## 1. SessionNormalizeSkill

| 项 | 内容 |
|---|---|
| 用途 | 将抖音/企微原始入站事件转为统一 SessionEvent |
| 输入 | `{ channel, raw_event, profile_id }` |
| 输出 | `{ session_id, customer_ref, content, content_type, ts, dedupe_key }` |
| 调用条件 | 任意渠道入站消息到达 |
| 依赖工具 | 抖音 Runtime / 企微 Hook 入站适配器 |
| 失败处理 | 记录日志并丢弃；不进入后续链路 |
| 安全边界 | 不修改全局入口 URL；cid 仅作会话级标识 |
| 与多 Agent 关系 | ChannelIngress 专属调用，结果交 SessionTL |

## 2. IntentTriageSkill

| 项 | 内容 |
|---|---|
| 用途 | 识别意图、评估优先级与风险、判定是否需审批 |
| 输入 | `SessionEvent + history[]` |
| 输出 | `{ intent, priority, risk_tag, need_approval, confidence }` |
| 调用条件 | SessionEvent 归一成功后 |
| 依赖工具 | LLM；规则引擎（敏感词/高风险模板） |
| 失败处理 | confidence 低则默认升级人工 |
| 安全边界 | 不得触发任何外部写操作 |
| 与多 Agent 关系 | TriageGuard 调用，结论供 DutyManager 决策 |

## 3. ReplyPlanSkill

| 项 | 内容 |
|---|---|
| 用途 | 基于会话与知识生成回复/处置草案 |
| 输入 | `SessionEvent + TriageResult + knowledge_hits[]` |
| 输出 | `{ draft_text, action_type, risk_tag, citations[] }` |
| 调用条件 | triage 完成且非立即升级 |
| 依赖工具 | LLM；知识检索（FAQ/历史案例） |
| 失败处理 | 无有效草案 → 转人工 |
| 安全边界 | 高风险仅出方案，不附带执行令牌 |
| 与多 Agent 关系 | TriageGuard 生成草案，ActVerify 消费 |

## 4. ChannelSendSkill

| 项 | 内容 |
|---|---|
| 用途 | 向指定渠道发送消息，保证幂等与账号隔离 |
| 输入 | `{ channel, profile_id, session_ref, content, idempotency_key }` |
| 输出 | `{ send_id, status, receipt_raw, ts }` |
| 调用条件 | 低风险自动路径，或高风险已获 ApprovalToken |
| 依赖工具 | 抖音 send_im_message；企微 send_text_message |
| 失败处理 | 有限重试；ticket 失效则清缓存重 resolve |
| 安全边界 | 必须带 profile_id；禁止跨账号发送 |
| 与多 Agent 关系 | ActVerify 专属调用 |

## 5. OutcomeVerifySkill

| 项 | 内容 |
|---|---|
| 用途 | 校验发送结果与客户侧实际展示是否一致 |
| 输入 | `{ expected_content, send_receipt, session_ref }` |
| 输出 | `{ pass, actual_content, evidence_type, evidence_ref }` |
| 调用条件 | ChannelSend 完成后 |
| 依赖工具 | 回执解析；DOM 二次读取（短文本场景） |
| 失败处理 | verify 失败 → 标记 failed，不记为成功回复 |
| 安全边界 | 候选必须唯一；expected/actual 不一致则失败 |
| 与多 Agent 关系 | ActVerify 调用，结果回传 DutyManager/SessionTL |

---

## MCP / 等价工具契约（初赛设计）

| 工具名 | 入口 | 主要参数 | 返回 |
|---|---|---|---|
| channel.send_message | 渠道 Runtime API | channel, profile_id, target, content | receipt |
| channel.query_session | 渠道 Runtime API | profile_id, session_id | messages[] |
| channel.fetch_history | 渠道 Runtime API | profile_id, session_ref | history[] |

迁移到 MCP：仅需协议适配层，Skill 与 Agent 编排无需重写。
