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


def test_agent_message_roundtrip_and_validation():
    from orchestrator.models.agent_message import AgentMessage

    message = AgentMessage(
        message_id="msg_001",
        task_id="task_001",
        from_agent="agent.session_tl",
        to_agent="agent.act_verify",
        intent="execute_approved_refund",
        context_ref="task://task_001",
        payload={"skill": "BusinessAction"},
        evidence_refs=("trace://task_001/requested",),
        expected_state="acting",
        risk_tag="high",
    )
    restored = AgentMessage.from_dict(message.to_dict())
    assert restored == message
    with pytest.raises(ValueError):
        AgentMessage(
            message_id="",
            task_id="task_001",
            from_agent="agent.session_tl",
            to_agent="agent.act_verify",
            intent="handoff",
            context_ref="task://task_001",
        )


def test_agent_handoff_and_context_wire_payloads_redact_sensitive_data():
    from orchestrator.models.agent_message import AgentMessage
    from orchestrator.models.privacy import redact_for_transport
    from orchestrator.models.task_context import TaskContext

    ctx = TaskContext(
        task_id="task_sensitive",
        profile_id="profile_test",
        session_id="session_test",
        channel="douyin",
        approval_token="secret-approval-token",
        raw_event={"text": "我要退款", "sender_name": "客户甲", "order_id": "order-1"},
    )
    wire_context = ctx.to_wire_dict()
    assert wire_context["approval_token"] == "[REDACTED]"
    assert wire_context["raw_event"] == "[REDACTED]"
    assert "secret-approval-token" not in ctx.to_wire_json()

    message = AgentMessage(
        message_id="msg_sensitive",
        task_id="task_sensitive",
        from_agent="agent.session_tl",
        to_agent="agent.act_verify",
        intent="execute_approved_refund",
        context_ref="task://task_sensitive",
        payload={"approval_token": "secret-approval-token", "text": "我要退款"},
        expected_state="acting",
        risk_tag="high",
    )
    wire_message = message.to_wire_dict()
    assert wire_message["payload"] == {"approval_token": "[REDACTED]", "text": "[REDACTED]"}

    redacted = redact_for_transport(
        {
            "customer_ref": "客户甲",
            "draft_text": "客户甲您好，退款已完成",
            "actual_content": "客户甲您好，退款已完成",
        }
    )
    assert redacted == {
        "customer_ref": "[REDACTED]",
        "draft_text": "[REDACTED]",
        "actual_content": "[REDACTED]",
    }


