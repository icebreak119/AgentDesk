"""Run-scoped cross-channel deduplication ledger for the reference orchestrator."""

from __future__ import annotations

from typing import Any


class ConversationLedger:
    """Keep the first task for a canonical customer message and link duplicates."""

    def __init__(self) -> None:
        self._first_tasks: dict[str, dict[str, str]] = {}

    def register(self, task_id: str, session_event: dict[str, Any]) -> dict[str, Any]:
        dedupe_key = str(session_event.get("dedupe_key") or "").strip()
        if not dedupe_key:
            raise ValueError("SessionEvent 缺少 dedupe_key")

        existing = self._first_tasks.get(dedupe_key)
        if existing:
            return {
                "accepted": False,
                "dedupe_key": dedupe_key,
                "duplicate_of_task_id": existing["task_id"],
                "duplicate_of_channel": existing["channel"],
                "reason": "same_customer_content_window",
            }

        self._first_tasks[dedupe_key] = {
            "task_id": task_id,
            "channel": str(session_event.get("channel") or "unknown"),
        }
        return {
            "accepted": True,
            "dedupe_key": dedupe_key,
            "canonical_customer_id": session_event.get("canonical_customer_id"),
            "reason": "first_seen",
        }
