from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_A = REPO_ROOT / "orchestrator" / "output" / "test_trace_a.jsonl"
OUTPUT_B = REPO_ROOT / "orchestrator" / "output" / "test_trace_b.jsonl"


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


def test_script_a_consult_trace():
    proc = _run_demo("orchestrator.demo.script_a_consult", OUTPUT_A)
    assert proc.returncode == 0, proc.stderr
    events = _read_trace(OUTPUT_A)
    agents = [e["agent"] for e in events]
    assert "ChannelIngress" in agents
    assert "TriageGuard" in agents
    assert "ActVerify" in agents
    triage = next(e for e in events if e.get("skill") == "IntentTriage")
    assert triage.get("need_approval") is False
    assert any(e.get("event") == "state_transition" for e in events)


def test_script_b_approval_suspend_and_resume():
    proc = _run_demo("orchestrator.demo.script_b_approval", OUTPUT_B)
    assert proc.returncode == 0, proc.stderr
    events = _read_trace(OUTPUT_B)
    assert any(e.get("event") == "approval_required" for e in events)
    assert any(e.get("event") == "approval_granted" for e in events)
    triage = next(e for e in events if e.get("skill") == "IntentTriage")
    assert triage.get("need_approval") is True


def test_script_b_reject():
    output = REPO_ROOT / "orchestrator" / "output" / "test_trace_b_reject.jsonl"
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
