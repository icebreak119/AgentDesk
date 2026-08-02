from pathlib import Path

from fastapi.testclient import TestClient

import demo_runtime.app as demo_app
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


def test_demo_run_validates_and_persists_scenario(monkeypatch, tmp_path: Path):
    def fake_workflow(demo, **kwargs):
        demo.final_state = kwargs["scenario"]
        demo.status = "completed"

    monkeypatch.setattr(demo_app, "_run_workflow", fake_workflow)
    client = TestClient(create_app(repo_root=tmp_path, gateway_url="http://testserver"))
    response = client.post("/api/demo/run", json={"scenario": "rollback_failure"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    state = client.get(f"/api/demo/state/{run_id}").json()
    assert state["scenario"] == "rollback_failure"
    assert state["final_state"] == "rollback_failure"

    invalid = client.post("/api/demo/run", json={"scenario": "unknown"})
    assert invalid.status_code == 400