def test_trace_writer_redacts_sensitive_fields(tmp_path: Path):
    from orchestrator.models.trace import TraceWriter

    output = tmp_path / "trace.jsonl"
    with TraceWriter(output) as trace:
        trace.emit(
            "task_sensitive",
            "ActVerify",
            output={"approval_token": "secret-token", "text": "客户原文"},
        )
    event = _read_trace(output)[0]
    assert event["output"] == {"approval_token": "[REDACTED]", "text": "[REDACTED]"}


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
    action_records = [json.loads(line) for line in (tmp_path / "trace_b.business_actions.jsonl").read_text(encoding="utf-8").splitlines()]
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
    assert not (tmp_path / "trace_b_reject.business_actions.jsonl").exists()


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
    action_records = [json.loads(line) for line in (tmp_path / "trace_b_rollback.business_actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [record["status"] for record in action_records] == ["executed", "rolled_back"]


def test_script_b_rollback_failure_escalates_to_human(tmp_path: Path):
    output = tmp_path / "trace_b_rollback_failed.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.demo.script_b_approval",
            "--inject-rollback-failure",
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
    rollback = next(e for e in events if e.get("event") == "business_action_rollback_failed")
    assert rollback["status"] == "escalated"
    assert rollback["output"]["next_action"] == "human_review"
    assert any(e.get("event") == "state_transition" and e.get("to") == "escalated" for e in events)
    assert not any(e.get("event") == "customer_notification_sent" for e in events)


def test_business_action_is_idempotent_and_conflict_safe(tmp_path: Path):
    from orchestrator.models.business_action import JsonlBusinessActionAdapter
    from orchestrator.models.approval import issue_approval_token

    payload = {
        "task_id": "task_action_test",
        "profile_id": "profile_test",
        "action_type": "refund",
        "order_id": "order_test_001",
        "amount": "19.90",
        "currency": "CNY",
        "reason": "测试退款",
        "idempotency_key": "task_action_test:business_action:1",
        "approval_token": "",
    }
    payload["approval_token"] = issue_approval_token(payload)
    adapter = JsonlBusinessActionAdapter(tmp_path / "actions.jsonl")
    first = adapter.execute(payload)
    repeat = adapter.execute(payload)
    conflict_payload = {**payload, "amount": "29.90"}
    conflict_payload["approval_token"] = issue_approval_token(conflict_payload)
    conflict = adapter.execute(conflict_payload)
    assert first["status"] == "executed"
    assert repeat["operation_id"] == first["operation_id"]
    assert conflict["error_code"] == "idempotency_conflict"
    verify_failed = adapter.verify(payload, first, inject_failure=True)
    rollback = adapter.rollback(payload, first)
    verify_after_rollback = adapter.verify(payload, first)
    assert verify_failed["status"] == "failed"
    assert rollback["status"] == "rolled_back"
    assert verify_after_rollback["error_code"] == "action_already_rolled_back"
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


def test_business_action_worker_rejects_unscoped_approval(tmp_path: Path):
    from orchestrator.agents.workers import act_verify
    from orchestrator.models.approval import issue_approval_token
    from orchestrator.models.task_context import TaskContext

    ctx = TaskContext(
        task_id="task_scope",
        profile_id="profile_test",
        session_id="session_test",
        channel="douyin",
        state="approved",
        need_approval=True,
        approval_token=issue_approval_token({
            "task_id": "other_task",
            "profile_id": "profile_test",
            "action_type": "refund",
            "order_id": "order_test_001",
            "amount": "19.90",
            "currency": "CNY",
            "reason": "测试退款",
            "idempotency_key": "other_task:business_action:1",
            "approval_token": "placeholder",
        }),
        triage_result={"requested_action": "refund"},
        raw_event={"order_id": "order_test_001", "amount": "19.90", "refund_reason": "测试退款"},
    )
    result = act_verify.execute_business_action(ctx, path=tmp_path / "actions.jsonl")
    assert result["error_code"] == "approval_scope_invalid"
    assert not (tmp_path / "actions.jsonl").exists()


def test_state_transition_rejects_wrong_execute_source(tmp_path: Path):
    from orchestrator.agents.session_tl import SessionTL
    from orchestrator.models.task_context import TaskContext
    from orchestrator.models.trace import TraceWriter

    ctx = TaskContext(
        task_id="task_state",
        profile_id="profile_test",
        session_id="session_test",
        channel="douyin",
        state="suspended",
        reply_draft={"draft_text": "test"},
    )
    with TraceWriter(tmp_path / "trace.jsonl") as trace:
        with pytest.raises(RuntimeError, match="状态转移来源不一致"):
            SessionTL().execute_send_verify(ctx, trace, from_state="planning")


def test_business_action_jsonl_excludes_identity_content_and_credentials(tmp_path: Path):
    from orchestrator.models.business_action import JsonlBusinessActionAdapter
    from orchestrator.models.approval import issue_approval_token

    payload = {
        "task_id": "task_privacy",
        "profile_id": "profile_test",
        "action_type": "refund",
        "order_id": "order_test_001",
        "amount": "19.90",
        "currency": "CNY",
        "reason": "客户申请退款",
        "idempotency_key": "task_privacy:business_action:1",
        "approval_token": "",
    }
    payload["approval_token"] = issue_approval_token(payload)
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
