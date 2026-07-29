from __future__ import annotations

from pathlib import Path

import pytest

from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path
from channels.douyin_reverse_ipc.errors import RpcError
from channels.douyin_reverse_ipc.query_service import get_conversations, get_messages


def test_query_isolation(tmp_path: Path):
    ensure_reverse_runtime_on_path()
    from utils.im_message_store import (
        ensure_message_tables,
        save_outbound_message,
        upsert_conversation_profile,
    )

    db = tmp_path / "im.db"
    ensure_message_tables(str(db))
    upsert_conversation_profile(str(db), "acc_a", "cid_a", "100")
    upsert_conversation_profile(str(db), "acc_b", "cid_b", "200")
    save_outbound_message(str(db), "acc_a", "cid_a", "100", "hello-a", status="sent")
    save_outbound_message(str(db), "acc_b", "cid_b", "200", "hello-b", status="sent")

    convs_a = get_conversations(str(db), "acc_a")
    assert all(c["conversation_id"] != "cid_b" for c in convs_a["conversations"])

    msgs_a = get_messages(str(db), "acc_a", "cid_a")
    contents = [m["content"] for m in msgs_a["messages"]]
    assert "hello-a" in contents
    assert "hello-b" not in contents

    msgs_wrong = get_messages(str(db), "acc_a", "cid_b")
    assert msgs_wrong["messages"] == []


def test_get_messages_requires_ids():
    with pytest.raises(RpcError) as ei:
        get_messages("x.db", "", "cid")
    assert ei.value.code == "account_required"
    with pytest.raises(RpcError) as ei2:
        get_messages("x.db", "acc", "")
    assert ei2.value.code == "peer_required"
