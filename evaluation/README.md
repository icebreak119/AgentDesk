# AgentDesk 量化评测

在仓库根目录执行：

```powershell
python evaluation/run_evaluation.py
```

评测覆盖：

- 20 条意图、优先级和审批样本
- 抖音/企微统一 `SessionEvent` 的跨渠道去重断言
- 审批成功、审批拒绝、动作核验失败回滚、回滚失败升级人工四条路径
- 成功剧本本地平均耗时和 P95 耗时

输出：

- `tmp/evaluation_result.json`：机器可读结果
- `docs/19_量化评测报告.md`：可提交的脱敏报告

这里的指标用于验证参考实现的行为不变量。当前意图 Skill 是规则化 MVP，延迟仅覆盖本地 JSONL Mock，不代表生产系统指标。
