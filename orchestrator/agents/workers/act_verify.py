"""ActVerify Worker — 发送与核验（默认 mock，可选 live）。"""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from orchestrator.models.business_action import HttpBusinessActionAdapter, JsonlBusinessActionAdapter
from orchestrator.models.task_context import TaskContext

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILLS = _REPO_ROOT / "skills"
DEFAULT_BUSINESS_ACTION_PATH = _REPO_ROOT / "orchestrator" / "output" / "business_actions.jsonl"


def _load_skill(relative: str):
    path = _SKILLS / relative
    spec = importlib.util.spec_from_file_location(f"agentdesk_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 Skill: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")


def _mock_send(ctx: TaskContext) -> dict[str, Any]:
    draft = ctx.reply_draft or {}
    return {
        "send_id": f"mock_{ctx.task_id}",
        "status": "ok",
        "receipt_raw": {"mode": "mock", "text": draft.get("draft_text", "")},
        "ts": _now_iso(),
        "mode": "mock",
    }


def _live_send(ctx: TaskContext, base_url: str) -> dict[str, Any]:
    draft = ctx.reply_draft or {}
    text = str(draft.get("draft_text") or "")
    session_ref = str((ctx.session_event or {}).get("session_id") or ctx.session_id)
    body = {
        "text": text,
        "conversation_id": session_ref,
        "peer_uid": "",
        "client_msg_id": f"{ctx.task_id}:send:1",
        "is_ai_reply": True,
    }
    url = f"{base_url.rstrip('/')}/accounts/{ctx.profile_id}/send/text"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "send_id": "",
            "status": "failed",
            "receipt_raw": {"http_status": exc.code, "detail": detail},
            "ts": _now_iso(),
            "mode": "live",
        }
    except OSError as exc:
        return {
            "send_id": "",
            "status": "failed",
            "receipt_raw": {"error": str(exc)},
            "ts": _now_iso(),
            "mode": "live",
        }

    data = payload.get("data") if isinstance(payload, dict) else payload
    return {
        "send_id": str((data or {}).get("client_msg_id") or body["client_msg_id"]),
        "status": "ok" if payload.get("ok", True) else "failed",
        "receipt_raw": payload,
        "ts": _now_iso(),
        "mode": "live",
    }


def send(ctx: TaskContext, *, live: bool = False, base_url: str = "http://127.0.0.1:8765") -> dict[str, Any]:
    if live or ctx.mode == "live":
        return _live_send(ctx, base_url)
    return _mock_send(ctx)


def verify(ctx: TaskContext) -> dict[str, Any]:
    draft = ctx.reply_draft or {}
    expected = str(draft.get("draft_text") or "")
    receipt = ctx.send_receipt or {}
    module = _load_skill("outcome_verify/v0.1/skill.py")
    return module.run(
        {
            "expected_content": expected,
            "send_receipt": receipt,
            "session_ref": str((ctx.session_event or {}).get("session_id") or ctx.session_id),
            "task_id": ctx.task_id,
        }
    )


def confirm_customer(ctx: TaskContext) -> dict[str, Any]:
    module = _load_skill("customer_confirm/v0.1/skill.py")
    return module.run(
        {
            "task_id": ctx.task_id,
            "customer_feedback": (ctx.raw_event or {}).get("customer_feedback"),
        }
    )


def business_action_request(ctx: TaskContext) -> dict[str, Any]:
    """Build a redacted, approval-scoped refund request for the business adapter."""
    raw = ctx.raw_event or {}
    return {
        "task_id": ctx.task_id,
        "profile_id": ctx.profile_id,
        "action_type": str((ctx.triage_result or {}).get("requested_action") or "refund"),
        "order_id": raw.get("order_id"),
        "amount": raw.get("amount"),
        "currency": raw.get("currency") or "CNY",
        "reason": raw.get("refund_reason") or "客户申请退款",
        "idempotency_key": f"{ctx.task_id}:business_action:1",
        "approval_token": ctx.approval_token,
    }


