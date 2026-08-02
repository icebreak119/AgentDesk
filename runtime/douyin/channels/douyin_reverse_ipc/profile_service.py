"""Account / conversation profile helpers (nickname + avatar)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from channels.douyin_reverse_ipc.errors import RpcError
from channels.douyin_reverse_ipc.query_service import _connect, _require_account_code


def peer_uid_from_conversation_id(conversation_id: str) -> str:
    cid = str(conversation_id or "").strip()
    parts = cid.split(":")
    if len(parts) >= 4 and parts[-1].isdigit():
        return parts[-1]
    return ""


def _row_profile(row: Optional[sqlite3.Row]) -> dict[str, str]:
    if row is None:
        return {
            "display_name": "",
            "nickname": "",
            "avatar_url": "",
            "avatar_local_path": "",
        }
    display = str(row["display_name"] or "").strip()
    return {
        "display_name": display,
        "nickname": display,
        "avatar_url": str(row["avatar_url"] or "").strip(),
        "avatar_local_path": str(row["avatar_local_path"] or "").strip(),
    }


def _optional_profile_row(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> Optional[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None


def get_account_profile(db_path: str, account_code: str) -> dict[str, Any]:
    code = _require_account_code(account_code)
    conn = _connect(db_path)
    try:
        account_row = conn.execute(
            "SELECT account_code, nickname, douyin_uid FROM im_accounts WHERE account_code = ? LIMIT 1",
            (code,),
        ).fetchone()
        self_row = _optional_profile_row(
            conn,
            """
            SELECT display_name, avatar_url, avatar_local_path
            FROM conversation_profiles
            WHERE profile_id = ? AND IFNULL(is_self, 0) = 1
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (code,),
        )
    finally:
        conn.close()

    nickname = ""
    douyin_uid = ""
    if account_row is not None:
        nickname = str(account_row["nickname"] or "").strip()
        douyin_uid = str(account_row["douyin_uid"] or "").strip()

    profile = _row_profile(self_row)
    display_name = profile["display_name"] or nickname or code
    avatar_api = f"/accounts/{quote(code, safe='')}/profile/avatar"
    return {
        "account_code": code,
        "nickname": nickname or display_name,
        "display_name": display_name,
        "douyin_uid": douyin_uid,
        "avatar_url": profile["avatar_url"],
        "avatar_local_path": profile["avatar_local_path"],
        "avatar_api": avatar_api,
    }


def get_conversation_profile(db_path: str, account_code: str, conversation_id: str) -> dict[str, Any]:
    code = _require_account_code(account_code)
    cid = str(conversation_id or "").strip()
    if not cid:
        raise RpcError("peer_required", "conversation_id is required")

    conn = _connect(db_path)
    try:
        row = _optional_profile_row(
            conn,
            """
            SELECT conversation_id, display_name, avatar_url, avatar_local_path, updated_at
            FROM conversation_profiles
            WHERE profile_id = ? AND conversation_id = ? AND IFNULL(is_self, 0) = 0
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (code, cid),
        )
    finally:
        conn.close()

    profile = _row_profile(row)
    display_name = profile["display_name"] or cid
    avatar_api = f"/accounts/{quote(code, safe='')}/conversations/{quote(cid, safe='')}/avatar"
    return {
        "conversation_id": cid,
        "display_name": display_name,
        "peer_uid": peer_uid_from_conversation_id(cid),
        "avatar_url": profile["avatar_url"],
        "avatar_local_path": profile["avatar_local_path"],
        "avatar_api": avatar_api,
        "updated_at": str(row["updated_at"] or "") if row is not None else "",
    }


def resolve_avatar_file(db_path: str, local_path: str) -> Path:
    raw = str(local_path or "").strip()
    if not raw:
        raise RpcError("avatar_not_found", "头像文件不存在")

    db_parent = Path(db_path).expanduser().resolve().parent
    allowed_roots = [
        (db_parent / "avatars").resolve(),
        db_parent.resolve(),
    ]
    target = Path(raw).expanduser().resolve()
    if not target.is_file():
        raise RpcError("avatar_not_found", "头像文件不存在")
    if not any(str(target).startswith(str(root)) for root in allowed_roots):
        raise RpcError("avatar_forbidden", "头像路径不在允许范围内")
    return target


def resolve_avatar_target(
    db_path: str,
    account_code: str,
    *,
    conversation_id: str = "",
    self_profile: bool = False,
) -> tuple[str, str]:
    """Return (kind, value) where kind is 'file' or 'url'."""
    if self_profile:
        profile = get_account_profile(db_path, account_code)
    else:
        profile = get_conversation_profile(db_path, account_code, conversation_id)

    local_path = str(profile.get("avatar_local_path") or "").strip()
    if local_path:
        return "file", str(resolve_avatar_file(db_path, local_path))

    avatar_url = str(profile.get("avatar_url") or "").strip()
    if avatar_url:
        return "url", avatar_url

    raise RpcError("avatar_not_found", "尚未获取到头像，请确认账号已启动并完成资料同步")
