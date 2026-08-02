# AgentDesk 初赛 Demo 演示脚本

> 建议录制 3~5 分钟视频，作为官网「Demo 链接」提交。  
> **录制来源：AgentDesk 抖音 Channel Runtime + 参考编排器 + Skill CLI，不是第三方桌面壳。**

## 1. 演示目标

向评委证明四件事：

1. **独立 Runtime**：抖音 IPC 可单独启动，不依赖任何第三方桌面产品。
2. **多 Agent 可运行**：参考编排器输出 `trace.jsonl`，跨 Agent 调度、去重、确认和案例沉淀有据可查。
3. **Skill 可调用**：8 个 Skill 均有 Schema，其中 7 个可经 CLI 演示。
4. **工程可信**：profile 隔离、审批闸门、独立 HTTP 企业动作、企微 Webhook、匿名 CaseDigest。

## 2. 环境说明

| 项 | 说明 |
|---|---|
| 主系统 | AgentDesk 抖音 Runtime + `orchestrator/` + `skills/` |
| API 入口 | `http://127.0.0.1:8765/console` |
| 企业动作服务 | `http://127.0.0.1:8770`，独立 HTTP 订单/退款模拟器 |
| 抖音本地入站接收 | `http://127.0.0.1:8780/webhooks/douyin/messages`（可作为 `DY_IPC_WEBHOOK_URL`） |
| 企微 Webhook | `http://127.0.0.1:8771/webhooks/wecom/messages` |
| 实时演示页 | `http://127.0.0.1:8780/` |
| 比赛仓库 | https://github.com/icebreak119/AgentDesk |
| 工作目录 | 克隆后 `cd AgentDesk`（仓库根目录） |

## 3. 录屏前准备

### 3.1 编排器 + Skill（离线可跑，无需 8765）

```powershell
cd AgentDesk
python -m orchestrator.demo.script_a_consult
python -m orchestrator.demo.script_b_approval
python -m orchestrator.demo.script_c_multichannel_case
type orchestrator\output\trace.jsonl
python skills/run_skill.py intent_triage -i skills/intent_triage/v0.1/examples/consult.json --pretty
```

### 3.2 Runtime（可选 live 联调）

```powershell
cd runtime/douyin
pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
python -m channels.douyin_reverse_ipc.http_server `
  --db-path channels\douyin_all_user\reverse_runtime\_douyin_im_accounts.db `
  --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/console`。

### 3.3 三项新增服务

分别启动：

```powershell
python -m enterprise_simulator.server --port 8770
python -m runtime.wecom.server --port 8771
python -m demo_runtime.server --repo-root $PWD --port 8780
```

浏览器打开 `http://127.0.0.1:8780/`，点击“启动实时演示”。页面会通过抖音本地入站接收和企微 Webhook 产生两条 HTTP 入站，逐条展示 Agent 分工、审批、订单查询、退款申请、执行回执、结果核验、客户通知和跨渠道去重。

live 发送：

```powershell
cd AgentDesk
python -m orchestrator.demo.script_a_consult --live
```

## 4. 录屏结构（约 4 分钟）

| 时段 | 内容 | 画面 |
|---|---|---|
| 0:00~0:20 | 背景：私域客服痛点 | PPT 或 `06_架构图.png` |
| 0:20~0:40 | AgentTeams 六 Agent + orchestrator 映射 | 架构图 |
| 0:40~1:15 | **剧本 A trace**：普通咨询、核验、客户确认和 CaseDigest | 终端 `script_a_consult` + `trace.jsonl` |
| 1:15~1:55 | **剧本 B 高风险动作**：挂起 → 批准 → HTTP 订单/退款 API → 核验/通知 | 实时演示页 / `script_b_approval` Trace |
| 1:45~2:20 | **剧本 C 去重/复用**：抖音 → 企微统一契约 | `script_c_multichannel_case` + `trace_c_multichannel.jsonl` |
| 2:20~2:45 | **Skill CLI**：SessionNormalize / CustomerConfirm / CaseDigest | `run_skill.py` 输出 |
| 2:45~3:20 | **服务证据**：企业 HTTP API / 企微 Webhook / Trace | `8770`、`8771`、`8780` |
| 3:20~3:45 | 全量 pytest + GitHub 仓库 | `python -m pytest -q` |
| 3:45~4:00 | 总结：参考编排 → 复赛官方 AgentTeams | PPT |

