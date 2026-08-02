"""Privacy helpers for Agent handoff and audit payloads."""

from __future__ import annotations

from typing import Any


_SENSITIVE_KEYS = frozenset(
    {
        "approval_token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "password",
        "cookie",
        "credentials",
        "raw_event",
        "conversation_id",
        "customer_id",
        "customer_ref",
        "canonical_customer_id",
        "text",
        "content",
        "transcript",
        "message",
        "sender_name",
        "display_name",
        "customer_feedback",
        "draft_text",
        "actual_content",
        "feedback_summary",
        "knowledge_snippet",
    }
)


def redact_for_transport(value: Any) -> Any:
    """Recursively remove credentials and customer content from wire payloads."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else redact_for_transport(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_for_transport(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_transport(item) for item in value]
    return value
