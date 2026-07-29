from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from channels.douyin_reverse_ipc.account_slots import AccountSlotManager
from channels.douyin_reverse_ipc.http_api import create_app


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

        import threading

        self.connected_event = threading.Event()
        self.connected_event.set()

    def start(self):
        import time

        while not self.stop_requested:
            time.sleep(0.02)

    def request_stop(self):
        self.stop_requested = True


def test_http_ping_and_lifecycle(tmp_path: Path):
    db = tmp_path / "im.db"
    _seed_account(db)
    slots = AccountSlotManager(str(db), recv_factory=lambda *a, **k: _FakeRecv())
    client = TestClient(create_app(str(db), slots=slots))

    ping = client.get("/ping")
    assert ping.status_code == 200
    assert ping.json()["ok"] is True
    assert ping.json()["data"]["pong"] is True

    started = client.post("/accounts/acc_001/start")
    assert started.status_code == 200
    assert started.json()["data"]["running"] is True

    missing = client.post("/accounts/nope/start")
    assert missing.status_code == 400
    assert missing.json()["ok"] is False
    assert missing.json()["error"]["code"] == "account_not_found"

    stopped = client.post("/accounts/acc_001/stop")
    assert stopped.status_code == 200
    assert stopped.json()["data"]["running"] is False


def test_http_send_text_mocked(tmp_path: Path, monkeypatch):
    db = tmp_path / "im.db"
    _seed_account(db)
    client = TestClient(create_app(str(db)))

    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service._load_auth",
        lambda db_path, account_code: (object(), object()),
    )
    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service._resolve_conversation",
        lambda auth, conversation_id, peer_uid: ("cid", 1, "ticket", peer_uid or "200"),
    )
    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service._validate_send",
        lambda result: None,
    )
    from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path

    ensure_reverse_runtime_on_path()
    import dy_apis.douyin_api as douyin_api

    monkeypatch.setattr(
        douyin_api.DouyinAPI,
        "send_msg",
        staticmethod(lambda *a, **k: {"status_code": 0}),
    )

    resp = client.post(
        "/accounts/acc_001/send/text",
        json={"text": "hello", "peer_uid": "200"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "sent"

    bad = client.post("/accounts/acc_001/send/text", json={"text": "hello"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "peer_required"


def test_http_refresh_profiles_mocked(tmp_path: Path, monkeypatch):
    db = tmp_path / "im.db"
    _seed_account(db)
    slots = AccountSlotManager(str(db), recv_factory=lambda *a, **k: _FakeRecv())
    client = TestClient(create_app(str(db), slots=slots))

    monkeypatch.setattr(
        slots,
        "_build_auth",
        lambda account: object(),
    )
    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.profile_sync.refresh_profiles",
        lambda db_path, account_code, auth: {
            "account_code": account_code,
            "self": {"nickname": "测试昵称", "douyin_uid": "123", "avatar_url": "", "avatar_local_path": ""},
            "peers": {"checked": 1, "updated": 1},
        },
    )

    resp = client.post("/accounts/acc_001/refresh_profiles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["self"]["nickname"] == "测试昵称"
    assert body["data"]["peers"]["updated"] == 1
