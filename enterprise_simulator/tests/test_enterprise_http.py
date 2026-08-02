from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn

from enterprise_simulator.app import create_app
from orchestrator.models.business_action import HttpBusinessActionAdapter


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(url: str) -> None:
    for _ in range(50):
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"service did not start: {url}")


def test_http_business_action_covers_order_execute_verify_rollback(tmp_path: Path):
    port = _free_port()
    evidence = tmp_path / "enterprise.jsonl"
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(evidence),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for(f"{base_url}/enterprise/ping")
        adapter = HttpBusinessActionAdapter(base_url)
        payload = {
            "task_id": "http_test_task",
            "profile_id": "d6a26b9e-demo",
            "action_type": "refund",
            "order_id": "order-demo-001",
            "amount": "199.00",
            "currency": "CNY",
            "reason": "测试退款",
            "idempotency_key": "http_test_task:refund:1",
            "approval_token": "approval-not-for-log",
        }
        order = adapter.query_order(payload)
        assert order["order_id"] == "order-demo-001"
        first = adapter.execute(payload)
        repeat = adapter.execute(payload)
        assert first["status"] == "executed"
        assert repeat["operation_id"] == first["operation_id"]
        verified = adapter.verify(payload, first)
        assert verified["status"] == "verified"
        rollback = adapter.rollback(payload, first)
        assert rollback["status"] == "rolled_back"
        assert rollback["rollback_of"] == first["operation_id"]

        raw = evidence.read_text(encoding="utf-8")
        assert "approval-not-for-log" not in raw
        assert "客户姓名" not in raw
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
        assert {record["event"] for record in records} >= {
            "order_queried", "refund_requested", "refund_executed", "refund_rolled_back",
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_http_business_action_rejects_bad_order_and_idempotency_conflict(tmp_path: Path):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(tmp_path / "enterprise.jsonl"), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for(f"{base_url}/enterprise/ping")
        adapter = HttpBusinessActionAdapter(base_url)
        payload = {
            "task_id": "http_conflict_task",
            "profile_id": "d6a26b9e-demo",
            "action_type": "refund",
            "order_id": "order-demo-001",
            "amount": "19.90",
            "currency": "CNY",
            "reason": "测试",
            "idempotency_key": "http_conflict_task:refund:1",
            "approval_token": "approval",
        }
        adapter.execute(payload)
        try:
            adapter.execute({**payload, "amount": "29.90"})
        except ValueError as exc:
            assert str(exc) == "idempotency_conflict"
        else:
            raise AssertionError("expected idempotency conflict")
        try:
            adapter.execute({**payload, "order_id": "missing-order"})
        except ValueError as exc:
            assert str(exc) == "order_not_found"
        else:
            raise AssertionError("expected missing order")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
