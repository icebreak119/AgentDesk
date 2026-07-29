"""子进程可读：聚合侧栏「拉黑」状态（aggregate_chat.db）。"""

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


def _is_blocked_key(key: str) -> bool:
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
                FROM aggregate_blocked_conversations
                WHERE conversation_key = ?
                LIMIT 1
                """,
                (agg_key,),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _douyin_aggregate_keys(profile_id: str, conversation_id: str) -> Set[str]:
    try:
        from channels.douyin_all_user.reverse_runtime.utils.aggregate_dismiss_check import (
            douyin_aggregate_key_variants,
        )

        return set(douyin_aggregate_key_variants(profile_id, conversation_id) or set())
    except Exception:
        pid = (profile_id or "").strip()
        conv = (conversation_id or "").strip()
        if not pid or not conv:
            return set()
        return {f"douyin:{pid}:{conv}"}


def is_douyin_conversation_blocked(profile_id: str, conversation_id: str) -> bool:
    for key in _douyin_aggregate_keys(profile_id, conversation_id):
        if _is_blocked_key(key):
            return True
    return False
