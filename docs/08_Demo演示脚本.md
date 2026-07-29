# AgentDesk 初赛 Demo 演示脚本

> 建议录制 3~5 分钟视频，作为官网「Demo 链接」提交。  
> **录制来源：AgentDesk 抖音 Channel Runtime + 参考编排器 + Skill CLI，不是第三方桌面壳。**

## 1. 演示目标

向评委证明四件事：

1. **独立 Runtime**：抖音 IPC 可单独启动，不依赖任何第三方桌面产品。
2. **多 Agent 可运行**：参考编排器输出 `trace.jsonl`，跨 Agent 调度有据可查。
3. **Skill 可调用**：注册表 + IntentTriage/ReplyPlan CLI 可演示。
4. **工程可信**：profile 隔离、审批闸门、mock/live 可切换。

## 2. 环境说明

| 项 | 说明 |
|---|---|
| 主系统 | AgentDesk 抖音 Runtime + `orchestrator/` + `skills/` |
| API 入口 | `http://127.0.0.1:8765/console` |
| 比赛仓库 | https://github.com/icebreak119/AgentDesk |
| 工作目录 | 克隆后 `cd AgentDesk`（仓库根目录） |

## 3. 录屏前准备

### 3.1 编排器 + Skill（离线可跑，无需 8765）

```powershell
cd AgentDesk
python -m orchestrator.demo.script_a_consult
python -m orchestrator.demo.script_b_approval
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

live 发送：

```powershell
cd AgentDesk
python -m orchestrator.demo.script_a_consult --live
```

## 4. 录屏结构（约 4 分钟）

| 时段 | 内容 | 画面 |
|---|---|---|
| 0:00~0:20 | 背景：私域客服痛点 | PPT 或 `06_架构图.png` |
| 0:20~0:40 | AgentTeams 五 Agent + orchestrator 映射 | 架构图 |
| 0:40~1:20 | **剧本 A trace**：编排器一键运行 | 终端 `script_a_consult` + `trace.jsonl` |
| 1:20~1:50 | **Skill CLI**：IntentTriage / ReplyPlan | `run_skill.py` 输出 |
| 1:50~2:30 | **8765 控制台**：账号 / 发送 / 会话 | `/console` 截图同类画面 |
| 2:30~3:10 | **剧本 B 审批**：挂起 → 批准 | `script_b_approval` trace 含 `approval_required` |
| 3:10~3:40 | 契约 pytest + GitHub 仓库 | `pytest docs/contracts/tests -q` |
| 3:40~4:00 | 总结：参考编排 → 复赛官方 AgentTeams | PPT |

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
4. ActVerify → ChannelSend（mock）→ OutcomeVerify（pass）
5. `trace.jsonl` 含 SessionTL 状态跳转

## 6. 剧本 B：高风险审批

**输入消息：**「我要退款，改一下账户」

**终端命令：**

```powershell
python -m orchestrator.demo.script_b_approval
```

**讲解要点：**

1. IntentTriage → `need_approval=true`
2. DutyManager 挂起 `suspended`，未批准不发送
3. `approval_granted` 后才 ChannelSend
4. 拒绝路径：`python -m orchestrator.demo.script_b_approval --reject`

## 7. Skill CLI 演示段（约 30 秒）

```powershell
python skills/run_skill.py intent_triage -i skills/intent_triage/v0.1/examples/refund.json --pretty
python skills/run_skill.py reply_plan -i skills/reply_plan/v0.1/examples/high_risk.json --pretty
```

强调：`registry.yaml` 五 Skill 索引；高风险 ReplyPlan **不附带 approval_token**。

## 8. 剧本 C：核验失败（Runtime 加分项）

**画面建议：** `07_系统截图/05_核验失败任务.png` + 口播 OutcomeVerify 设计。

短文本「1」「在」须 DOM 二次校验，`verify_failed` 不入库为成功。

## 9. 演示总结话术

> AgentDesk 是基于 AgentTeams 的多 Agent 私域客服自治基础设施。  
> 初赛我们交付了可独立运行的抖音 Channel Runtime，以及 **AgentTeams 能力映射的参考编排器**——剧本 A/B 可输出 trace.jsonl，Skill 可注册调用，MCP 契约有 pytest 校验。  
> 复赛将编排层迁移至 AgentTeams 官方运行时，并建设 Trace 工作台与 Task 持久化。

## 10. 视频文件建议

- 文件名：`AgentDesk_初赛Demo_20260729.mp4`
- 上传：B 站 / 飞书 / 阿里云盘公开链接
- 官网 Demo 链接：填**公网视频 URL**
