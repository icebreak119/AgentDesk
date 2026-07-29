"""Refresh self / peer nickname and avatar into conversation_profiles."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path
from channels.douyin_reverse_ipc.errors import RpcError
from channels.douyin_reverse_ipc.profile_service import peer_uid_from_conversation_id

logger = logging.getLogger(__name__)


def _is_weak_customer_name(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if text in {"客户", "用户", "未知", "匿名用户", "新客户"}:
        return True
    if text.lower() in {"customer", "user", "unknown"}:
        return True
    if text.startswith("客户") and text[2:].isdigit():
        return True
    if text.startswith("用户") and text[2:].isdigit():
        return True
    if text.startswith("0:") or text.startswith("1:"):
        return True
    return bool(text.isdigit() and len(text) >= 6)


def _update_account_nickname(db_path: str, account_code: str, nickname: str) -> None:
    name = str(nickname or "").strip()
    if not name:
        return
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE im_accounts SET nickname = ? WHERE account_code = ?",
            (name, account_code),
        )
        conn.commit()


def refresh_profiles(
    db_path: str,
    account_code: str,
    auth: Any,
    *,
    max_peers: int = 30,
) -> dict[str, Any]:
    """Sync hosted account self profile and peer avatars/nicknames."""
    code = str(account_code or "").strip()
    if not code:
        raise RpcError("account_required", "account_code is required")
    if auth is None:
        raise RpcError("auth_invalid", "账号凭证不可用，无法同步资料")

    ensure_reverse_runtime_on_path()
    from utils.im_profile_enricher import enrich_peer_profile, enrich_self_profile

    self_result = enrich_self_profile(db_path, code, auth) or {}
    nickname = str(self_result.get("nickname") or self_result.get("display_name") or "").strip()
    if nickname:
        _update_account_nickname(db_path, code, nickname)

    self_uid = str(self_result.get("user_id") or "").strip()
    if not self_uid:
        try:
            self_uid = str(auth.get_uid())
        except Exception:
            self_uid = ""

    checked = 0
    updated = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT conversation_id, display_name
            FROM conversation_profiles
            WHERE profile_id = ?
              AND IFNULL(is_self, 0) = 0
              AND IFNULL(conversation_id, '') != ''
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (code, max(1, int(max_peers))),
        ).fetchall()

    for row in rows:
        conversation_id = str(row["conversation_id"] or "").strip()
        display_name = str(row["display_name"] or "").strip()
        if display_name and not _is_weak_customer_name(display_name):
            continue
        peer_uid = peer_uid_from_conversation_id(conversation_id)
        if not peer_uid.isdigit():
            continue
        checked += 1
        result = enrich_peer_profile(db_path, code, auth, peer_uid, conversation_id) or {}
        final_name = str(result.get("display_name") or "").strip()
        avatar_url = str(result.get("avatar_url") or "").strip()
        avatar_local = str(result.get("avatar_local_path") or "").strip()
        if (final_name and not _is_weak_customer_name(final_name)) or avatar_url or avatar_local:
            updated += 1

    payload = {
        "account_code": code,
        "self": {
            "nickname": nickname or str(self_result.get("display_name") or "").strip(),
            "douyin_uid": self_uid,
            "avatar_url": str(self_result.get("avatar_url") or "").strip(),
            "avatar_local_path": str(self_result.get("avatar_local_path") or "").strip(),
        },
        "peers": {"checked": checked, "updated": updated},
    }
    logger.info("profile_sync account=%s self_nickname=%s peers_updated=%s", code, nickname, updated)
    return payload


def refresh_profiles_async(db_path: str, account_code: str, auth: Any) -> None:
    import threading

    def _runner() -> None:
        try:
            refresh_profiles(db_path, account_code, auth)
        except Exception:
            logger.exception("profile_sync failed account=%s", account_code)

    threading.Thread(
        target=_runner,
        name=f"dy-profile-sync-{account_code}",
        daemon=True,
    ).start()
