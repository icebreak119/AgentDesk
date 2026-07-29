# AgentDesk Runtime

抖音 Channel Runtime 由两块模块组成，可独立启动，不依赖第三方桌面壳：

| 模块 | 开发仓路径 | 说明 |
|---|---|---|
| 渠道托管 / 入站 | `channels/douyin_all_user/` | ChannelIngress、profile 隔离 |
| IPC HTTP 服务 | `channels/douyin_reverse_ipc/` | MCP 等价 API，默认 `8765` |

启动与验证见 [docs/10_运行说明.md](docs/10_运行说明.md)。

复赛将迁入本目录 `runtime/douyin/`。
