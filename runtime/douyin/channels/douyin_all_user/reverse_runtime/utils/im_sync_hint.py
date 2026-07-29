"""账号侧栏「同步中」提示：SQLite 按 reason 记账，主进程轮询刷新 UI。

未回复扫描（主进程）与新会话历史补拉（runtime 子进程）共用，避免互相清掉提示。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Set

logger = logging.getLogger(__name__)

REASON_UNREPLIED_SCAN = "unreplied_scan"
REASON_NEW_CONVERSATION_HISTORY = "new_conversation_history"


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS im_account_sync_hints (
            account_code TEXT NOT NULL,
            reason TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(account_code, reason)
        )
        """
    )
    return conn


def begin_account_sync(db_path: str, account_code: str, reason: str) -> None:
    code = str(account_code or "").strip()
    path = str(db_path or "").strip()
    why = str(reason or "").strip() or "unknown"
    if not code or not path:
        return
    try:
        conn = _connect(path)
        try:
            conn.execute(
                """
                INSERT INTO im_account_sync_hints(account_code, reason, active, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(account_code, reason) DO UPDATE SET
                    active=1,
                    updated_at=excluded.updated_at
                """,
                (code, why, _now_str()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug(
            "begin_account_sync 失败: account_code=%s reason=%s error=%s",
            code,
            why,
            exc,
        )


def end_account_sync(db_path: str, account_code: str, reason: str) -> None:
    code = str(account_code or "").strip()
    path = str(db_path or "").strip()
    why = str(reason or "").strip() or "unknown"
    if not code or not path:
        return
    try:
        conn = _connect(path)
        try:
            conn.execute(
                """
                INSERT INTO im_account_sync_hints(account_code, reason, active, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(account_code, reason) DO UPDATE SET
                    active=0,
                    updated_at=excluded.updated_at
                """,
                (code, why, _now_str()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug(
            "end_account_sync 失败: account_code=%s reason=%s error=%s",
            code,
            why,
            exc,
        )


def list_accounts_with_active_sync(db_path: str) -> Set[str]:
    path = str(db_path or "").strip()
    if not path:
        return set()
    try:
        conn = _connect(path)
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT account_code
                FROM im_account_sync_hints
                WHERE active = 1
                """
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return set()
    return {str(r[0] or "").strip() for r in rows if str(r[0] or "").strip()}


def is_account_sync_active(db_path: str, account_code: str) -> bool:
    code = str(account_code or "").strip()
    path = str(db_path or "").strip()
    if not code or not path:
        return False
    try:
        conn = _connect(path)
        try:
            row = conn.execute(
                """
                SELECT 1 FROM im_account_sync_hints
                WHERE account_code = ? AND active = 1
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return False
    return row is not None
