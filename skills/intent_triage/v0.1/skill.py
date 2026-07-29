"""IntentTriage v0.1 — 规则引擎 MVP（无 LLM 依赖）。"""

from __future__ import annotations

from typing import Any

HIGH_RISK_KEYWORDS = ("退款", "改账户", "改账号", "换账户", "修改账户")
CONSULT_KEYWORDS = ("价格", "多少钱", "费用", "怎么收费", "报价")


def _collect_hits(content: str, keywords: tuple[str, ...]) -> list[str]:
    return [kw for kw in keywords if kw in content]


def run(payload: dict[str, Any]) -> dict[str, Any]:
    session_event = payload.get("session_event") or {}
    content = str(session_event.get("content") or "").strip()

    high_hits = _collect_hits(content, HIGH_RISK_KEYWORDS)
    if high_hits:
        confidence = min(0.95, 0.55 + 0.15 * len(high_hits))
        return {
            "intent": "refund_or_account_change",
            "priority": "high",
            "risk_tag": "high",
            "need_approval": True,
            "confidence": round(confidence, 2),
            "rule_hits": high_hits,
        }

    consult_hits = _collect_hits(content, CONSULT_KEYWORDS)
    if consult_hits:
        confidence = min(0.9, 0.5 + 0.2 * len(consult_hits))
        return {
            "intent": "consult",
            "priority": "low",
            "risk_tag": "low",
            "need_approval": False,
            "confidence": round(confidence, 2),
            "rule_hits": consult_hits,
        }

    return {
        "intent": "unknown",
        "priority": "medium",
        "risk_tag": "medium",
        "need_approval": False,
        "confidence": 0.35,
        "rule_hits": [],
    }
