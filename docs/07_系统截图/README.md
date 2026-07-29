# AgentDesk 系统截图说明

## 用途

为初赛材料提供**独立 Runtime 工程证据**，证明抖音渠道模块可脱离第三方桌面壳运行。

## 采集来源（必须）

1. **AgentDesk 抖音渠道 Runtime 中文控制台**：`http://127.0.0.1:8765/console`（Playwright 真实截图）
2. **架构图**：`06_架构图.png` 复制为 03
3. **兜底**：若 8765 未启动，02/04/05 回退为 Runtime 日志排版图

## 禁止来源

- 第三方「云朵 / 私域」整壳桌面 UI
- `http://127.0.0.1:5173`（xhs-ai-kefu / 小红书）

## 截图清单

| 文件名 | 内容 | 状态 |
|---|---|---|
| `01_账号接入页.png` | 控制台「账号托管」面板（真实 UI） | 脚本生成 |
| `02_会话工作台.png` | 控制台整页工作台（账号+发送+会话，真实 UI） | 脚本生成 |
| `03_架构图或日志总览.png` | AgentTeams 架构图 | 脚本生成 |
| `04_高风险审批任务.png` | 发送表单 + 已选会话（真实 UI） | 脚本生成 |
| `05_核验失败任务.png` | 调用结果区（浅色 UI + 接口回显） | 脚本生成 |

## 生成命令

```powershell
cd C:\Users\31368\Desktop\siyu\siyu
python docs/goai/build_submission_screenshots.py
```

脚本会：

1. 若 `8765` 已启动，用 Playwright 截取 `/console` 各面板（01/02/04/05 为真实 UI）
2. 复制 `06_架构图.png` 为 03
3. 若控制台不可达，01/02/04/05 回退为日志打码排版图

## 打码要求

- 客户昵称、手机号、完整 conversation_id 打码
- 保留 `profile_id` 前缀、模块名、`verify_failed` 等工程关键字
