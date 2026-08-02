from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from enterprise_simulator.app import create_app
from enterprise_simulator.store import EnterpriseBusinessStore
from orchestrator.models.approval import issue_approval_token
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
            "approval_token": "",
        }
        payload["approval_token"] = issue_approval_token(payload)
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
        assert adapter.verify(payload, first)["status"] == "failed"
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
            "approval_token": "",
        }
        payload["approval_token"] = issue_approval_token(payload)
        adapter.execute(payload)
        try:
            conflict_payload = {**payload, "amount": "29.90"}
            conflict_payload["approval_token"] = issue_approval_token(conflict_payload)
            adapter.execute(conflict_payload)
        except ValueError as exc:
            assert str(exc) == "idempotency_conflict"
        else:
            raise AssertionError("expected idempotency conflict")
        try:
            missing_payload = {
                **payload,
                "order_id": "missing-order",
                "idempotency_key": "http_conflict_task:missing:1",
            }
            missing_payload["approval_token"] = issue_approval_token(missing_payload)
            adapter.execute(missing_payload)
        except ValueError as exc:
            assert str(exc) == "order_not_found"
        else:
            raise AssertionError("expected missing order")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_enterprise_store_rebuilds_rolled_back_state_after_restart(tmp_path: Path):
    evidence = tmp_path / "enterprise.jsonl"
    payload = {
        "task_id": "restart_task",
        "profile_id": "d6a26b9e-demo",
        "action_type": "refund",
        "order_id": "order-demo-001",
        "amount": "19.90",
        "currency": "CNY",
        "reason": "测试退款",
        "idempotency_key": "restart_task:refund:1",
        "approval_token": "",
    }
    payload["approval_token"] = issue_approval_token(payload)
    first = EnterpriseBusinessStore(evidence)
    requested = first.apply_refund(payload)
    executed = first.execute_refund(
        requested["operation_id"],
        profile_id=payload["profile_id"],
        idempotency_key=payload["idempotency_key"],
        approval_token=payload["approval_token"],
    )
    rolled_back = first.rollback_refund(
        executed["operation_id"],
        profile_id=payload["profile_id"],
        idempotency_key=payload["idempotency_key"],
        approval_token=payload["approval_token"],
    )
    assert rolled_back["status"] == "rolled_back"

    restarted = EnterpriseBusinessStore(evidence)
    restored = restarted.get_operation(executed["operation_id"], payload["profile_id"])
    assert restored["status"] == "rolled_back"


def test_enterprise_store_rejects_second_refund_until_rollback(tmp_path: Path):
    evidence = tmp_path / "enterprise.jsonl"
    first_payload = {
        "task_id": "balance_task_1",
        "profile_id": "d6a26b9e-demo",
        "action_type": "refund",
        "order_id": "order-demo-001",
        "amount": "199.00",
        "currency": "CNY",
        "reason": "测试退款",
        "idempotency_key": "balance_task_1:refund:1",
        "approval_token": "",
    }
    first_payload["approval_token"] = issue_approval_token(first_payload)
    second_payload = {
        **first_payload,
        "task_id": "balance_task_2",
        "idempotency_key": "balance_task_2:refund:1",
        "approval_token": "",
    }
    second_payload["approval_token"] = issue_approval_token(second_payload)

    store = EnterpriseBusinessStore(evidence)
    first = store.apply_refund(first_payload)
    store.execute_refund(
        first["operation_id"],
        profile_id=first_payload["profile_id"],
        idempotency_key=first_payload["idempotency_key"],
        approval_token=first_payload["approval_token"],
    )
    with pytest.raises(ValueError, match="amount_exceeds_refundable"):
        store.apply_refund(second_payload)
    assert store.query_order(first_payload["profile_id"], first_payload["order_id"])["refundable_amount"] == "0.00"
