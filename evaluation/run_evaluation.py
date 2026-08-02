"""Run the preliminary AgentDesk quality and safety evaluation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures.jsonl")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_intent_skill():
    path = REPO_ROOT / "skills/intent_triage/v0.1/skill.py"
    spec = importlib.util.spec_from_file_location("agentdesk_eval_intent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载评测 Skill: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixtures() -> list[dict[str, Any]]:
    return [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_intent() -> dict[str, Any]:
    from orchestrator.models.session_event import normalize_session_event

    skill = _load_intent_skill()
    rows = []
    for fixture in _fixtures():
        event = normalize_session_event(
            "douyin",
            "evaluation-profile",
            {
                "conversation_id": f"eval-{fixture['id']}",
                "event_id": f"event-{fixture['id']}",
                "customer_id": f"customer-{fixture['id']}",
                "text": fixture["text"],
                "ts": "2026-08-02T09:05:00+08:00",
            },
        )
        actual = skill.run({"session_event": event})
        expected = {
            "intent": fixture["expected_intent"],
            "priority": fixture["expected_priority"],
            "need_approval": fixture["expected_need_approval"],
            "requested_action": fixture["expected_action"],
        }
        selected = {key: actual.get(key) for key in expected}
        rows.append({"id": fixture["id"], "pass": selected == expected, "expected": expected, "actual": selected})
    passed = sum(row["pass"] for row in rows)
    return {"passed": passed, "total": len(rows), "accuracy": passed / len(rows), "cases": rows}


def evaluate_dedupe() -> dict[str, Any]:
    from orchestrator.models.conversation_ledger import ConversationLedger
    from orchestrator.models.session_event import normalize_session_event

    ledger = ConversationLedger()
    base = {
        "customer_id": "evaluation-customer",
        "text": "我要退款，改一下账户",
        "ts": "2026-08-02T09:05:00+08:00",
    }
    first = normalize_session_event("douyin", "evaluation-profile", {**base, "conversation_id": "dy-eval"})
    second = normalize_session_event("qywx", "evaluation-profile", {**base, "conversation_id": "wx-eval"})
    distinct = normalize_session_event(
        "douyin",
        "evaluation-profile",
        {**base, "conversation_id": "dy-eval-2", "text": "想了解产品价格"},
    )
    results = [
        ledger.register("eval-first", first),
        ledger.register("eval-duplicate", second),
        ledger.register("eval-distinct", distinct),
    ]
    checks = [
        results[0].get("accepted") is True,
        results[1].get("accepted") is False and results[1].get("reason") == "same_customer_content_window",
        results[2].get("accepted") is True,
    ]
    return {"passed": sum(checks), "total": len(checks), "accuracy": sum(checks) / len(checks), "results": results}


def _run_script(arguments: list[str], output_dir: Path) -> tuple[int, float, list[dict[str, Any]]]:
    trace = output_dir / "trace.jsonl"
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator.demo.script_b_approval", *arguments, "-o", str(trace)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    elapsed = time.perf_counter() - started
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()] if trace.is_file() else []
    if proc.returncode != 0:
        raise RuntimeError(f"剧本失败: {' '.join(arguments)}\n{proc.stderr[-1000:]}")
    return proc.returncode, elapsed, events


def _event(events: list[dict[str, Any]], name: str, status: str | None = None) -> bool:
    return any(item.get("event") == name and (status is None or item.get("status") == status) for item in events)


def evaluate_safety(latency_runs: int) -> dict[str, Any]:
    scenarios = [
        ("success", [], {"final": "done", "notification": True, "verified": True}),
        ("reject", ["--reject"], {"final": "failed", "notification": False, "verified": False}),
        (
            "verify_failure_rollback",
            ["--inject-verify-failure"],
            {"final": "failed", "notification": False, "rollback": "rolled_back"},
        ),
        (
            "rollback_failure_escalation",
            ["--inject-rollback-failure"],
            {"final": "escalated", "notification": False, "rollback_failed": True},
        ),
    ]
    checks = []
    with tempfile.TemporaryDirectory(prefix="agentdesk-eval-") as temp:
        root = Path(temp)
        for name, args, expected in scenarios:
            _, elapsed, events = _run_script(args, root / name)
            transitions = [e for e in events if e.get("event") == "state_transition"]
            final = transitions[-1].get("to") if transitions else ""
            passed = final == expected["final"] and _event(events, "customer_notification_sent") == expected.get("notification", False)
            if expected.get("verified"):
                passed = passed and _event(events, "business_action_verified", "verified")
            if expected.get("rollback"):
                passed = passed and _event(events, "business_action_rollback_verified", expected["rollback"])
            if expected.get("rollback_failed"):
                passed = passed and _event(events, "business_action_rollback_failed", "escalated")
            if name == "reject":
                passed = passed and not _event(events, "business_action_executed")
            checks.append({"scenario": name, "pass": passed, "elapsed_ms": round(elapsed * 1000, 2), "final_state": final})

        timings = []
        for index in range(max(1, latency_runs)):
            _, elapsed, _ = _run_script([], root / f"latency-{index}")
            timings.append(elapsed * 1000)
    ordered = sorted(timings)
    p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
    passed = sum(item["pass"] for item in checks)
    return {
        "passed": passed,
        "total": len(checks),
        "invariant_pass_rate": passed / len(checks),
        "scenarios": checks,
        "latency_ms": {"runs": len(timings), "mean": round(mean(timings), 2), "p95": round(p95, 2)},
    }


def _markdown(result: dict[str, Any]) -> str:
    intent = result["intent"]
    dedupe = result["dedupe"]
    safety = result["safety"]
    return f"""# AgentDesk 初赛量化评测报告

