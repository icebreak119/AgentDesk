"""CaseLearning Worker helpers for retrieval and privacy-safe case digesting."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from orchestrator.models.task_context import TaskContext

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = REPO_ROOT / "skills" / "case_digest" / "v0.1" / "skill.py"
DEFAULT_KNOWLEDGE_PATH = REPO_ROOT / "orchestrator" / "output" / "case_knowledge.jsonl"


def _load_skill():
    spec = importlib.util.spec_from_file_location("agentdesk_case_digest", SKILL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 Skill: {SKILL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            item = json.loads(raw)
            if isinstance(item, dict):
                records.append(item)
    return records


def retrieve(
    session_event: dict[str, Any],
    triage_result: dict[str, Any],
    *,
    path: Path = DEFAULT_KNOWLEDGE_PATH,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return tag-matched, non-PII case snippets for ReplyPlan."""
    del session_event  # Retrieval uses only workflow tags in the preliminary reference implementation.
    intent = str(triage_result.get("intent") or "unknown")
    risk_tag = str(triage_result.get("risk_tag") or "medium")
    hits: list[dict[str, Any]] = []
    for record in reversed(_read_records(path)):
        if record.get("intent") != intent or record.get("risk_tag") != risk_tag:
            continue
        if record.get("resolution") not in {"done", "escalated", "failed"}:
            continue
        hits.append(
            {
                "ref": f"case://{record.get('case_id')}",
                "snippet": str(record.get("knowledge_snippet") or "已归档历史案例"),
            }
        )
        if len(hits) >= limit:
            break
    return hits


def publish(ctx: TaskContext, *, path: Path = DEFAULT_KNOWLEDGE_PATH) -> dict[str, Any]:
    """Persist a structured digest only after a task reaches a terminal outcome."""
    if ctx.state not in {"done", "failed", "escalated"}:
        raise ValueError(f"任务尚未闭环，不能沉淀案例: {ctx.state}")

    module = _load_skill()
    record = module.run(
        {
            "task_id": ctx.task_id,
            "channel": ctx.channel,
            "triage_result": ctx.triage_result or {},
            "verify_result": ctx.verify_result or {},
            "customer_confirm_result": ctx.customer_confirm_result or {},
            "resolution": ctx.state,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_records(path)
    for item in existing:
        if item.get("case_id") == record["case_id"]:
            return item
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
