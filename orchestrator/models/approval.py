"""Scoped approval tokens for the local high-risk action demo."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

_TOKEN_VERSION = "ad1"
_MAX_AGE_SECONDS = 15 * 60
_CLOCK_SKEW_SECONDS = 30
_DEFAULT_SECRET = "agentdesk-demo-approval-secret"


def _secret() -> bytes:
    return os.environ.get("AGENTDESK_APPROVAL_SECRET", _DEFAULT_SECRET).encode("utf-8")


def approval_scope(payload: dict[str, Any]) -> str:
    """Return a stable hash for the exact high-risk action being approved."""
    fields = {
        key: str(payload.get(key) or "")
        for key in (
            "task_id",
            "profile_id",
            "action_type",
            "order_id",
            "amount",
            "currency",
            "reason",
            "idempotency_key",
        )
    }
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def issue_approval_token(payload: dict[str, Any], *, issued_at: int | None = None) -> str:
    scope = approval_scope(payload)
    timestamp = int(time.time() if issued_at is None else issued_at)
    body = f"{_TOKEN_VERSION}.{scope}.{timestamp}"
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def validate_approval_token(
    payload: dict[str, Any],
    token: str,
    *,
    now: int | None = None,
) -> bool:
    parts = str(token or "").split(".")
    if len(parts) != 4 or parts[0] != _TOKEN_VERSION:
        return False
    _, token_scope, timestamp_text, signature = parts
    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return False
    current = int(time.time() if now is None else now)
    age = current - timestamp
    if age < -_CLOCK_SKEW_SECONDS or age > _MAX_AGE_SECONDS:
        return False
    body = ".".join(parts[:3])
    expected_signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(token_scope, approval_scope(payload)) and hmac.compare_digest(
        signature,
        expected_signature,
    )


def redact_token(token: str) -> str:
    """Expose only a short fingerprint when a UI needs to show approval state."""
    digest = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    return f"approval://{digest[:16]}"
