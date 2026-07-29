"""自动回复软成功：补确认命中不重发；未命中则刷新 ticket 后重发一次。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from dy_apis.douyin_api import DouyinAPIError
from utils.im_reply_engine import IMReplyEngine


def _engine() -> IMReplyEngine:
    eng = IMReplyEngine.__new__(IMReplyEngine)
    eng.auth = object()
    eng.conversation_cache_lock = __import__("threading").Lock()
    eng.conversation_cache = {}
    return eng


def test_soft_retry_skips_resend_when_late_history_hit(monkeypatch) -> None:
    eng = _engine()
    task = SimpleNamespace(sender="1001", conversation_id="0:1:1:1001")
    first_err = DouyinAPIError("send_unconfirmed", "not confirmed", raw={"message": "OK"})

    monkeypatch.setattr(
        "utils.im_reply_engine.DouyinAPI.send_msg_confirmed",
        mock.Mock(side_effect=first_err),
    )
    late = {"server_message_id": "sid-1", "index_in_conversation": 9, "create_time": 1}
    monkeypatch.setattr(
        "utils.im_reply_engine.DouyinAPI.find_recent_outbound_by_text",
        mock.Mock(return_value=late),
    )
    refresh = mock.Mock(return_value=("0:1:1:1001", 22, "tkt-new"))
    eng._get_conversation_meta = refresh  # type: ignore[method-assign]
    eng._invalidate_conversation_cache = mock.Mock()  # type: ignore[method-assign]

    result = eng._send_confirmed_with_soft_retry(
        task, "0:1:1:1001", 11, "tkt-old", "你好"
    )
    assert result["confirmed_message"] is late
    assert result["soft_retry"] == 0
    eng._invalidate_conversation_cache.assert_not_called()
    refresh.assert_not_called()


def test_soft_retry_refreshes_ticket_and_resends_once(monkeypatch) -> None:
    eng = _engine()
    task = SimpleNamespace(sender="1001", conversation_id="0:1:1:1001")
    first_err = DouyinAPIError("send_unconfirmed", "not confirmed", raw={"message": "OK"})
    confirmed = {
        "response": {"message": "OK"},
        "confirmed_message": {
            "server_message_id": "sid-2",
            "index_in_conversation": 10,
            "create_time": 2,
        },
    }
    send_mock = mock.Mock(side_effect=[first_err, confirmed])
    monkeypatch.setattr(
        "utils.im_reply_engine.DouyinAPI.send_msg_confirmed",
        send_mock,
    )
    monkeypatch.setattr(
        "utils.im_reply_engine.DouyinAPI.find_recent_outbound_by_text",
        mock.Mock(return_value=None),
    )
    eng._invalidate_conversation_cache = mock.Mock()  # type: ignore[method-assign]
    eng._get_conversation_meta = mock.Mock(  # type: ignore[method-assign]
        return_value=("0:1:1:1001", 33, "tkt-fresh")
    )

    result = eng._send_confirmed_with_soft_retry(
        task, "0:1:1:1001", 11, "tkt-old", "你好"
    )
    assert result["soft_retry"] == 1
    assert result["confirmed_message"]["server_message_id"] == "sid-2"
    eng._invalidate_conversation_cache.assert_called_once_with("1001")
    eng._get_conversation_meta.assert_called_once()
    assert send_mock.call_count == 2
    # 第二次使用刷新后的 ticket
    assert send_mock.call_args_list[1].args[3] == "tkt-fresh"
