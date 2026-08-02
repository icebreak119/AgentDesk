# AgentDesk Orchestrator（初赛参考实现）

AgentTeams **Manager → Team Leader → Worker** 分层能力的可执行参考实现，与 `skills/`、`runtime/douyin/` 通过 MCP 等价契约连接。

## AgentTeams 映射

| AgentTeams 角色 | AgentDesk Agent | 模块 |
|---|---|---|
| Manager | DutyManager | `agents/duty_manager.py` |
| Team Leader | SessionTL | `agents/session_tl.py` |
| Worker | ChannelIngress | `agents/workers/channel_ingress.py` |
| Worker | TriageGuard | `agents/workers/triage_guard.py` |
| Worker | ActVerify | `agents/workers/act_verify.py` |

## 状态机

```
pending → triaging → planning → acting → verifying → done
                      ↓
                  suspended → (审批) → acting → ...
```

## 验收命令

在仓库根目录执行：

```powershell
python -m orchestrator.demo.script_a_consult
python -m orchestrator.demo.script_b_approval
type orchestrator\output\trace.jsonl
python -m pytest orchestrator/tests -q
```

### 可选：真实 Runtime 联调

需先启动 `8765` HTTP 服务，再加 `--live`：

```powershell
python -m orchestrator.demo.script_a_consult --live --base-url http://127.0.0.1:8765
```

默认 **mock** 模式离线可跑；trace 中 `mode=mock|live` 标注发送方式。

## Trace 样例

```jsonl
{"task_id":"task_001","agent":"ChannelIngress","skill":"SessionNormalize","status":"ok"}
{"task_id":"task_001","agent":"TriageGuard","skill":"IntentTriage","status":"ok","need_approval":false}
{"task_id":"task_001","agent":"SessionTL","event":"state_transition","from":"triaging","to":"planning"}
{"task_id":"task_001","agent":"ActVerify","skill":"ChannelSend","status":"ok","mode":"mock"}
{"task_id":"task_001","agent":"ActVerify","skill":"OutcomeVerify","status":"ok"}
{"task_id":"task_002","agent":"ActVerify","event":"business_action_verified","status":"ok","operation_id":"op_demo_001","evidence_ref":"action://op_demo_001"}
```

## 答辩口径

- 初赛编排器为 **AgentTeams 能力映射的参考实现**，复赛可替换为官方 orchestrator
- Skill 与 MCP 契约无需重写，仅替换调度层
