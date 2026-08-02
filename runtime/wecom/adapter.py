"""Enterprise WeChat inbound payload adapter."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator.models.session_event import normalize_session_event

_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


class WecomWebhookAdapter:
    """Normalize WeCom callbacks into the same SessionEvent as Douyin."""

    def __init__(self, evidence_path: Path) -> None:
        self.evidence_path = Path(evidence_path)
        self._lock = threading.RLock()

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = dict(payload)
        if not any(source.get(name) for name in ("ts", "timestamp", "created_at")):
            source["ts"] = source.get("create_time") or source.get("createTime") or ""
        profile_id = str(
            source.get("profile_id")
            or source.get("agent_id")
            or source.get("corp_id")
            or "wecom-demo"
        )
        return normalize_session_event("wecom", profile_id, source)

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("webhook_payload_must_be_object")
        event = self.normalize(payload)
        evidence_ref = f"channel://wecom/{event['source_event_id']}"
        with self._lock:
            self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": _now_iso(),
                "event": "wecom_message_normalized",
                "channel": "wecom",
                "profile_id": event["profile_id"],
                "source_event_id": event["source_event_id"],
                "session_id": event["session_id"],
                "canonical_customer_id": event["canonical_customer_id"],
                "dedupe_key": event["dedupe_key"],
                "content_hash": hashlib.sha256(event["content"].encode("utf-8")).hexdigest()[:16],
                "evidence_ref": evidence_ref,
            }
            with self.evidence_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {
            "session_event": event,
            "evidence_ref": evidence_ref,
            "privacy": {
                "stored_content": False,
                "stored_customer_name": False,
                "stored_credentials": False,
            },
        }
