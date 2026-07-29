"""Douyin IM 消息 sender / conversation_id 与抖音 uid 的对应关系。"""

from __future__ import annotations


def conversation_participants(conversation_id: str) -> list[str]:
    parts = str(conversation_id or "").strip().split(":")
    if len(parts) >= 4:
        return [str(p).strip() for p in parts[-2:] if str(p).strip()]
    return []


def resolve_self_participant(
    conversation_id: str,
    *,
    douyin_uid: str = "",
    cached_im_uid: str = "",
) -> str:
    """在会话双方 ID 中识别己方 IM uid（不假定固定在第 3 段）。"""
    participants = conversation_participants(conversation_id)
    uid = str(douyin_uid or "").strip()
    cached = str(cached_im_uid or "").strip()
    if uid and uid in participants:
        return uid
    if cached and cached in participants:
        return cached
    return ""


def resolve_peer_participant(
    conversation_id: str,
    sender_id,
    *,
    douyin_uid: str = "",
    cached_im_uid: str = "",
) -> str:
    sender = str(sender_id or "").strip()
    participants = conversation_participants(conversation_id)
    self_p = resolve_self_participant(
        conversation_id,
        douyin_uid=douyin_uid,
        cached_im_uid=cached_im_uid,
    )
    if participants:
        for participant in participants:
            if self_p and participant == self_p:
                continue
            return participant
    return sender


def is_im_self_sender(
    sender_id,
    conversation_id: str = "",
    *,
    douyin_uid: str = "",
    cached_im_uid: str = "",
    known_self_senders: frozenset | set = frozenset(),
) -> bool:
    sender = str(sender_id or "").strip()
    if not sender:
        return False
    uid = str(douyin_uid or "").strip()
    if uid and sender == uid:
        return True
    cached = str(cached_im_uid or "").strip()
    if cached and sender == cached:
        return True
    if sender in known_self_senders:
        return True
    self_p = resolve_self_participant(
        conversation_id,
        douyin_uid=uid,
        cached_im_uid=cached,
    )
    return bool(self_p and sender == self_p)
