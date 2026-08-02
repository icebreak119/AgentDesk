"""BusinessAction v0.1 - validate a high-risk refund request."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

SUPPORTED_ACTIONS = {"refund"}
SUPPORTED_CURRENCIES = {"CNY"}


def _required_text(payload: dict[str, Any], name: str) -> str:
    value = str(payload.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} 不能为空")
    return value


def _amount(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("amount 必须是正数") from exc
    if amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("amount 必须是两位小数以内的正数")
    return format(amount.quantize(Decimal("0.01")), "f")


def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("BusinessAction 输入必须是对象")
    action_type = _required_text(payload, "action_type")
    if action_type not in SUPPORTED_ACTIONS:
        raise ValueError(f"暂不支持的业务动作: {action_type}")
    currency = _required_text(payload, "currency").upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(f"暂不支持的币种: {currency}")
    approval_token = _required_text(payload, "approval_token")
    return {
        "task_id": _required_text(payload, "task_id"),
        "profile_id": _required_text(payload, "profile_id"),
        "action_type": action_type,
        "order_id": _required_text(payload, "order_id"),
        "amount": _amount(payload.get("amount")),
        "currency": currency,
        "reason": _required_text(payload, "reason"),
        "idempotency_key": _required_text(payload, "idempotency_key"),
        "approval_token": approval_token,
    }


def run(payload: dict[str, Any]) -> dict[str, Any]:
    request = validate_request(payload)
    return {
        "operation_id": "",
        "action_type": request["action_type"],
        "status": "ready",
        "idempotency_key": request["idempotency_key"],
        "evidence_ref": f"action://pending/{request['task_id']}",
        "error_code": "",
        "rollback_of": "",
    }
