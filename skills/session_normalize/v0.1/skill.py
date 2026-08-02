"""SessionNormalize v0.1 - shared multi-channel event normalization."""

from __future__ import annotations

from typing import Any

from orchestrator.models.session_event import normalize_session_event


def run(payload: dict[str, Any]) -> dict[str, Any]:
    raw_event = payload.get("raw_event") or {}
    if not isinstance(raw_event, dict):
        raise ValueError("raw_event 必须是对象")
    channel = str(payload.get("channel") or "").strip()
    profile_id = str(payload.get("profile_id") or "").strip()
    if not channel or not profile_id:
        raise ValueError("channel 和 profile_id 不能为空")
    return normalize_session_event(channel, profile_id, raw_event)
