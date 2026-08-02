"""CustomerConfirm v0.1 - classify the post-action customer acknowledgement."""

from __future__ import annotations

from typing import Any

POSITIVE = ("已解决", "解决了", "收到", "谢谢", "可以", "好的", "明白了", "清楚了")
NEGATIVE = ("没解决", "未解决", "不行", "还是", "投诉", "错误", "问题还在")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    feedback = str(payload.get("customer_feedback") or "").strip()
    task_id = str(payload.get("task_id") or "task")
    if not feedback:
        return {
            "confirmation_state": "awaiting_feedback",
            "needs_follow_up": False,
            "feedback_summary": "等待客户确认",
            "evidence_ref": f"log://confirm/{task_id}/awaiting",
        }

    negative_hits = [item for item in NEGATIVE if item in feedback]
    if negative_hits:
        return {
            "confirmation_state": "needs_follow_up",
            "needs_follow_up": True,
            "feedback_summary": "客户反馈未解决，需要人工跟进",
            "evidence_ref": f"log://confirm/{task_id}/follow_up",
            "rule_hits": negative_hits,
        }

    positive_hits = [item for item in POSITIVE if item in feedback]
    if positive_hits:
        return {
            "confirmation_state": "confirmed",
            "needs_follow_up": False,
            "feedback_summary": "客户已确认本次处置结果",
            "evidence_ref": f"log://confirm/{task_id}/confirmed",
            "rule_hits": positive_hits,
        }

    return {
        "confirmation_state": "awaiting_feedback",
        "needs_follow_up": False,
        "feedback_summary": "客户反馈未形成明确确认结论",
        "evidence_ref": f"log://confirm/{task_id}/ambiguous",
    }
