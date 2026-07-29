# 抖音个人号逆向 IPC

本机 HTTP 服务：按账号启停、发文本/表情/图片、查会话与消息。

- **接口说明（完整路径/参数/错误码）**：同目录 [`接口说明.md`](./接口说明.md)
- **在线调试**：服务启动后打开 `http://127.0.0.1:8765/docs`

运行前提：本目录位于完整仓库的 `channels/douyin_reverse_ipc/`，且同仓库已有  
`channels/douyin_all_user/reverse_runtime/`。

---

## 从零到发出第一条消息

在仓库根目录操作。

### 0. 依赖

```powershell
pip install fastapi uvicorn requests websocket-client playwright protobuf
# 若采集凭证要用浏览器登录：
playwright install chromium
```

（仓库若已有 `.venv` / `requirements.txt`，优先用项目环境。）

### 1. 准备凭证库（二选一）

**A. 已有库**  
确认存在：`channels/douyin_all_user/reverse_runtime/_douyin_im_accounts.db`，且 `im_accounts` 里有启用账号、cookie/keys/web_protect 非空。

查看账号：

```powershell
python -c "import sqlite3; c=sqlite3.connect(r'channels/douyin_all_user/reverse_runtime/_douyin_im_accounts.db');
print([dict(r) for r in c.execute('select account_code,enabled,length(cookies_str) ck,douyin_uid from im_accounts').fetchall()])"
```

**B. 新采集一个账号**

```powershell
cd channels\douyin_all_user\reverse_runtime
python dy_apis/collect_im_account_credentials.py --account-code my_test_acc --db-path .\_douyin_im_accounts.db
cd ..\..\..
```

浏览器打开后登录抖音，等脚本写库成功。记下 `account_code`（上例为 `my_test_acc`）。

### 2. 启动 HTTP 服务

仍在仓库根目录：

```powershell
python -m channels.douyin_reverse_ipc.http_server `
  --db-path channels\douyin_all_user\reverse_runtime\_douyin_im_accounts.db `
  --host 127.0.0.1 `
  --port 8765
```

- 地址：`http://127.0.0.1:8765`
- Swagger：`http://127.0.0.1:8765/docs`
- 仅允许本机绑定

### 3. 发一条文本

另开终端（把 `ACC`、`PEER` 换成真实值；`PEER` 为对方数字 uid）：

```powershell
$base = "http://127.0.0.1:8765"
$ACC = "my_test_acc"
$PEER = "1234567890"

curl "$base/ping"
curl -X POST "$base/accounts/$ACC/start"
curl -X POST "$base/accounts/$ACC/send/text" `
  -H "Content-Type: application/json" `
  -d "{\"text\":\"本地测试\",\"peer_uid\":\"$PEER\"}"
curl "$base/accounts/$ACC/conversations"
curl -X POST "$base/accounts/$ACC/stop"
```

Python：

```python
import requests

BASE = "http://127.0.0.1:8765"
ACC = "my_test_acc"
PEER = "1234567890"

print(requests.get(f"{BASE}/ping").json())
print(requests.post(f"{BASE}/accounts/{ACC}/start").json())
print(
    requests.post(
        f"{BASE}/accounts/{ACC}/send/text",
        json={"text": "你好", "peer_uid": PEER},
    ).json()
)
print(requests.get(f"{BASE}/accounts/{ACC}/conversations").json())
print(requests.post(f"{BASE}/accounts/{ACC}/stop").json())
```

---

## 接口一览

统一响应：

```json
{"ok": true, "data": {}}
{"ok": false, "error": {"code": "peer_required", "message": "..."}}
```

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/ping` | 探活 |
| GET | `/db_path` | 当前 SQLite 路径 |
| GET | `/accounts` | 账号列表 |
| GET | `/accounts/{account_code}/status` | 账号状态 |
| POST | `/accounts/{account_code}/start` | 开始收信（WS） |
| POST | `/accounts/{account_code}/stop` | 停止收信 |
| POST | `/accounts/{account_code}/reload_credentials` | 热加载凭证 |
| POST | `/accounts/stop_all` | 全部停止 |
| POST | `/accounts/{account_code}/send/text` | 发文本 |
| POST | `/accounts/{account_code}/send/emoji` | 发表情 |
| POST | `/accounts/{account_code}/send/image` | 发图片 |
| GET | `/accounts/{account_code}/conversations?limit=50` | 会话列表 |
| GET | `/accounts/{account_code}/conversations/{cid}/messages?after_id=&limit=50` | 消息列表 |

### 发送 body

文本：

```json
{"text": "你好", "peer_uid": "123456789", "conversation_id": "", "client_msg_id": ""}
```

表情：

```json
{"emoji_url": "https://...", "emoji_name": "", "peer_uid": "123", "conversation_id": ""}
```

图片：

```json
{"image_path": "D:\\a.png", "peer_uid": "123", "conversation_id": ""}
```

`conversation_id` 与 `peer_uid` 至少一个；有 cid 优先。

---

## 错误码

`account_required` `account_not_found` `not_running` `auth_invalid` `peer_required`  
`text_empty` `emoji_invalid` `image_invalid` `send_unconfirmed` `account_mismatch`  
`invalid_request` `method_not_found` `internal`

---

## 注意

- 默认关闭自动回复
- 同一 `account_code` 不要与现网托管同时开，避免双收
- 路径参数里的 `{account_code}` 必须带上，禁止跨号读写

---

## 附录：stdio Client（一般不需要）

```python
from channels.douyin_reverse_ipc.client import DouyinReverseClient

c = DouyinReverseClient(db_path=r"channels\douyin_all_user\reverse_runtime\_douyin_im_accounts.db")
c.start_account("my_test_acc")
c.send_text("my_test_acc", text="你好", peer_uid="123")
c.close()
```

## 附录：单测

```powershell
python -m pytest channels/douyin_reverse_ipc/tests -q -p no:cacheprovider
```
