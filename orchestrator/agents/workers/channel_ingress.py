"""ChannelIngress Worker — normalize every channel through one event contract."""

from __future__ import annotations

from typing import Any

from orchestrator.models.session_event import normalize_session_event


def normalize(channel: str, profile_id: str, raw_event: dict[str, Any]) -> dict[str, Any]:
    return normalize_session_event(channel, profile_id, raw_event)
