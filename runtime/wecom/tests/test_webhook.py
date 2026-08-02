from __future__ import annotations

import json
from pathlib import Path

from runtime.wecom.adapter import WecomWebhookAdapter
from orchestrator.models.session_event import normalize_session_event


def test_wecom_webhook_normalizes_to_same_cross_channel_dedupe_contract(tmp_path: Path):
    adapter = WecomWebhookAdapter(tmp_path / "wecom.jsonl")
    payload = {
        "profile_id": "d6a26b9e-demo",
        "msgid": "wx-test-001",
        "external_userid": "customer-demo-002",
        "chat_id": "wecom-chat-001",
        "create_time": "2026-08-02T09:05:00+08:00",
        "text": "我要退款，改一下账户",
        "sender_name": "客户姓名",
    }
    wecom = adapter.handle(payload)
    douyin = normalize_session_event(
        "douyin",
        "d6a26b9e-demo",
        {
            "event_id": "dy-test-001",
            "customer_id": "customer-demo-002",
            "conversation_id": "dy-chat-001",
            "ts": "2026-08-02T09:05:00+08:00",
            "text": "我要退款，改一下账户",
        },
    )
    event = wecom["session_event"]
    assert event["channel"] == "wecom"
    assert event["requested_action"] == "refund"
    assert event["dedupe_key"] == douyin["dedupe_key"]
    raw = (tmp_path / "wecom.jsonl").read_text(encoding="utf-8")
    assert "客户姓名" not in raw
    assert "我要退款" not in raw
    assert json.loads(raw)["content_hash"]
