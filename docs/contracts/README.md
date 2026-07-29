# AgentDesk MCP 等价契约

本目录定义渠道工具层（`channel.*`）的 MCP 等价契约，与 `runtime/douyin` HTTP 接口一一对应。

## 目录结构

```
contracts/
├── common/
│   ├── errors.json       # 统一错误码表
│   ├── audit.json        # 审计字段规范 + 样例
│   └── idempotency.json  # 写操作幂等键规范
├── channel.send_message.json
├── channel.query_session.json
├── channel.fetch_history.json
├── validate.py           # 可执行校验（幂等键、审计记录）
└── tests/
    └── test_contracts.py
```

## 验证命令

在仓库根目录执行：

```powershell
python -m pytest docs/contracts/tests -q
```

## 与 Runtime 的对应关系

| 契约 | HTTP 实现 |
|---|---|
| `channel.send_message` | `POST /accounts/{code}/send/text` |
| `channel.query_session` | `GET /accounts/{code}/conversations` |
| `channel.fetch_history` | `GET /accounts/{code}/conversations/{cid}/messages` |

幂等键映射：HTTP 请求体 `client_msg_id` ↔ 契约 `idempotency_key`。
