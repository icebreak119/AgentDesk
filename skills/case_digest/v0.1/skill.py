"""CaseDigest v0.1 - create a privacy-safe reusable case record."""

from __future__ import annotations

from typing import Any


def run(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id 不能为空")
    triage = payload.get("triage_result") or {}
    verify = payload.get("verify_result") or {}
    confirmation = payload.get("customer_confirm_result") or {}
    intent = str(triage.get("intent") or "unknown")
    risk_tag = str(triage.get("risk_tag") or "medium")
    resolution = str(payload.get("resolution") or "unknown")
    confirmation_state = str(confirmation.get("confirmation_state") or "not_collected")
    verify_state = "passed" if verify.get("pass") else "not_passed"

    return {
        "case_id": f"case_{task_id}",
        "channel": str(payload.get("channel") or "unknown"),
        "intent": intent,
        "risk_tag": risk_tag,
        "resolution": resolution,
        "verification": verify_state,
        "customer_confirmation": confirmation_state,
        "reusable_tags": [intent, risk_tag, resolution, confirmation_state],
        "knowledge_snippet": (
            f"{intent} / {risk_tag} 案例: 处置结果={resolution}; "
            f"核验={verify_state}; 客户确认={confirmation_state}。"
        ),
        "privacy": {
            "contains_customer_identity": False,
            "contains_customer_content": False,
            "contains_credential": False,
        },
    }
