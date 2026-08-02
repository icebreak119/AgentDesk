# AgentDesk 系统截图说明

## 用途

为初赛材料提供**独立 Runtime 与参考编排工程证据**。所有截图均使用脱敏演示数据，不包含真实账号、会话、凭据或本机路径。

## 采集来源（必须）

1. **AgentDesk 抖音渠道控制台**：仓内 `console.html` 通过本地 Mock API 渲染，展示真实前端与脱敏演示数据
2. **架构图**：由 `build_arch_diagram.py` 生成当前版本
3. **Trace 证据**：由参考编排器剧本 A/B/C 实际运行后渲染，不包含真实渠道数据；剧本 C 的企微仅为离线统一契约

## 禁止来源

- 真实 Cookie、Token、profile、账号编号或客户会话
- 任何其他项目或历史桌面 UI
- 其他本地服务或无关渠道的截图

## 截图清单

| 文件名 | 内容 | 状态 |
|---|---|---|
| `01_托管账号登录控制面.png` | Web 发起 / 查看登录任务的控制面 | `console.html` + 脱敏 Mock API |
| `02_抖音渠道控制台_脱敏演示.png` | 账号、发送、登录、会话的完整控制台 | `console.html` + 脱敏 Mock API |
| `03_多Agent架构图.png` | 当前多 Agent / Skill / 工具契约架构 | 架构图生成脚本 |
| `04_审批闭环Trace.png` | 高风险任务的挂起、批准、执行和核验 | `script_b_approval` |
| `05_执行核验Trace.png` | 低风险任务的一次发送、结果核验、客户确认和匿名归档 | `script_a_consult` |
| `06_跨渠道去重与案例复用Trace.png` | 跨渠道重复拦截、客户确认和 `case://` 标签复用 | `script_c_multichannel_case`（企微离线契约） |

## 生成命令

```powershell
# 在 AgentDesk 仓库根目录执行；首次需要 playwright install chromium
python docs/build_submission_screenshots.py
```

脚本会：

1. 以仓内 `console.html` 和本地 Mock API 生成 01/02，账号和会话均为脱敏演示数据
2. 生成当前 `06_架构图.png` 并复制为 03
3. 实际运行剧本 B / A / C，分别生成 04 的审批 Trace、05 的执行核验 Trace 和 06 的跨渠道去重/案例复用 Trace

## 打码要求

- 禁止写入真实账号、客户昵称、手机号、Cookie、Token、profile 或本机绝对路径
- 演示标识统一使用 `demo-*`、`customer-demo-*` 与“已脱敏”字样