def _failed_business_action(
    ctx: TaskContext,
    error: Exception | str,
    *,
    operation_id: str = "",
    rollback_of: str = "",
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "action_type": str((ctx.triage_result or {}).get("requested_action") or "refund"),
        "status": "failed",
        "idempotency_key": f"{ctx.task_id}:business_action:1",
        "evidence_ref": f"action://failed/{ctx.task_id}",
        "error_code": error if isinstance(error, str) else type(error).__name__,
        "rollback_of": rollback_of,
    }


def _business_adapter(
    *,
    backend: str,
    path: Path,
    enterprise_base_url: str,
):
    if backend == "http":
        return HttpBusinessActionAdapter(enterprise_base_url)
    if backend != "jsonl":
        raise ValueError(f"unsupported_business_action_backend:{backend}")
    return JsonlBusinessActionAdapter(path)


def query_business_order(
    ctx: TaskContext,
    *,
    backend: str = "jsonl",
    path: Path = DEFAULT_BUSINESS_ACTION_PATH,
    enterprise_base_url: str = "http://127.0.0.1:8770",
) -> dict[str, Any]:
    request = business_action_request(ctx)
    if backend == "http":
        return _business_adapter(
            backend=backend,
            path=path,
            enterprise_base_url=enterprise_base_url,
        ).query_order(request)
    return {
        "order_id": request["order_id"],
        "amount": str(request["amount"]),
        "currency": request["currency"],
        "status": "available",
        "evidence_ref": f"order://mock/{request['order_id']}",
    }


def execute_business_action(
    ctx: TaskContext,
    *,
    path: Path = DEFAULT_BUSINESS_ACTION_PATH,
    backend: str = "jsonl",
    enterprise_base_url: str = "http://127.0.0.1:8770",
) -> dict[str, Any]:
    if ctx.state not in {"approved", "acting"} or not ctx.need_approval or not ctx.approval_token:
        return _failed_business_action(ctx, "approval_required")
    try:
        adapter = _business_adapter(
            backend=backend,
            path=path,
            enterprise_base_url=enterprise_base_url,
        )
        return adapter.execute(business_action_request(ctx))
    except (TypeError, ValueError) as exc:
        return _failed_business_action(ctx, exc)


def verify_business_action(
    ctx: TaskContext,
    receipt: dict[str, Any],
    *,
    path: Path = DEFAULT_BUSINESS_ACTION_PATH,
    inject_failure: bool = False,
    backend: str = "jsonl",
    enterprise_base_url: str = "http://127.0.0.1:8770",
) -> dict[str, Any]:
    try:
        adapter = _business_adapter(
            backend=backend,
            path=path,
            enterprise_base_url=enterprise_base_url,
        )
        return adapter.verify(business_action_request(ctx), receipt, inject_failure=inject_failure)
    except (TypeError, ValueError) as exc:
        return _failed_business_action(ctx, exc)


def rollback_business_action(
    ctx: TaskContext,
    receipt: dict[str, Any],
    *,
    path: Path = DEFAULT_BUSINESS_ACTION_PATH,
    backend: str = "jsonl",
    enterprise_base_url: str = "http://127.0.0.1:8770",
    inject_failure: bool = False,
) -> dict[str, Any]:
    if inject_failure:
        return _failed_business_action(
            ctx,
            "rollback_failed",
            operation_id="",
            rollback_of=str(receipt.get("operation_id") or ""),
        )
    try:
        adapter = _business_adapter(
            backend=backend,
            path=path,
            enterprise_base_url=enterprise_base_url,
        )
        return adapter.rollback(business_action_request(ctx), receipt)
    except (TypeError, ValueError) as exc:
        return _failed_business_action(ctx, exc)


def build_business_action_notification(ctx: TaskContext) -> None:
    """Replace the approval-only draft after a verified mock refund."""
    draft = dict(ctx.reply_draft or {})
    customer_ref = str((ctx.session_event or {}).get("customer_ref") or "客户")
    operation_id = str((ctx.business_action or {}).get("operation_id") or "")
    draft["draft_text"] = (
        f"{customer_ref}您好，您的退款申请已完成处理，操作编号为 {operation_id}。"
        "如仍有问题，请继续回复，我们会为您跟进。"
    )
    draft["action_type"] = "business_action_notification"
    ctx.reply_draft = draft
