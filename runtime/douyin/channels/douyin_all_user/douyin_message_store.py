"""Compatibility helpers backed by the standalone reverse_runtime SQLite DB."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict, Tuple

_RUNTIME_ROOT = Path(__file__).resolve().parent / "reverse_runtime"


def message_db_path() -> str:
    env = str(os.environ.get("DY_IM_MESSAGE_DB_PATH") or "").strip()
    if env:
        return str(Path(env).expanduser().resolve())
    return str((_RUNTIME_ROOT / "_douyin_im_accounts.db").resolve())


def load_conversation_profile_aliases(profile_id: str, *, db_path: str = "") -> Tuple[Dict[str, str], Dict[str, str]]:
    pid = str(profile_id or "").strip()
    path = str(db_path or message_db_path() or "").strip()
    if not pid or not path:
        return {}, {}
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                """
                SELECT conversation_id, display_name
                FROM conversation_profiles
                WHERE profile_id = ?
                  AND IFNULL(conversation_id, '') != ''
                  AND IFNULL(display_name, '') != ''
                """,
                (pid,),
            ).fetchall()
    except Exception:
        return {}, {}

    name_to_conversation: Dict[str, str] = {}
    conversation_to_name: Dict[str, str] = {}
    for conv_id, display_name in rows:
        conv = str(conv_id or "").strip()
        name = str(display_name or "").strip()
        if not conv or not name:
            continue
        conversation_to_name[conv] = name
        name_to_conversation[name] = conv
    return name_to_conversation, conversation_to_name


def resolve_peer_user_id(conversation_id: str, peer_user_id: str = "", self_uid: str = "") -> str:
    peer = str(peer_user_id or "").strip()
    if peer:
        return peer
    parts = str(conversation_id or "").strip().split(":")
    for item in reversed(parts):
        if item and item != str(self_uid or "").strip():
            return item
    return ""
