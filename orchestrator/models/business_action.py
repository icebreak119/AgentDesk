"""Auditable JSONL mock for enterprise business actions."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_TZ = timezone(timedelta(hours=8))
_FILE_LOCK = threading.RLock()
_SKILL_PATH = Path(__file__).resolve().parents[2] / "skills" / "business_action" / "v0.1" / "skill.py"


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("agentdesk_business_action", _SKILL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 BusinessAction Skill: {_SKILL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_request(payload)


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _fingerprint(request: dict[str, Any]) -> str:
    stable = {key: request[key] for key in ("action_type", "order_id", "amount", "currency", "reason")}
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class JsonlBusinessActionAdapter:
    """Single-process initial-stage adapter; replaceable by an ERP client later."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if isinstance(record, dict):
                    records.append(record)
        return records

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _operation_id(request: dict[str, Any]) -> str:
        seed = f"{request['profile_id']}:{request['idempotency_key']}"
        return f"refund_op_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate_request(payload)
        fingerprint = _fingerprint(request)
        with _FILE_LOCK:
            existing = [
                record
                for record in self._records()
                if record.get("profile_id") == request["profile_id"]
                and record.get("idempotency_key") == request["idempotency_key"]
            ]
            if existing:
                previous = existing[0]
                if previous.get("request_fingerprint") != fingerprint:
                    return self._result(
                        request,
                        status="failed",
                        error_code="idempotency_conflict",
                        operation_id=str(previous.get("operation_id") or ""),
                    )
                return self._result(
                    request,
                    status=str(previous.get("status") or "executed"),
                    operation_id=str(previous.get("operation_id") or ""),
                    evidence_ref=str(previous.get("evidence_ref") or ""),
                )

            operation_id = self._operation_id(request)
            record = {
                "ts": _now_iso(),
                "profile_id": request["profile_id"],
                "task_id": request["task_id"],
                "action_type": request["action_type"],
                "order_id": request["order_id"],
                "amount": request["amount"],
                "currency": request["currency"],
                "reason": request["reason"],
                "idempotency_key": request["idempotency_key"],
                "request_fingerprint": fingerprint,
                "operation_id": operation_id,
                "status": "executed",
                "evidence_ref": f"action://business/{operation_id}",
                "error_code": "",
                "rollback_of": "",
                "provider": "jsonl-mock-refund-system",
            }
            self._append(record)
            return self._result(request, status="executed", operation_id=operation_id, evidence_ref=record["evidence_ref"])

    def verify(self, payload: dict[str, Any], receipt: dict[str, Any], *, inject_failure: bool = False) -> dict[str, Any]:
        request = _validate_request(payload)
        operation_id = str(receipt.get("operation_id") or "")
        if inject_failure:
            return self._result(request, status="failed", operation_id=operation_id, error_code="verification_mismatch")
        if receipt.get("status") != "executed" or not operation_id:
            return self._result(request, status="failed", operation_id=operation_id, error_code="action_not_executed")
        fingerprint = _fingerprint(request)
        with _FILE_LOCK:
            record = next(
                (item for item in self._records() if item.get("operation_id") == operation_id),
                None,
            )
        if not record:
            return self._result(request, status="failed", operation_id=operation_id, error_code="action_not_found")
        if record.get("request_fingerprint") != fingerprint or record.get("action_type") != request["action_type"]:
            return self._result(request, status="failed", operation_id=operation_id, error_code="action_request_mismatch")
        return self._result(
            request,
            status="verified",
            operation_id=operation_id,
            evidence_ref=f"action://verify/{operation_id}",
        )

    def rollback(self, payload: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        request = _validate_request(payload)
        operation_id = str(receipt.get("operation_id") or "")
        if not operation_id:
            return self._result(request, status="failed", error_code="rollback_target_missing")
        rollback_id = f"rollback_{operation_id}"
        with _FILE_LOCK:
            for record in self._records():
                if record.get("operation_id") == rollback_id:
                    return self._result(
                        request,
                        status=str(record.get("status") or "rolled_back"),
                        operation_id=rollback_id,
                        evidence_ref=str(record.get("evidence_ref") or ""),
                        rollback_of=operation_id,
                    )
            record = {
                "ts": _now_iso(),
                "profile_id": request["profile_id"],
                "task_id": request["task_id"],
                "action_type": "refund_rollback",
                "idempotency_key": f"{request['idempotency_key']}:rollback",
                "request_fingerprint": _fingerprint(request),
                "operation_id": rollback_id,
                "status": "rolled_back",
                "evidence_ref": f"action://rollback/{operation_id}",
                "error_code": "",
                "rollback_of": operation_id,
                "provider": "jsonl-mock-refund-system",
            }
            self._append(record)
            return self._result(
                request,
                status="rolled_back",
                operation_id=rollback_id,
                evidence_ref=record["evidence_ref"],
                rollback_of=operation_id,
            )

    @staticmethod
    def _result(
        request: dict[str, Any],
        *,
        status: str,
        operation_id: str = "",
        evidence_ref: str = "",
        error_code: str = "",
        rollback_of: str = "",
    ) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "action_type": request["action_type"],
            "status": status,
            "idempotency_key": request["idempotency_key"],
            "evidence_ref": evidence_ref,
            "error_code": error_code,
            "rollback_of": rollback_of,
        }


