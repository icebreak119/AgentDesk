"""No-op aggregate UI notifications for the standalone AgentDesk runtime."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify_douyin_conversation_message_changed(
    profile_id: str,
    conversation_id: str,
    *,
    inbound: bool = True,
) -> None:
    logger.debug(
        "standalone aggregate notify skipped: profile_id=%s conversation_id=%s inbound=%s",
        profile_id,
        conversation_id,
        inbound,
    )
