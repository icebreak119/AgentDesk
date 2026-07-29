from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from channels.douyin_reverse_ipc.account_slots import AccountSlotManager


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _seed_account(db_path: Path, account_code: str = "acc_001") -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS im_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_code TEXT NOT NULL UNIQUE,
                nickname TEXT NOT NULL DEFAULT '',
                douyin_uid TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'idle',
                profile_dir TEXT NOT NULL,
                cookies_str TEXT NOT NULL DEFAULT '',
                keys_str TEXT NOT NULL DEFAULT '',
                web_protect_str TEXT NOT NULL DEFAULT '',
                last_captured_at TEXT NOT NULL DEFAULT '',
                last_check_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        now = _now()
        conn.execute(
            """
            INSERT OR REPLACE INTO im_accounts (
                account_code, nickname, douyin_uid, enabled, status, profile_dir,
                cookies_str, keys_str, web_protect_str,
                last_captured_at, last_check_at, last_error, created_at, updated_at
            ) VALUES (?, '', '', 1, 'idle', ?, '', '', '', '', '', '', ?, ?)
            """,
            (account_code, str(db_path.parent / account_code), now, now),
        )
        conn.commit()
    finally:
        conn.close()


class _ConnectedFakeRecv:
    def __init__(self, *, status_reporter=None):
        self.status_reporter = status_reporter
        self.connected_event = threading.Event()
        self.closed_event = threading.Event()
        self.error_event = threading.Event()
        self.opened_at = time.monotonic()
        self.stop_requested = False
        self.last_error = ""

    def start(self) -> None:
        self.connected_event.set()
        while not self.stop_requested:
            time.sleep(0.02)


def test_default_start_marks_ready_for_next(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "im.db"
    _seed_account(db, "acc_001")

    from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path

    ensure_reverse_runtime_on_path()
    import utils.im_account_manager as im_account_manager

    _RealWatcher = im_account_manager.RuntimeStatusWatcher

    def _fast_watcher(recv_msg, reporter):
        return _RealWatcher(
            recv_msg,
            reporter,
            ready_seconds=0.1,
            fully_active_seconds=0.2,
        )

    monkeypatch.setattr(im_account_manager, "RuntimeStatusWatcher", _fast_watcher)

    def _fake_default_factory(account_code, auth, db_path, *, status_reporter=None):
        assert status_reporter is not None
        return _ConnectedFakeRecv(status_reporter=status_reporter)

    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.account_slots._default_recv_factory",
        _fake_default_factory,
    )

    mgr = AccountSlotManager(str(db))
    monkeypatch.setattr(mgr, "_build_auth", lambda account: object())

    started = mgr.start_account("acc_001")
    assert started["running"] is True

    deadline = time.monotonic() + 3.0
    status = "starting"
    while time.monotonic() < deadline:
        row = sqlite3.connect(str(db)).execute(
            "SELECT status FROM im_accounts WHERE account_code = ?",
            ("acc_001",),
        ).fetchone()
        status = str(row[0] if row else "")
        if status == "ready_for_next":
            break
        time.sleep(0.05)

    mgr.stop_account("acc_001")
    assert status in {"ready_for_next", "fully_active"}