class HttpBusinessActionAdapter:
    """HTTP client for the replaceable enterprise business-action service.

    The service owns order state and audit evidence. This client only translates
    the stable BusinessAction contract into order/refund API calls.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8770", *, timeout: float = 10.0) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = {}
            error = detail.get("error") if isinstance(detail, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            raise ValueError(str(code or f"enterprise_http_{exc.code}")) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("enterprise_service_unavailable") from exc

        if isinstance(data, dict) and data.get("ok") is False:
            error = data.get("error") or {}
            raise ValueError(str(error.get("code") or "enterprise_request_failed"))
        result = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(result, dict):
            raise ValueError("enterprise_invalid_response")
        return result

    def query_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate_request(payload)
        order_id = urllib.parse.quote(request["order_id"], safe="")
        profile_id = urllib.parse.quote(request["profile_id"], safe="")
        return self._request(
            "GET",
            f"/enterprise/orders/{order_id}?profile_id={profile_id}",
        )

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate_request(payload)
        application = self._request("POST", "/enterprise/refunds", request)
        operation_id = str(application.get("operation_id") or "")
        if application.get("status") == "failed" or not operation_id:
            return self._result(request, status="failed", **self._receipt_fields(application))

        execution = self._request(
            "POST",
            f"/enterprise/refunds/{urllib.parse.quote(operation_id, safe='')}/execute",
            {
                "profile_id": request["profile_id"],
                "idempotency_key": request["idempotency_key"],
                "approval_token": request["approval_token"],
            },
        )
        return self._result(
            request,
            status=str(execution.get("status") or "failed"),
            **self._receipt_fields(execution, operation_id=operation_id),
        )

    def verify(
        self,
        payload: dict[str, Any],
        receipt: dict[str, Any],
        *,
        inject_failure: bool = False,
    ) -> dict[str, Any]:
        request = _validate_request(payload)
        operation_id = str(receipt.get("operation_id") or "")
        if inject_failure:
            return self._result(
                request,
                status="failed",
                operation_id=operation_id,
                error_code="verification_mismatch",
            )
        if receipt.get("status") != "executed" or not operation_id:
            return self._result(
                request,
                status="failed",
                operation_id=operation_id,
                error_code="action_not_executed",
            )
        operation = self._request(
            "GET",
            f"/enterprise/operations/{urllib.parse.quote(operation_id, safe='')}"
            f"?profile_id={urllib.parse.quote(request['profile_id'], safe='')}",
        )
        expected = {
            "order_id": request["order_id"],
            "amount": request["amount"],
            "currency": request["currency"],
            "status": "executed",
        }
        if any(str(operation.get(key)) != value for key, value in expected.items()):
            return self._result(
                request,
                status="failed",
                operation_id=operation_id,
                error_code="action_response_mismatch",
            )
        return self._result(
            request,
            status="verified",
            operation_id=operation_id,
            evidence_ref=str(operation.get("evidence_ref") or f"action://enterprise/verify/{operation_id}"),
        )

    def rollback(self, payload: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
        request = _validate_request(payload)
        operation_id = str(receipt.get("operation_id") or "")
        if not operation_id:
            return self._result(request, status="failed", error_code="rollback_target_missing")
        result = self._request(
            "POST",
            f"/enterprise/refunds/{urllib.parse.quote(operation_id, safe='')}/rollback",
            {
                "profile_id": request["profile_id"],
                "idempotency_key": request["idempotency_key"],
                "approval_token": request["approval_token"],
            },
        )
        return self._result(
            request,
            status=str(result.get("status") or "failed"),
            **self._receipt_fields(result, operation_id=str(result.get("operation_id") or "")),
            rollback_of=str(result.get("rollback_of") or operation_id),
        )

    @staticmethod
    def _receipt_fields(
        result: dict[str, Any],
        *,
        operation_id: str = "",
    ) -> dict[str, Any]:
        return {
            "operation_id": str(result.get("operation_id") or operation_id),
            "evidence_ref": str(result.get("evidence_ref") or ""),
            "error_code": str(result.get("error_code") or ""),
        }

    @staticmethod
    def _result(
        request: dict[str, Any],
        *,
        status: str,
        operation_id: str = "",
        evidence_ref: str = "",
        error_code: str = "",
        rollback_of: str = "",
    ) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "action_type": request["action_type"],
            "status": status,
            "idempotency_key": request["idempotency_key"],
            "evidence_ref": evidence_ref,
            "error_code": error_code,
            "rollback_of": rollback_of,
        }
