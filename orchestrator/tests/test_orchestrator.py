from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
def _run_demo(module: str, output: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, "-o", str(output)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**dict(**__import__("os").environ), "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def _read_trace(path: Path) -> list[dict]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            lines.append(json.loads(raw))
    return lines


def test_task_context_roundtrip():
    from orchestrator.models.task_context import TaskContext

    ctx = TaskContext(
        task_id="task_x",
        profile_id="p1",
        session_id="s1",
        channel="douyin",
        triage_result={"intent": "consult", "need_approval": False},
    )
    restored = TaskContext.from_dict(json.loads(ctx.to_json()))
    assert restored.task_id == "task_x"
    assert restored.triage_result["intent"] == "consult"
    assert restored.knowledge_hits == []


def test_script_a_consult_trace(tmp_path: Path):
    output = tmp_path / "trace_a.jsonl"
    proc = _run_demo("orchestrator.demo.script_a_consult", output)
    assert proc.returncode == 0, proc.stderr
    events = _read_trace(output)
    agents = [e["agent"] for e in events]
    assert "ChannelIngress" in agents
    assert "TriageGuard" in agents
    assert "ActVerify" in agents
    triage = next(e for e in events if e.get("skill") == "IntentTriage")
    assert triage.get("need_approval") is False
    assert any(e.get("event") == "state_transition" for e in events)
    assert len([e for e in events if e.get("skill") == "ChannelSend"]) == 1
    assert len([e for e in events if e.get("skill") == "OutcomeVerify"]) == 1
    assert len([e for e in events if e.get("skill") == "CustomerConfirm"]) == 1
    assert len([e for e in events if e.get("skill") == "CaseDigest"]) == 1


def test_script_b_approval_suspend_and_resume(tmp_path: Path):
    output = tmp_path / "trace_b.jsonl"
    proc = _run_demo("orchestrator.demo.script_b_approval", output)
    assert proc.returncode == 0, proc.stderr
    events = _read_trace(output)
    assert any(e.get("event") == "approval_required" for e in events)
    assert any(e.get("event") == "approval_granted" for e in events)
    assert any(e.get("event") == "business_action_verified" and e.get("status") == "verified" for e in events)
    notification = next(e for e in events if e.get("event") == "customer_notification_sent")
    verified_index = next(i for i, e in enumerate(events) if e.get("event") == "business_action_verified")
    assert events.index(notification) > verified_index
    action_records = [json.loads(line) for line in (tmp_path / "business_actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert action_records and action_records[0]["status"] == "executed"
    triage = next(e for e in events if e.get("skill") == "IntentTriage")
    assert triage.get("need_approval") is True


def test_script_b_reject(tmp_path: Path):
    output = tmp_path / "trace_b_reject.jsonl"
    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator.demo.script_b_approval", "--reject", "-o", str(output)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    events = _read_trace(output)
    assert any(e.get("event") == "approval_rejected" for e in events)
    assert any(e.get("skill") == "CaseDigest" for e in events)
    assert not (tmp_path / "business_actions.jsonl").exists()


def test_script_b_business_action_verification_failure_rolls_back(tmp_path: Path):
    output = tmp_path / "trace_b_rollback.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.demo.script_b_approval",
            "--inject-verify-failure",
            "-o",
            str(output),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    events = _read_trace(output)
    assert any(e.get("event") == "business_action_verified" and e.get("status") == "failed" for e in events)
    assert any(e.get("event") == "business_action_rollback_verified" and e.get("status") == "rolled_back" for e in events)
    assert not any(e.get("event") == "customer_notification_sent" for e in events)
    assert any(e.get("event") == "state_transition" and e.get("to") == "failed" for e in events)
    action_records = [json.loads(line) for line in (tmp_path / "business_actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [record["status"] for record in action_records] == ["executed", "rolled_back"]


def test_business_action_is_idempotent_and_conflict_safe(tmp_path: Path):
    from orchestrator.models.business_action import JsonlBusinessActionAdapter

    payload = {
        "task_id": "task_action_test",
        "profile_id": "profile_test",
        "action_type": "refund",
        "order_id": "order_test_001",
        "amount": "19.90",
        "currency": "CNY",
        "reason": "测试退款",
        "idempotency_key": "task_action_test:business_action:1",
        "approval_token": "approval_test",
    }
    adapter = JsonlBusinessActionAdapter(tmp_path / "actions.jsonl")
    first = adapter.execute(payload)
    repeat = adapter.execute(payload)
    conflict = adapter.execute({**payload, "amount": "29.90"})
    assert first["status"] == "executed"
    assert repeat["operation_id"] == first["operation_id"]
    assert conflict["error_code"] == "idempotency_conflict"
    verify_failed = adapter.verify(payload, first, inject_failure=True)
    rollback = adapter.rollback(payload, first)
    assert verify_failed["status"] == "failed"
    assert rollback["status"] == "rolled_back"
    raw = (tmp_path / "actions.jsonl").read_text(encoding="utf-8")
    assert "测试退款" in raw
    assert "customer_feedback" not in raw


def test_business_action_worker_requires_approval_state(tmp_path: Path):
    from orchestrator.agents.workers import act_verify
    from orchestrator.models.task_context import TaskContext

    ctx = TaskContext(
        task_id="task_unapproved",
        profile_id="profile_test",
        session_id="session_test",
        channel="douyin",
        state="suspended",
        need_approval=True,
        approval_token="",
        triage_result={"requested_action": "refund"},
        raw_event={"order_id": "order_test_001", "amount": "19.90"},
    )
    result = act_verify.execute_business_action(ctx, path=tmp_path / "actions.jsonl")
    assert result["status"] == "failed"
    assert result["error_code"] == "approval_required"
    assert not (tmp_path / "actions.jsonl").exists()


def test_business_action_jsonl_excludes_identity_content_and_credentials(tmp_path: Path):
    from orchestrator.models.business_action import JsonlBusinessActionAdapter

    payload = {
        "task_id": "task_privacy",
        "profile_id": "profile_test",
        "action_type": "refund",
        "order_id": "order_test_001",
        "amount": "19.90",
        "currency": "CNY",
        "reason": "客户申请退款",
        "idempotency_key": "task_privacy:business_action:1",
        "approval_token": "secret-token-not-for-log",
    }
    adapter = JsonlBusinessActionAdapter(tmp_path / "actions.jsonl")
    adapter.execute(payload)
    raw = (tmp_path / "actions.jsonl").read_text(encoding="utf-8")
    assert "secret-token-not-for-log" not in raw
    assert "sender_name" not in raw
    assert "customer_feedback" not in raw
    assert "我要退款" not in raw


def test_script_c_multichannel_dedupe_confirmation_and_case_reuse(tmp_path: Path):
    output = tmp_path / "trace_c.jsonl"
    knowledge = tmp_path / "case_knowledge.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.demo.script_c_multichannel_case",
            "-o",
            str(output),
            "--knowledge-output",
            str(knowledge),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    events = _read_trace(output)
    assert any(e.get("event") == "duplicate_linked" for e in events)
    assert any(e.get("skill") == "CustomerConfirm" and e.get("status") == "confirmed" for e in events)
    retrieve = [e for e in events if e.get("skill") == "CaseKnowledgeRetrieve"]
    assert retrieve and retrieve[-1].get("output", {}).get("hit_count", 0) >= 1
    records = _read_trace(knowledge)
    assert len(records) == 2
    assert all(record["privacy"]["contains_customer_content"] is False for record in records)
