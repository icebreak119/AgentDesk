"""ActVerify Worker — 发送与核验（默认 mock，可选 live）。"""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from orchestrator.models.task_context import TaskContext

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILLS = _REPO_ROOT / "skills"


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
