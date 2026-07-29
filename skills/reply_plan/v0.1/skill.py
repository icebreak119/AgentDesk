"""ReplyPlan v0.1 — 模板回复 MVP（高风险仅出草案，不附带执行令牌）。"""

from __future__ import annotations

from typing import Any


def run(payload: dict[str, Any]) -> dict[str, Any]:
    triage = payload.get("triage_result") or {}
    session_event = payload.get("session_event") or {}
    knowledge_hits = payload.get("knowledge_hits") or []

    intent = str(triage.get("intent") or "unknown")
    risk_tag = str(triage.get("risk_tag") or "medium")
    need_approval = bool(triage.get("need_approval"))

    customer_ref = str(session_event.get("customer_ref") or "客户")

    if need_approval or risk_tag == "high":
        draft_text = (
            f"{customer_ref}您好，您的问题涉及账户或资金变更，"
            "需要人工审核后处理，请稍候，我们会尽快联系您。"
        )
        action_type = "escalate"
    elif intent == "consult":
        draft_text = (
            f"{customer_ref}您好，感谢关注！产品价格请参考官方页面或联系顾问获取最新报价。"
            "如需详细方案，请告知您的具体需求。"
        )
        action_type = "reply"
    else:
        draft_text = f"{customer_ref}您好，已收到您的消息，我们会尽快为您处理。"
        action_type = "reply"

    citations = [
        str(item.get("ref") or item)
        for item in knowledge_hits[:3]
        if str(item.get("ref") if isinstance(item, dict) else item).strip()
    ]

    result: dict[str, Any] = {
        "draft_text": draft_text,
        "action_type": action_type,
        "risk_tag": risk_tag,
        "citations": citations,
    }
    # 高风险仅出方案，不附带执行令牌
    if need_approval or risk_tag == "high":
        result["approval_token"] = None
    return result
