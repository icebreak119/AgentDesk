"""Canonical customer-session events used by every channel adapter."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Any

_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _first_text(source: dict[str, Any], *names: str) -> str:
    for name in names:
        value = source.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_content(content: str) -> str:
    return re.sub(r"\s+", "", content).lower()


def _time_window(ts: str) -> str:
    """Return a deterministic five-minute bucket for duplicate detection."""
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_TZ)
        return f"5m:{int(parsed.timestamp()) // 300}"
    except ValueError:
        return f"raw:{ts[:16]}"


def normalize_session_event(channel: str, profile_id: str, raw_event: dict[str, Any]) -> dict[str, Any]:
    """Translate a channel payload into a privacy-conscious SessionEvent.

    The dedupe key intentionally excludes the channel and profile. A customer
    repeating the same content across Douyin and WeCom in the same time window
    should form one customer task, not two competing replies.
    """
    channel_name = str(channel or "unknown").strip().lower()
    content = _first_text(raw_event, "text", "content", "transcript", "message")
    session_id = _first_text(raw_event, "conversation_id", "session_id", "chat_id", "thread_id")
    customer_ref = _first_text(raw_event, "sender_name", "customer_ref", "display_name") or "客户"
    customer_source = _first_text(
        raw_event,
        "customer_id",
        "external_customer_id",
        "external_userid",
        "sender_id",
        "peer_uid",
        "phone_hash",
        "email_hash",
    ) or customer_ref
    source_event_id = _first_text(raw_event, "event_id", "message_id", "msgid", "client_msg_id")
    ts = _first_text(raw_event, "ts", "timestamp", "created_at") or _now_iso()
    canonical_customer_id = hashlib.sha256(customer_source.encode("utf-8")).hexdigest()[:20]
    content_fingerprint = hashlib.sha256(_normalize_content(content).encode("utf-8")).hexdigest()[:20]
    dedupe_src = f"{canonical_customer_id}:{content_fingerprint}:{_time_window(ts)}"

    return {
        "channel": channel_name,
        "profile_id": str(profile_id),
        "session_id": session_id,
        "source_event_id": source_event_id or f"{channel_name}:{session_id}:{ts}",
        "customer_ref": customer_ref,
        "canonical_customer_id": canonical_customer_id,
        "content": content,
        "content_type": _first_text(raw_event, "content_type", "msg_type", "type") or "text",
        "ts": ts,
        "dedupe_key": hashlib.sha256(dedupe_src.encode("utf-8")).hexdigest()[:24],
        "dedupe_window": _time_window(ts),
        "requested_action": "refund" if "退款" in content else "",
    }
