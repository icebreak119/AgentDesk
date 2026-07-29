from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from channels.douyin_reverse_ipc.account_slots import AccountSlotManager
from channels.douyin_reverse_ipc.supervisor import ReverseSupervisor


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


class _FakeRecv:
    def __init__(self):
        self.stop_requested = False
        self.connected = True
        self.last_error = ""
        self.connected_event = threading.Event()
        self.connected_event.set()

    def start(self):
        while not self.stop_requested:
            time.sleep(0.02)

    def request_stop(self):
        self.stop_requested = True


def _fake_factory(account_code, auth, db_path):
    return _FakeRecv()


def _call(s: ReverseSupervisor, method: str, params: dict | None = None, req_id: str = "1"):
    payload = {"id": req_id, "method": method, "params": params or {}}
    return json.loads(s.handle_line(json.dumps(payload) + "\n"))


def test_ping(tmp_path: Path):
    db = tmp_path / "im.db"
    s = ReverseSupervisor(str(db))
    resp = s.handle_line('{"id":"1","method":"ping","params":{}}\n')
    obj = json.loads(resp)
    assert obj["ok"] is True
    assert obj["data"].get("pong") is True


def test_get_db_path(tmp_path: Path):
    db = tmp_path / "im.db"
    s = ReverseSupervisor(str(db))
    obj = json.loads(s.handle_line('{"id":"2","method":"get_db_path","params":{}}\n'))
    assert obj["ok"] is True
    assert Path(obj["data"]["db_path"]).resolve() == db.resolve()


def test_unknown_method(tmp_path: Path):
    s = ReverseSupervisor(str(tmp_path / "im.db"))
    obj = json.loads(s.handle_line('{"id":"3","method":"nope","params":{}}\n'))
    assert obj["ok"] is False
    assert obj["error"]["code"] == "method_not_found"


def test_start_requires_account_code(tmp_path: Path):
    db = tmp_path / "im.db"
    _seed_account(db)
    s = ReverseSupervisor(str(db), slots=AccountSlotManager(str(db), recv_factory=_fake_factory))
    obj = _call(s, "start_account", {})
    assert obj["ok"] is False
    assert obj["error"]["code"] == "account_required"


def test_start_unknown_account(tmp_path: Path):
    db = tmp_path / "im.db"
    _seed_account(db, "acc_001")
    s = ReverseSupervisor(str(db), slots=AccountSlotManager(str(db), recv_factory=_fake_factory))
    obj = _call(s, "start_account", {"account_code": "missing"})
    assert obj["ok"] is False
    assert obj["error"]["code"] == "account_not_found"


def test_start_stop_status_list(tmp_path: Path):
    db = tmp_path / "im.db"
    _seed_account(db, "acc_001")
    s = ReverseSupervisor(str(db), slots=AccountSlotManager(str(db), recv_factory=_fake_factory))

    started = _call(s, "start_account", {"account_code": "acc_001"})
    assert started["ok"] is True
    assert started["data"]["running"] is True

    status = _call(s, "get_account_status", {"account_code": "acc_001"}, req_id="2")
    assert status["ok"] is True
    assert status["data"]["running"] is True

    listed = _call(s, "list_accounts", {}, req_id="3")
    assert listed["ok"] is True
    codes = [a["account_code"] for a in listed["data"]["accounts"]]
    assert "acc_001" in codes

    stopped = _call(s, "stop_account", {"account_code": "acc_001"}, req_id="4")
    assert stopped["ok"] is True
    assert stopped["data"]["running"] is False
