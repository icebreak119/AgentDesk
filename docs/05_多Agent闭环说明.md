# AgentDesk 多 Agent 闭环说明

## 1. 闭环总览

AgentDesk 围绕「私域客服会话处置」完成 8 步企业级闭环：

1. 任务输入
2. 任务拆解
3. 上下文传递
4. 工具调用
5. 结果验证
6. 执行证据沉淀
7. 审批与回滚
8. 经验沉淀

## 2. 逐步映射

### 2.1 任务输入

- 来源：抖音私信入站、企微文本消息
- 处理：ChannelIngress 接收并调用 SessionNormalizeSkill
- 产出：SessionEvent + task_id

### 2.2 任务拆解

- DutyManager 创建 Task，拆为：
  - ingress（归一）
  - triage（分级）
  - plan/act（方案与执行）
  - verify（核验）
  - digest（复盘，可选）
- SessionTL 维护状态机驱动执行

### 2.3 上下文传递

- TaskContext 字段：
  - task_id, profile_id, session_id, channel
  - triage_result, reply_draft, send_receipt, verify_result
- 传递方式：SessionTL 在各 Worker 间组装共享上下文

### 2.4 工具调用

- Agent 不直连业务细节，统一经 Skill 调用
- Skill 经等价 MCP 契约访问渠道 Runtime
- 知识检索经 ReplyPlanSkill 注入上下文

### 2.5 结果验证

- ActVerify 调用 OutcomeVerifySkill
- 校验维度：回执状态、实际消息内容、短文本 DOM 一致性
- 失败：不入库为成功，记录 verify_failed 证据

### 2.6 执行证据沉淀

- 日志：profile_id + task_id + 分支关键字
- 证据：send_receipt、verify screenshot/log、审批记录
- 可观测：Trace 串联 agent → skill → tool

### 2.7 审批与回滚

- 触发：退款/改账户/敏感操作 → need_approval=true
- 流程：DutyManager 挂起 → 人工确认 → 发放 ApprovalToken → 执行
- 拒绝：任务终止，写 audit_log
- 回滚：未执行动作直接取消；已发送则标记异常并人工介入

### 2.8 经验沉淀

- 成功案例：可入 FAQ 候选
- 失败案例：记录 triage/verify 失败原因，供复盘
- 复赛目标：CaseDigestSkill 自动沉淀

## 3. 演示剧本

### 剧本 A：主路径（抖音）

客户：「在吗，想了解价格」

→ Normalize → Triage(consult/low) → ReplyPlan → Send → Verify(pass) → 证据入库

### 剧本 B：高风险路径

客户：「我要退款，改一下账户」

→ Triage(refund/high/need_approval) → DutyManager 挂起
→ 人工审批通过 → Send → Verify
或 审批拒绝 → 审计结束，不发送

### 剧本 C：扩展证明（企微）

同一条消息经企微入站 → 同一 SessionNormalize / Triage / Verify 链路
→ 证明渠道可扩展，Agent 编排不变

## 4. 上下文能力（满足赛道 4 选 2）

已实现/设计项：

1. **共享状态管理**：TaskContext + 会话状态机
2. **轨迹可观测**：Trace/Log 按 task_id 串联
3. （复赛）**知识库 RAG**：FAQ + 历史案例检索
4. （复赛）**Agent 记忆存储**：客户会话短期记忆
