"""ChannelIngress Worker — 会话归一。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")


def normalize(channel: str, profile_id: str, raw_event: dict[str, Any]) -> dict[str, Any]:
    content = str(raw_event.get("text") or raw_event.get("content") or "").strip()
    session_id = str(
        raw_event.get("conversation_id")
        or raw_event.get("session_id")
        or ""
    )
    customer_ref = str(raw_event.get("sender_name") or raw_event.get("customer_ref") or "客户")
    dedupe_src = f"{profile_id}:{session_id}:{content}"
    return {
        "channel": channel,
        "profile_id": profile_id,
        "session_id": session_id,
        "customer_ref": customer_ref,
        "content": content,
        "content_type": str(raw_event.get("content_type") or "text"),
        "ts": str(raw_event.get("ts") or _now_iso()),
        "dedupe_key": hashlib.sha256(dedupe_src.encode("utf-8")).hexdigest()[:24],
    }
