# AgentDesk 系统截图说明

## 用途

为初赛材料提供**独立 Runtime 工程证据**，证明抖音渠道模块可脱离第三方桌面壳运行。

## 采集来源（必须）

1. **AgentDesk 抖音 IPC Runtime**：`http://127.0.0.1:8765/docs`（Swagger）
2. **Runtime 日志**：`logs/yunduo.log` 或 `logs/agentdesk.log`（打码后排版）

## 禁止来源

- 第三方「云朵 / 私域」整壳桌面 UI
- `http://127.0.0.1:5173`（xhs-ai-kefu / 小红书）

## 截图清单

| 文件名 | 内容 | 状态 |
|---|---|---|
| `01_账号接入页.png` | Runtime API `/docs` 或托管启动日志 | 脚本生成 |
| `02_会话工作台.png` | 入站 WebSocket / profile 隔离日志 | 脚本生成 |
| `03_架构图或日志总览.png` | AgentTeams 架构图 | 脚本生成 |
| `04_高风险审批任务.png` | pending_reply / AI 管线分流日志 | 脚本生成 |
| `05_核验失败任务.png` | 发送失败 / 核验告警日志 | 脚本生成 |

## 生成命令

```powershell
cd C:\Users\31368\Desktop\siyu\siyu
python docs/goai/build_submission_screenshots.py
```

脚本会：

1. 若 `8765` 已启动，用 Playwright 截取 `/docs` 作为 `01_账号接入页.png`
2. 从 Runtime 日志提取真实行并打码，生成 02/04/05
3. 复制 `06_架构图.png` 为 03

## 打码要求

- 客户昵称、手机号、完整 conversation_id 打码
- 保留 `profile_id` 前缀、模块名、`verify_failed` 等工程关键字
