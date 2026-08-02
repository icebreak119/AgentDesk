"""In-memory enterprise order/refund state with append-only evidence."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from orchestrator.models.business_action import _fingerprint, _validate_request

_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


class EnterpriseBusinessStore:
    """Small, deterministic service store suitable for local demonstrations.

    The JSONL file contains operational evidence only. It deliberately omits
    customer names, session text and approval credentials.
    """

    def __init__(self, evidence_path: Path) -> None:
        self.evidence_path = Path(evidence_path)
        self._lock = threading.RLock()
        self.orders: dict[str, dict[str, Any]] = {
            "order-demo-001": {
                "order_id": "order-demo-001",
                "profile_id": "d6a26b9e-demo",
                "amount": "199.00",
                "refundable_amount": "199.00",
                "currency": "CNY",
                "status": "paid",
                "evidence_ref": "order://enterprise/order-demo-001",
            },
            "order-demo-002": {
                "order_id": "order-demo-002",
                "profile_id": "d6a26b9e-demo",
                "amount": "59.90",
                "refundable_amount": "0.00",
                "currency": "CNY",
                "status": "paid",
                "evidence_ref": "order://enterprise/order-demo-002",
            },
        }
        self.operations: dict[str, dict[str, Any]] = {}
        self._load_evidence()

    def _load_evidence(self) -> None:
        if not self.evidence_path.is_file():
            return
        try:
            lines = self.evidence_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            operation_id = str(record.get("operation_id") or "")
            if operation_id and record.get("event") in {
                "refund_requested", "refund_executed", "refund_rolled_back",
            }:
                self.operations[operation_id] = {
                    key: record.get(key)
                    for key in (
                        "operation_id", "profile_id", "order_id", "amount", "currency",
                        "idempotency_key", "request_fingerprint", "status", "evidence_ref",
                        "rollback_of",
                    )
                }

    def _audit(self, record: dict[str, Any]) -> None:
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        safe = {"ts": _now_iso(), **record}
        with self.evidence_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, ensure_ascii=False) + "\n")

    def query_order(self, profile_id: str, order_id: str) -> dict[str, Any]:
        with self._lock:
            order = self.orders.get(order_id)
            if not order or order.get("profile_id") != profile_id:
                raise KeyError("order_not_found")
            self._audit({
                "event": "order_queried",
                "profile_id": profile_id,
                "order_id": order_id,
                "status": "ok",
                "evidence_ref": f"order://query/{order_id}",
            })
            return dict(order)

    def apply_refund(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate_request(payload)
        with self._lock:
            order = self.query_order(request["profile_id"], request["order_id"])
            if request["currency"] != order["currency"]:
                raise ValueError("order_currency_mismatch")
            if Decimal(request["amount"]) > Decimal(order["refundable_amount"]):
                raise ValueError("amount_exceeds_refundable")

            fingerprint = _fingerprint(request)
            existing = next(
                (
                    operation for operation in self.operations.values()
                    if operation.get("profile_id") == request["profile_id"]
                    and operation.get("idempotency_key") == request["idempotency_key"]
                ),
                None,
            )
            if existing:
                if existing.get("request_fingerprint") != fingerprint:
                    raise ValueError("idempotency_conflict")
                return self._action_result(existing)

            operation_id = "refund_op_" + hashlib.sha256(
                f"{request['profile_id']}:{request['idempotency_key']}".encode("utf-8")
            ).hexdigest()[:16]
            operation = {
                "operation_id": operation_id,
                "profile_id": request["profile_id"],
                "order_id": request["order_id"],
                "amount": request["amount"],
                "currency": request["currency"],
                "idempotency_key": request["idempotency_key"],
                "request_fingerprint": fingerprint,
                "status": "refund_requested",
                "evidence_ref": f"action://enterprise/application/{operation_id}",
                "rollback_of": "",
            }
            self.operations[operation_id] = operation
            self._audit({
                "event": "refund_requested",
                **{key: operation[key] for key in (
                    "operation_id", "profile_id", "order_id", "amount", "currency",
                    "idempotency_key", "request_fingerprint", "status", "evidence_ref",
                )},
            })
            return self._action_result(operation)

    def execute_refund(
        self,
        operation_id: str,
        *,
        profile_id: str,
        idempotency_key: str,
        approval_token: str,
    ) -> dict[str, Any]:
        if not str(approval_token or "").strip():
            raise ValueError("approval_required")
        with self._lock:
            operation = self._owned_operation(operation_id, profile_id, idempotency_key)
            if operation["status"] == "executed":
                return self._action_result(operation)
            if operation["status"] == "rolled_back":
                raise ValueError("operation_already_rolled_back")
            operation["status"] = "executed"
            operation["evidence_ref"] = f"action://enterprise/receipt/{operation_id}"
            self._audit({
                "event": "refund_executed",
                **{key: operation[key] for key in (
                    "operation_id", "profile_id", "order_id", "amount", "currency",
                    "idempotency_key", "request_fingerprint", "status", "evidence_ref",
                )},
            })
            return self._action_result(operation)

    def get_operation(self, operation_id: str, profile_id: str) -> dict[str, Any]:
        with self._lock:
            operation = self._owned_operation(operation_id, profile_id)
            return {
                **self._action_result(operation),
                "order_id": operation["order_id"],
                "amount": operation["amount"],
                "currency": operation["currency"],
            }

    def rollback_refund(
        self,
        operation_id: str,
        *,
        profile_id: str,
        idempotency_key: str,
        approval_token: str,
    ) -> dict[str, Any]:
        if not str(approval_token or "").strip():
            raise ValueError("approval_required")
        with self._lock:
            original = self._owned_operation(operation_id, profile_id, idempotency_key)
            rollback_id = f"rollback_{operation_id}"
            existing = self.operations.get(rollback_id)
            if existing:
                return self._action_result(existing, rollback_of=operation_id)
            if original["status"] != "executed":
                raise ValueError("operation_not_executed")
            rollback = {
                "operation_id": rollback_id,
                "profile_id": profile_id,
                "order_id": original["order_id"],
                "amount": original["amount"],
                "currency": original["currency"],
                "idempotency_key": f"{original['idempotency_key']}:rollback",
                "request_fingerprint": original["request_fingerprint"],
                "status": "rolled_back",
                "evidence_ref": f"action://enterprise/rollback/{operation_id}",
                "rollback_of": operation_id,
            }
            self.operations[rollback_id] = rollback
            original["status"] = "rolled_back"
            self._audit({
                "event": "refund_rolled_back",
                **{key: rollback[key] for key in (
                    "operation_id", "profile_id", "order_id", "amount", "currency",
                    "idempotency_key", "request_fingerprint", "status", "evidence_ref",
                    "rollback_of",
                )},
            })
            return self._action_result(rollback, rollback_of=operation_id)

    def _owned_operation(
        self,
        operation_id: str,
        profile_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        operation = self.operations.get(operation_id)
        if not operation or operation.get("profile_id") != profile_id:
            raise KeyError("operation_not_found")
        if idempotency_key and operation.get("idempotency_key") != idempotency_key:
            raise ValueError("idempotency_conflict")
        return operation

    @staticmethod
    def _action_result(
        operation: dict[str, Any],
        *,
        rollback_of: str | None = None,
    ) -> dict[str, Any]:
        return {
            "operation_id": operation["operation_id"],
            "action_type": "refund",
            "status": operation["status"],
            "idempotency_key": operation["idempotency_key"],
            "evidence_ref": operation["evidence_ref"],
            "error_code": "",
            "rollback_of": operation.get("rollback_of") or rollback_of or "",
        }
