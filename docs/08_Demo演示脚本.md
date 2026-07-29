# AgentDesk 初赛 Demo 演示脚本

> 建议录制 3~5 分钟视频，作为官网「Demo 链接」提交。  
> **录制来源：AgentDesk 抖音 Channel Runtime（独立模块），不是第三方桌面壳，也不是 5173 小红书项目。**

## 1. 演示目标

向评委证明三件事：

1. **独立 Runtime**：抖音 IPC 可单独启动，不依赖任何第三方「云朵 / 私域」桌面产品。
2. **闭环设计完整**：入站 → 分级 → 执行 → 核验 → 证据（编排层复赛落地，Runtime 已可验证）。
3. **工程可信**：profile 隔离、发送回执、失败分支可定位。

## 2. 环境说明

| 项 | 说明 |
|---|---|
| 主系统 | AgentDesk 抖音 Runtime（`douyin_reverse_ipc` + `douyin_all_user`） |
| API 入口 | `http://127.0.0.1:8765/console`（中文操作台，MCP 等价工具层） |
| 比赛仓库 | https://github.com/icebreak119/AgentDesk |
| 主渠道 | 抖音私信 |
| 扩展渠道 | 企业微信（复赛） |

**不要使用的 Demo 来源：**

- `http://127.0.0.1:5173`（`xhs-ai-kefu` 小红书，与本次赛道无关）
- 第三方「云朵 / 私域」整壳桌面 UI（非 AgentDesk 交付物）
- 未标注「设计稿 / 复赛」的 Trace 全链路页面

## 3. 录屏前启动（终端可见）

```powershell
cd C:\Users\31368\Desktop\siyu\siyu
python -m channels.douyin_reverse_ipc.http_server `
  --db-path channels\douyin_all_user\reverse_runtime\_douyin_im_accounts.db `
  --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/console` 确认服务在线。

## 4. 录屏结构（约 3 分 30 秒）

| 时段 | 内容 | 画面 |
|---|---|---|
| 0:00~0:20 | 背景：私域客服痛点 | PPT 或 `06_架构图.png` |
| 0:20~0:40 | AgentTeams 五 Agent 分工 | 架构图 |
| 0:40~1:10 | **独立 Runtime**：8765 在线接口文档 | 接口文档页面 |
| 1:10~1:50 | 剧本 A：发送消息 + 回执 | 调用 send API 或日志 send ok |
| 1:50~2:20 | 剧本 B：高风险 / 人工介入设计 | 日志 pending_reply / AI 管线分流 |
| 2:20~3:00 | 剧本 C：核验失败证据 | 日志 verify_failed / IPC 失败 |
| 3:00~3:30 | 总结：复赛 AgentTeams 代码包 | PPT P11 |

## 5. 剧本 A：普通咨询（主路径）

**输入消息：**「在吗，想了解价格」

**讲解要点：**

1. ChannelIngress 归一入站（SessionNormalize）。
2. TriageGuard → consult / low / 无需审批（编排层设计，复赛联调）。
3. ActVerify 经 ChannelSend 调用 **8765 Runtime** 发送。
4. OutcomeVerify 校验回执与内容一致。
5. 日志含 `profile_id` 可追踪。

**画面建议：** 接口文档「发送文本私信」+ 终端日志发送成功行。

## 6. 剧本 B：高风险审批

**输入消息：**「我要退款，帮我改一下账户」

**讲解要点：**

1. TriageGuard 标记 `need_approval=true`。
2. DutyManager 挂起，未审批不调用 ChannelSend。
3. 审批通过后才走 Runtime 发送；拒绝只写审计。

**画面建议：** 日志中「主进程 AI 管线 / pending_reply」+ PPT 剧本 B。

## 7. 剧本 C：核验失败（加分项）

**输入消息：** 短文本「1」「在」

**讲解要点：**

1. 不只看 preview，OutcomeVerify 做 DOM/回执二次校验。
2. `verify_failed` / `result=failed` 不入库为成功。

**画面建议：** `07_系统截图/05_核验失败任务.png` 同类日志（已打码）。

## 8. 演示总结话术（可直接念）

> AgentDesk 是基于 AgentTeams 的多 Agent 私域客服自治基础设施。  
> 初赛我们已把抖音渠道抽成**可独立运行的 Channel Runtime**（IPC HTTP + 托管入站），并给出完整的 Agent、Skill 与闭环设计。  
> 复赛将把编排层与 Trace 工作台迁入 AgentDesk 主仓，完成端到端联调。

## 9. 视频文件建议

- 文件名：`AgentDesk_初赛Demo_20260729.mp4`
- 上传：B 站 / 飞书 / 阿里云盘公开链接
- 官网 Demo 链接：填**公网视频 URL**（可口播 localhost，但表单勿只填 localhost）
