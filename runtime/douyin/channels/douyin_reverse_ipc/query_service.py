"""Read helpers for conversations/messages (account_code scoped)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path
from channels.douyin_reverse_ipc.errors import RpcError


def _peer_uid_from_conversation_id(conversation_id: str) -> str:
    cid = str(conversation_id or "").strip()
    parts = cid.split(":")
    if len(parts) >= 4 and parts[-1].isdigit():
        return parts[-1]
    return ""


def _require_account_code(account_code: str) -> str:
    code = str(account_code or "").strip()
    if not code:
        raise RpcError("account_required", "account_code is required")
    return code


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    conn.row_factory = sqlite3.Row
    return conn


def get_conversations(db_path: str, account_code: str, limit: int = 50) -> dict[str, Any]:
    code = _require_account_code(account_code)
    ensure_reverse_runtime_on_path()
    from utils.im_message_store import ensure_message_tables

    ensure_message_tables(db_path)
    lim = max(1, min(int(limit or 50), 500))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT conversation_id, display_name, avatar_url, avatar_local_path,
                   conversation_short_id, updated_at
            FROM conversation_profiles
            WHERE profile_id = ? AND IFNULL(is_self, 0) = 0
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (code, lim),
        ).fetchall()
    finally:
        conn.close()
    conversations = []
    for row in rows:
        cid = row["conversation_id"] or ""
        conversations.append(
            {
                "conversation_id": cid,
                "display_name": row["display_name"] or "",
                "peer_uid": _peer_uid_from_conversation_id(cid),
                "avatar_url": row["avatar_url"] or "",
                "avatar_local_path": row["avatar_local_path"] or "",
                "avatar_api": (
                    f"/accounts/{quote(code, safe='')}/conversations/{quote(cid, safe='')}/avatar"
                    if cid
                    else ""
                ),
                "conversation_short_id": row["conversation_short_id"] or "",
                "updated_at": row["updated_at"] or "",
            }
        )
    return {"conversations": conversations}


def get_messages(
    db_path: str,
    account_code: str,
    conversation_id: str,
    *,
    after_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    code = _require_account_code(account_code)
    cid = str(conversation_id or "").strip()
    if not cid:
        raise RpcError("peer_required", "conversation_id is required")
    ensure_reverse_runtime_on_path()
    from utils.im_message_store import ensure_message_tables

    ensure_message_tables(db_path)
    lim = max(1, min(int(limit or 50), 500))
    after = str(after_id or "").strip()
    conn = _connect(db_path)
    try:
        if after:
            rows = conn.execute(
                """
                SELECT msg_id, direction, msg_type, content, media_url, created_at, id
                FROM messages
                WHERE account_profile_id = ?
                  AND conversation_id = ?
                  AND id > (
                      SELECT IFNULL(MAX(id), 0) FROM messages
                      WHERE account_profile_id = ? AND msg_id = ?
                  )
                ORDER BY id ASC
                LIMIT ?
                """,
                (code, cid, code, after, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT msg_id, direction, msg_type, content, media_url, created_at, id
                FROM messages
                WHERE account_profile_id = ? AND conversation_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (code, cid, lim),
            ).fetchall()
    finally:
        conn.close()
    messages = []
    for row in rows:
        messages.append(
            {
                "msg_id": row["msg_id"] or "",
                "direction": row["direction"] or "",
                "msg_type": row["msg_type"] or "text",
                "content": row["content"] or "",
                "media_url": row["media_url"] or "",
                "created_at": row["created_at"] or "",
            }
        )
    return {"messages": messages}


def get_messages_after_id(
    db_path: str,
    account_code: str,
    conversation_id: str,
    *,
    after_id: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    return get_messages(
        db_path,
        account_code,
        conversation_id,
        after_id=after_id,
        limit=limit,
    )
