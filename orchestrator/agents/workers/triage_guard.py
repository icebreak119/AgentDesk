"""TriageGuard Worker — 意图分级与回复草案。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILLS = _REPO_ROOT / "skills"


def _load_skill(relative: str):
    path = _SKILLS / relative
    spec = importlib.util.spec_from_file_location(f"skill_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 Skill: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def triage(session_event: dict[str, Any], history: list | None = None) -> dict[str, Any]:
    module = _load_skill("intent_triage/v0.1/skill.py")
    return module.run({"session_event": session_event, "history": history or []})


def plan(
    session_event: dict[str, Any],
    triage_result: dict[str, Any],
    knowledge_hits: list | None = None,
) -> dict[str, Any]:
    module = _load_skill("reply_plan/v0.1/skill.py")
    return module.run(
        {
            "session_event": session_event,
            "triage_result": triage_result,
            "knowledge_hits": knowledge_hits or [],
        }
    )
