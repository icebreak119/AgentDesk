# AgentDesk 多 Agent 闭环说明

## 1. 闭环总览

AgentDesk 围绕「私域客服会话处置」完成 9 步企业级闭环：

1. 任务输入
2. 任务拆解
3. 上下文传递
4. 工具调用
5. 结果验证
6. 客户确认
7. 执行证据沉淀
8. 审批与回滚
9. 经验沉淀

## 2. 逐步映射

### 2.1 任务输入

- 来源：抖音私信入站与企业微信本地 Webhook；二者都转换为统一 `SessionEvent`，生产企微验签与队列属于复赛
- 处理：ChannelIngress 接收并调用 SessionNormalizeSkill
- 产出：SessionEvent + task_id

### 2.2 任务拆解

- DutyManager 创建 Task，拆为：
  - ingress（归一与去重）
  - triage（分级）
  - plan/act（方案与执行）
  - verify（核验）
  - confirm（客户确认）
  - digest（脱敏复盘与标签归档）
- SessionTL 维护状态机驱动执行

### 2.3 上下文传递

TaskContext 示例：

```json
{
  "task_id": "task_20260726_001",
  "profile_id": "dy_account_01",
  "session_id": "sess_abc",
  "channel": "douyin",
  "triage_result": {"intent": "consult", "priority": "low", "need_approval": false},
  "reply_draft": {"draft_text": "您好，价格请参考..."},
  "send_receipt": {"status": "ok", "send_id": "msg_123"},
  "verify_result": {"pass": true, "evidence_ref": "log://verify/001"},
  "dedupe_result": {"accepted": true, "reason": "first_seen"},
  "customer_confirm_result": {"confirmation_state": "confirmed"},
  "case_digest": {"case_id": "case_task_20260726_001", "privacy": {"contains_customer_content": false}}
}
```

### 2.4 工具调用

- Agent 不直连业务细节，统一经 Skill 调用
- Skill 经等价 MCP 契约访问渠道 Runtime
- 知识检索经 ReplyPlanSkill 注入上下文

### 2.5 结果验证

- ActVerify 调用 OutcomeVerifySkill
- 校验维度：回执状态、实际消息内容、短文本 DOM 一致性
- 失败：不入库为成功，记录 verify_failed 证据

### 2.6 客户确认

- ActVerify 在 OutcomeVerify 通过后调用 CustomerConfirmSkill。
- 正向反馈进入 `confirmed → done`；负向反馈进入 `needs_follow_up → escalated`；无明确反馈保持 `awaiting_customer_confirmation`。
- 确认结论与证据引用进入 TaskContext，但案例档案只保留匿名摘要。

### 2.7 执行证据沉淀

| 证据类型 | 内容 | 用途 |
|---|---|---|
| Log | profile_id + task_id + 分支关键字 | 故障定位 |
| Trace | agent → skill → tool 链路 | 协同审计 |
| Receipt | 渠道发送回执 | 执行证明 |
| Verify | expected/actual 对比 | 结果可信 |
| Confirm | 客户确认结论与 evidence_ref | 闭环/升级依据 |
| CaseDigest | 匿名案例 ID、标签和隐私校验 | 经验复用与审计 |

### 2.8 审批与回滚

- 触发：退款/改账户/敏感操作 → need_approval=true
- 流程：DutyManager 挂起 → 人工确认 → 发放 ApprovalToken → 执行
- 拒绝：任务终止，写 audit_log
- 退款动作：审批通过后调用 BusinessAction，先核验企业动作，再发送客户通知
- 回滚：退款核验失败写入补偿操作，并以 `rollback_of` 关联原 operation_id；回滚失败升级人工

### 2.9 经验沉淀

- CaseLearning 仅在 `done/failed/escalated` 终态后调用 CaseDigestSkill。
- 案例记录为匿名 JSONL，包含意图、风险、处置、核验、确认和可复用标签；不含客户身份、原文或凭据。
- 后续同意图/风险任务按标签检索 `case://` 片段并注入 ReplyPlan。当前是结构化标签检索，不将其表述为完整 RAG。

## 3. 演示剧本

### 剧本 A：主路径（抖音）

客户：「在吗，想了解价格」

→ Normalize → Triage(consult/low) → ReplyPlan → Send → Verify(pass) → 证据入库

### 剧本 B：高风险路径

客户：「我要退款，改一下账户」

→ Triage(refund/high/need_approval) → DutyManager 挂起
→ 人工审批通过 → BusinessAction 查询订单 → 退款申请 → 执行回执 → 动作核验
→ 核验通过 → ChannelSend 客户通知 → OutcomeVerify → CustomerConfirm → CaseDigest
或 动作核验失败 → 补偿回滚 → failed/escalated，不发送成功通知
或 审批拒绝 → 审计结束，不执行退款、不发送

> 初赛剧本 B 的实时路径使用可替换的 HTTP 企业业务模拟器，暴露订单查询、退款申请、执行回执、操作查询和回滚接口；JSONL 适配器保留为离线测试后端，不将模拟器表述为真实支付/订单系统。

### 剧本 C：跨渠道去重、客户确认与案例复用（离线契约演示）

1. 抖音咨询完成，客户确认，写入匿名 CaseDigest。
2. 同一客户在 5 分钟窗口内以相同内容经企微契约入站，被标记 `deduplicated`，不会产生第二次发送。
3. 同一客户后续的企微咨询检索到 `case://case_task_c_001`，完成核验、客户确认和再次沉淀。

企微通过 `runtime/wecom` 的本地 `POST /webhooks/wecom/messages` 进入统一 SessionEvent；它是可运行的本地适配器，不宣称已接入企业微信生产网关。

## 4. 上下文能力（满足赛道 4 选 2）

| 能力 | 状态 | 说明 |
|---|---|---|
| 共享状态管理 | ✅ 参考实现可运行 | `TaskContext` + 会话状态机；状态转移来源校验；跨 Agent 使用脱敏 `to_wire_dict()` |
| 轨迹可观测 | ✅ 参考实现可运行 | `trace.jsonl` 按 `task_id / agent / skill / status` 串联 |
| 结构化案例归档与标签检索 | ✅ 参考实现可运行 | 匿名 JSONL CaseDigest + `case://` 引用；剧本 C 覆盖 |
| 知识库 RAG | 🔄 复赛 | FAQ + 历史案例检索 |
| Agent 记忆存储 | 🔄 复赛 | 客户会话短期记忆 |

## 5. 评审维度对齐

| 评审维度（25%） | AgentDesk 对应材料 |
|---|---|
| 场景价值与可复制性 | 私域客服通用痛点 + 抖音真实 Runtime + 企微统一契约 |
| 多 Agent 协同与闭环 | 6 Agent + 9 步闭环 + 去重、客户确认、审批回滚 |
| Skill 工程与生态复用 | 8 Skill + Schema + 版本策略 |
| 工程落地与安全审计 | profile 隔离、核验、证据、MCP 契约 |
| 开源贡献 | GitHub 仓库 + 可复用模板 |
