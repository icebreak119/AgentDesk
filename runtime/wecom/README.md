# Enterprise WeChat Webhook Adapter

本地演示用企微回调适配器，将企业微信消息转换为 AgentDesk 统一 `SessionEvent`，并保留 `channel=wecom`、`dedupe_key` 和脱敏证据。

启动：

```powershell
python -m runtime.wecom.server --port 8771
```

回调入口：`POST http://127.0.0.1:8771/webhooks/wecom/messages`

这不是企业微信生产网关；复赛可将同一 `WecomWebhookAdapter` 接入真实回调验签、重试和队列。