> 评测入口：`python evaluation/run_evaluation.py`。数据为脱敏固定样本，结果用于验证参考编排器的行为不变量，不代表生产流量指标。

## 结果摘要

| 指标 | 结果 |
|---|---:|
| 意图/风险样本通过率 | {intent['passed']}/{intent['total']}（{intent['accuracy']:.1%}） |
| 跨渠道去重断言通过率 | {dedupe['passed']}/{dedupe['total']}（{dedupe['accuracy']:.1%}） |
| 四条高风险分支不变量通过率 | {safety['passed']}/{safety['total']}（{safety['invariant_pass_rate']:.1%}） |
| 成功剧本本地平均耗时 | {safety['latency_ms']['mean']:.2f} ms |
| 成功剧本本地 P95 耗时 | {safety['latency_ms']['p95']:.2f} ms |

## 高风险分支

| 场景 | 终态 | 关键断言 |
|---|---|---|
| 成功 | `done` | 业务动作核验成功后才发送通知 |
| 审批拒绝 | `failed` | 无 BusinessAction 执行、无客户通知 |
| 核验失败 | `failed` | 触发补偿回滚、无客户成功通知 |
| 回滚失败 | `escalated` | 写入 `human_review`，无客户成功通知 |

## 边界

- 意图识别当前为规则化 Skill，样本指标不等同于 LLM 泛化能力。
- 延迟只覆盖本地参考编排器与 JSONL Mock，不代表真实 ERP、支付或渠道延迟。
- 真实生产指标将在复赛接入官方 AgentTeams、企业系统和持久化 Trace 后重新采集。
"""


def run(*, latency_runs: int = 12, json_output: Path | None = None, markdown_output: Path | None = None) -> dict[str, Any]:
    result = {"intent": evaluate_intent(), "dedupe": evaluate_dedupe(), "safety": evaluate_safety(latency_runs)}
    result["overall_pass"] = all(
        section["passed"] == section["total"]
        for section in (result["intent"], result["dedupe"], result["safety"])
    )
    if json_output:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if markdown_output:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(_markdown(result), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentDesk 初赛量化评测")
    parser.add_argument("--latency-runs", type=int, default=12)
    parser.add_argument("--json-output", type=Path, default=Path("tmp/evaluation_result.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("docs/19_量化评测报告.md"))
    args = parser.parse_args()
    result = run(
        latency_runs=max(1, args.latency_runs),
        json_output=args.json_output,
        markdown_output=args.markdown_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
