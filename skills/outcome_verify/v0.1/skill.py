"""OutcomeVerify v0.1 - receipt and content consistency verification."""

from __future__ import annotations

from typing import Any


def run(payload: dict[str, Any]) -> dict[str, Any]:
    expected = str(payload.get("expected_content") or "")
    receipt = payload.get("send_receipt") or {}
    if not isinstance(receipt, dict):
        raise ValueError("send_receipt 必须是对象")
    session_ref = str(payload.get("session_ref") or "")
    task_id = str(payload.get("task_id") or "task")
    if receipt.get("status") != "ok":
        return {
            "pass": False,
            "actual_content": "",
            "evidence_type": "receipt",
            "evidence_ref": f"log://verify/{task_id}/receipt_failed",
            "reason": "receipt_not_ok",
        }

    receipt_raw = receipt.get("receipt_raw") or {}
    actual = str(
        payload.get("actual_content")
        or (receipt_raw.get("text") if isinstance(receipt_raw, dict) else "")
        or ""
    )
    if actual:
        passed = actual == expected
        evidence_type = "receipt_and_content"
        reason = "content_match" if passed else "content_mismatch"
    else:
        passed = True
        evidence_type = "receipt"
        reason = "receipt_confirmed"
    return {
        "pass": passed,
        "actual_content": actual,
        "evidence_type": evidence_type,
        "evidence_ref": f"log://verify/{task_id}/{session_ref or 'session'}",
        "reason": reason,
    }
