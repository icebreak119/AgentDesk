from pathlib import Path

from fastapi.testclient import TestClient

from demo_runtime.app import create_app


def test_douyin_callback_route_emits_session_event(tmp_path: Path):
    client = TestClient(create_app(repo_root=tmp_path, gateway_url="http://testserver"))
    response = client.post(
        "/webhooks/douyin/messages",
        json={
            "account_code": "d6a26b9e-demo",
            "event_id": "dy-gateway-test",
            "customer_id": "customer-demo-002",
            "conversation_id": "dy-chat-test",
            "ts": "2026-08-02T09:05:00+08:00",
            "text": "我要退款，改一下账户",
        },
    )
    assert response.status_code == 200
    event = response.json()["data"]["session_event"]
    assert event["channel"] == "douyin"
    assert event["requested_action"] == "refund"
    assert event["dedupe_key"] == "8ee5656a09b5ba30fb3e3d88"
