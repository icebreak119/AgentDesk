from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from channels.douyin_reverse_ipc.errors import RpcError
from channels.douyin_reverse_ipc.send_service import send_emoji, send_image, send_text
from channels.douyin_reverse_ipc.supervisor import ReverseSupervisor
from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path

ensure_reverse_runtime_on_path()


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
            ) VALUES (?, '', '100', 1, 'idle', ?, 'x', 'x', 'x', '', '', '', ?, ?)
            """,
            (account_code, str(db_path.parent / account_code), now, now),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def patched_send(monkeypatch):
    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service._load_auth",
        lambda db_path, account_code: (
            type("Acc", (), {"douyin_uid": "100"})(),
            object(),
        ),
    )
    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service._resolve_conversation",
        lambda auth, conversation_id, peer_uid, my_id=None: (
            conversation_id or "0:1:100:200",
            1,
            "ticket",
            peer_uid or "200",
        ),
    )
    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service._validate_send",
        lambda result: None,
    )


def test_resolve_maps_invalid_request_to_login_expired(monkeypatch):
    class _Boom(Exception):
        pass

    class _FakeAPI:
        @staticmethod
        def resolve_or_create_conversation(*_a, **_k):
            raise _Boom("conversation_resolve_failed: INVALID_REQUEST")

    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service.ensure_reverse_runtime_on_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "channels.douyin_reverse_ipc.send_service._session_login_ok",
        lambda _auth: False,
    )

    import sys
    import types

    fake_mod = types.ModuleType("dy_apis.douyin_api")
    fake_mod.DouyinAPI = _FakeAPI
    monkeypatch.setitem(sys.modules, "dy_apis.douyin_api", fake_mod)

    from channels.douyin_reverse_ipc import send_service as svc

    with pytest.raises(RpcError) as ei:
        svc._resolve_conversation(object(), "0:1:100:200", "200", my_id=100)
    assert ei.value.code == "auth_invalid"
    assert "登录已失效" in ei.value.message


def test_send_text_requires_peer(tmp_path: Path, patched_send):
    db = tmp_path / "im.db"
    _seed_account(db)
    with pytest.raises(RpcError) as ei:
        send_text(str(db), "acc_001", text="hi")
    assert ei.value.code == "peer_required"


def test_send_text_empty(tmp_path: Path, patched_send):
    db = tmp_path / "im.db"
    _seed_account(db)
    with pytest.raises(RpcError) as ei:
        send_text(str(db), "acc_001", text="  ", peer_uid="200")
    assert ei.value.code == "text_empty"


def test_send_text_ok_mocked(tmp_path: Path, patched_send, monkeypatch):
    db = tmp_path / "im.db"
    _seed_account(db)

    calls = {}

    def _send_msg(*args, **kwargs):
        calls["sent"] = True
        return {"status_code": 0}

    monkeypatch.setattr(
        "dy_apis.douyin_api.DouyinAPI.send_msg",
        _send_msg,
        raising=False,
    )
    # Patch where used after ensure path — send_service imports inside function
    import dy_apis.douyin_api as douyin_api

    monkeypatch.setattr(douyin_api.DouyinAPI, "send_msg", staticmethod(_send_msg))

    result = send_text(str(db), "acc_001", text="你好", peer_uid="200", client_msg_id="c1")
    assert result["status"] == "sent"
    assert result["msg_type"] == "text"
    assert result["account_code"] == "acc_001"
    assert calls.get("sent") is True

    # idempotent
    again = send_text(str(db), "acc_001", text="你好", peer_uid="200", client_msg_id="c1")
    assert again == result


def test_send_emoji_and_image(tmp_path: Path, patched_send, monkeypatch):
    db = tmp_path / "im.db"
    _seed_account(db)
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    import dy_apis.douyin_api as douyin_api

    monkeypatch.setattr(
        douyin_api.DouyinAPI,
        "send_emoji",
        staticmethod(lambda *a, **k: {"status_code": 0}),
    )
    monkeypatch.setattr(
        douyin_api.DouyinAPI,
        "send_image",
        staticmethod(lambda *a, **k: {"status_code": 0}),
    )

    emoji = send_emoji(str(db), "acc_001", emoji_url="https://example.com/e.png", peer_uid="200")
    assert emoji["msg_type"] == "emoji"

    image = send_image(str(db), "acc_001", image_path=str(img), peer_uid="200")
    assert image["msg_type"] == "image"

    with pytest.raises(RpcError) as ei:
        send_emoji(str(db), "acc_001", emoji_url="", peer_uid="200")
    assert ei.value.code == "emoji_invalid"

    with pytest.raises(RpcError) as ei2:
        send_image(str(db), "acc_001", image_path=str(tmp_path / "missing.png"), peer_uid="200")
    assert ei2.value.code == "image_invalid"


def test_supervisor_send_text_rpc(tmp_path: Path, patched_send, monkeypatch):
    db = tmp_path / "im.db"
    _seed_account(db)
    import dy_apis.douyin_api as douyin_api

    monkeypatch.setattr(
        douyin_api.DouyinAPI,
        "send_msg",
        staticmethod(lambda *a, **k: {"status_code": 0}),
    )
    s = ReverseSupervisor(str(db))
    payload = {
        "id": "1",
        "method": "send_text",
        "params": {"account_code": "acc_001", "text": "hi", "peer_uid": "200"},
    }
    obj = json.loads(s.handle_line(json.dumps(payload) + "\n"))
    assert obj["ok"] is True
    assert obj["data"]["status"] == "sent"
