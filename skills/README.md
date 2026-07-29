# AgentDesk Skills

初赛参考实现：5 个 Skill 注册 + IntentTriage / ReplyPlan 可运行 MVP。

## 注册表

见 [`registry.yaml`](registry.yaml)，包含 `name / version / entrypoint / schema / examples`。

| Skill | 可 CLI 运行 | 说明 |
|---|---|---|
| SessionNormalize | 否 | Schema + 样例；实现见 runtime 适配 |
| IntentTriage | ✅ | 规则引擎 MVP |
| ReplyPlan | ✅ | 模板回复 MVP |
| ChannelSend | 否 | 契约见 `docs/contracts/channel.send_message.json` |
| OutcomeVerify | 否 | 实现见 runtime 核验链路 |

## 验收命令

```powershell
cd AgentDesk
python skills/run_skill.py intent_triage -i skills/intent_triage/v0.1/examples/consult.json
python skills/run_skill.py intent_triage -i skills/intent_triage/v0.1/examples/refund.json
python skills/run_skill.py reply_plan -i skills/reply_plan/v0.1/examples/consult.json
python -m pytest skills/tests -q
```

依赖：`pip install pyyaml`（若环境未预装）。
