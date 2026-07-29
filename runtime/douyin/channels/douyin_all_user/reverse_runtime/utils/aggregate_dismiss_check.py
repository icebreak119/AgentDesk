"""子进程可读：聚合侧栏「结束会话」状态（aggregate_chat.db）。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Set


def _aggregate_chat_db_path() -> Path:
    root = str(os.environ.get("YUNDUO_PRIVATE_DATA_ROOT") or "").strip()
    if root:
        return Path(root) / "aggregate" / "aggregate_chat.db"
    return Path("C:/YunduoPrivate/data/aggregate/aggregate_chat.db")


def _is_dismissed_key(key: str) -> bool:
    agg_key = (key or "").strip()
    if not agg_key:
        return False
    db_path = _aggregate_chat_db_path()
    if not db_path.is_file():
        return False
    try:
        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM aggregate_dismissed_conversations
                WHERE conversation_key = ?
                LIMIT 1
                """,
                (agg_key,),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _douyin_aggregate_keys(profile_id: str, conversation_id: str) -> Set[str]:
    pid = (profile_id or "").strip()
    conv = (conversation_id or "").strip()
    keys: Set[str] = set()
    if not pid or not conv:
        return keys

    def _add(variant: str) -> None:
        variant = (variant or "").strip()
        if not variant:
            return
        keys.add(f"douyin:{pid}:{variant}")

    _add(conv)
    try:
        from channels.douyin_all_user import douyin_message_store

        name_map, _ = douyin_message_store.load_conversation_profile_aliases(pid)
    except Exception:
        name_map = {}

    canonical = str((name_map or {}).get(conv) or conv).strip()
    for variant in {conv, canonical}:
        _add(variant)
    for alias, canon in (name_map or {}).items():
        alias_s = str(alias or "").strip()
        canon_s = str(canon or "").strip()
        if conv not in (alias_s, canon_s) and canonical not in (alias_s, canon_s):
            continue
        for variant in {alias_s, canon_s}:
            _add(variant)
    return keys


def is_douyin_conversation_dismissed(profile_id: str, conversation_id: str) -> bool:
    for key in _douyin_aggregate_keys(profile_id, conversation_id):
        if _is_dismissed_key(key):
            return True
    return False


def douyin_aggregate_key_variants(profile_id: str, conversation_id: str) -> Set[str]:
    """同一抖音会话在聚合侧栏可能使用的 key 变体（含别名）。"""
    return set(_douyin_aggregate_keys(profile_id, conversation_id))
