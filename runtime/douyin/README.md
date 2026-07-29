# AgentDesk 抖音 Channel Runtime

初赛可独立运行的渠道工具层源码，对应 Skill **ChannelSend** / **OutcomeVerify** 的 HTTP 实现。

## 目录

```
runtime/douyin/
├── channels/
│   ├── douyin_reverse_ipc/     # HTTP 8765 + 中文控制台 /console
│   └── douyin_all_user/
│       ├── reverse_runtime/    # 抖音 IM 收发包、凭证与消息存储
│       └── reverse_runtime_utils_preload.py
├── requirements.txt
└── README.md
```

## 快速开始

在 **本目录**（`runtime/douyin`）下执行：

```powershell
pip install -r requirements.txt
# 凭证采集（首次）需要浏览器：
playwright install chromium
```

### 1. 准备凭证库

```powershell
cd channels\douyin_all_user\reverse_runtime
python dy_apis/collect_im_account_credentials.py --account-code my_acc --db-path .\_douyin_im_accounts.db
cd ..\..\..
```

或使用已有 `_douyin_im_accounts.db`（勿提交含真实 cookie 的库文件）。

### 2. 启动服务

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m channels.douyin_reverse_ipc.http_server `
  --db-path channels\douyin_all_user\reverse_runtime\_douyin_im_accounts.db `
  --host 127.0.0.1 --port 8765
```

浏览器打开：**http://127.0.0.1:8765/console**

### 3. 跑测试（无需真实账号）

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m pytest channels/douyin_reverse_ipc/tests -q -p no:cacheprovider
```

## MCP 等价契约

见仓库根目录 `docs/contracts/`：

- `channel.send_message.json`
- `channel.query_session.json`
- `channel.fetch_history.json`

## 说明

- 本包从开发仓抽取，**不依赖**第三方桌面壳 UI。
- AgentTeams 编排层（DutyManager / SessionTL）为复赛实现；本目录仅渠道 Runtime。
- 完整初赛材料见 `docs/`。
