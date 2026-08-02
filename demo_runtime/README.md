# Live Demo

浏览器演示页面把真实本地服务串成一条实时链路：

```text
抖音 Runtime Webhook -> SessionTL / Agent -> 审批 -> 8770 企业退款 API -> 核验 -> 通知 -> Trace
          \-> 8771 企微 Webhook -> SessionEvent -> 去重
```

启动依赖服务后运行：

```powershell
python -m demo_runtime.server --repo-root C:\Users\31368\Desktop\siyu\AgentDesk
```

打开 `http://127.0.0.1:8780/`，点击“启动实时演示”。页面会通过本地 `POST /webhooks/douyin/messages` 和 `POST /webhooks/wecom/messages` 产生两条 HTTP 入站，再由同一个 `SessionEvent` / `ConversationLedger` 处理。事件时间线由运行中的 `TraceWriter` 回调驱动，不读取预生成截图或静态 Trace。

真实抖音 Runtime 联调时，可将 `DY_IPC_WEBHOOK_URL` 指向 `http://127.0.0.1:8780/webhooks/douyin/messages`。