## 5. 剧本 A：普通咨询（编排器主路径）

**输入消息：**「在吗，想了解价格」

**终端命令：**

```powershell
python -m orchestrator.demo.script_a_consult
```

**讲解要点：**

1. ChannelIngress → SessionNormalize
2. TriageGuard → IntentTriage：`consult / low / need_approval=false`
3. ReplyPlan 生成 `draft_text`
4. ActVerify → ChannelSend（mock）→ OutcomeVerify（pass）→ CustomerConfirm（confirmed）
5. CaseLearning → CaseDigest（匿名归档），`trace.jsonl` 含 SessionTL 状态跳转

## 6. 剧本 B：高风险退款动作

**输入消息：**「我要退款，改一下账户」

**终端命令：**

```powershell
python -m orchestrator.demo.script_b_approval
```

**讲解要点：**

1. IntentTriage → `need_approval=true`
2. DutyManager 挂起 `suspended`，未批准不发送
3. `approval_granted` 后调用 BusinessAction HTTP 后端：订单查询 → 退款申请 → 执行回执
4. `business_action_verified` 成功后才 ChannelSend 客户通知；Trace 中标注 `mode=http`
5. 拒绝路径：`python -m orchestrator.demo.script_b_approval --reject`
6. 核验失败回滚路径：`python -m orchestrator.demo.script_b_approval --inject-verify-failure`

## 7. 剧本 C：跨渠道去重、客户确认与案例复用

**终端命令：**

```powershell
python -m orchestrator.demo.script_c_multichannel_case
type orchestrator\output\trace_c_multichannel.jsonl
```

**讲解要点：**

1. 抖音咨询完成并收到脱敏客户确认，CaseDigest 仅归档标签化结果。
2. 同一客户、同一内容在 5 分钟窗口内通过企微统一契约入站，状态为 `deduplicated`，不触发第二次发送。
3. 后续咨询命中 `case://case_task_c_001`，由 ReplyPlan 复用历史摘要后再次确认并归档。
4. 企微通过本地 `POST /webhooks/wecom/messages` 进入统一 `SessionEvent`；生产验签、重试和队列属于复赛。

## 8. Skill CLI 演示段（约 30 秒）

```powershell
python skills/run_skill.py intent_triage -i skills/intent_triage/v0.1/examples/refund.json --pretty
python skills/run_skill.py reply_plan -i skills/reply_plan/v0.1/examples/high_risk.json --pretty
python skills/run_skill.py customer_confirm -i skills/customer_confirm/v0.1/examples/confirmed.json --pretty
python skills/run_skill.py case_digest -i skills/case_digest/v0.1/examples/confirmed_consult.json --pretty
python skills/run_skill.py business_action -i skills/business_action/v0.1/examples/refund.json --pretty
```

强调：`registry.yaml` 八 Skill 索引；高风险 ReplyPlan **不附带 approval_token**；BusinessAction 只在审批后执行；CaseDigest 输出不含客户身份、原文或凭据。

## 9. 执行与核验证据（Runtime 加分项）

**画面建议：** `07_系统截图/05_执行核验Trace.png` + 口播 OutcomeVerify 设计。

低风险剧本的 Trace 明确显示仅一次 `ChannelSend`、一次 `OutcomeVerify`、一次 `CustomerConfirm` 与一次 `CaseDigest`。高风险 Trace 额外显示 HTTP 企业动作的订单查询、退款申请、执行回执、核验和回滚分支；本地服务不冒充真实支付系统。

## 10. 演示总结话术

> AgentDesk 是基于 AgentTeams 的多 Agent 私域客服自治基础设施。  
> 初赛我们交付了可独立运行的抖音 Channel Runtime、企微本地 Webhook、独立 HTTP 企业动作模拟器，以及 **AgentTeams 能力映射的参考编排器**——实时演示验证审批、订单查询、退款执行/核验/回滚、通知和跨渠道去重；8 个 Skill 可注册调用，MCP 契约有 pytest 校验。
> 复赛将编排层迁移至 AgentTeams 官方运行时，并建设 Trace 工作台与 Task 持久化。

## 11. 视频文件建议

- 文件名：`AgentDesk_初赛Demo_20260729.mp4`
- 上传：B 站 / 飞书 / 阿里云盘公开链接
- 本地录制产物：`C:\Users\31368\Videos\AgentDesk_实时闭环演示.mp4`
- 官网 Demo 链接：上传该视频后填写**公网视频 URL**
