"""stdio NDJSON supervisor for Douyin reverse IPC."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

from channels.douyin_reverse_ipc.account_slots import AccountSlotManager
from channels.douyin_reverse_ipc.errors import RpcError
from channels.douyin_reverse_ipc.protocol import encode_err, encode_ok, parse_request
from channels.douyin_reverse_ipc import query_service, send_service
from channels.douyin_reverse_ipc import profile_service


def _account_code_from_params(params: dict[str, Any]) -> str:
    if not isinstance(params, dict):
        return ""
    return str(params.get("account_code") or params.get("accountCode") or "").strip()


class ReverseSupervisor:
    def __init__(self, db_path: str, *, slots: Optional[AccountSlotManager] = None):
        self.db_path = str(Path(db_path).expanduser().resolve())
        self.slots = slots or AccountSlotManager(self.db_path)

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        name = str(method or "").strip()
        params = params or {}
        if name == "ping":
            return {"pong": True}
        if name == "get_db_path":
            return {"db_path": self.db_path}
        if name == "start_account":
            return self.slots.start_account(_account_code_from_params(params))
        if name == "stop_account":
            return self.slots.stop_account(_account_code_from_params(params))
        if name == "stop_all":
            return self.slots.stop_all()
        if name == "reload_credentials":
            return self.slots.reload_credentials(_account_code_from_params(params))
        if name == "get_account_status":
            return self.slots.get_account_status(_account_code_from_params(params))
        if name == "list_accounts":
            return self.slots.list_accounts()
        if name == "send_text":
            return send_service.send_text(
                self.db_path,
                _account_code_from_params(params),
                text=str(params.get("text") or ""),
                conversation_id=str(params.get("conversation_id") or params.get("conversationId") or ""),
                peer_uid=str(params.get("peer_uid") or params.get("peerUid") or ""),
                client_msg_id=str(params.get("client_msg_id") or params.get("clientMsgId") or ""),
                is_ai_reply=bool(params.get("is_ai_reply") or params.get("isAiReply")),
            )
        if name == "send_emoji":
            return send_service.send_emoji(
                self.db_path,
                _account_code_from_params(params),
                emoji_url=str(params.get("emoji_url") or params.get("emojiUrl") or ""),
                emoji_name=str(params.get("emoji_name") or params.get("emojiName") or ""),
                conversation_id=str(params.get("conversation_id") or params.get("conversationId") or ""),
                peer_uid=str(params.get("peer_uid") or params.get("peerUid") or ""),
                client_msg_id=str(params.get("client_msg_id") or params.get("clientMsgId") or ""),
            )
        if name == "send_image":
            return send_service.send_image(
                self.db_path,
                _account_code_from_params(params),
                image_path=str(params.get("image_path") or params.get("imagePath") or ""),
                conversation_id=str(params.get("conversation_id") or params.get("conversationId") or ""),
                peer_uid=str(params.get("peer_uid") or params.get("peerUid") or ""),
                client_msg_id=str(params.get("client_msg_id") or params.get("clientMsgId") or ""),
            )
        if name == "get_conversations":
            return query_service.get_conversations(
                self.db_path,
                _account_code_from_params(params),
                limit=int(params.get("limit") or 50),
            )
        if name in ("get_messages", "get_messages_after_id"):
            return query_service.get_messages(
                self.db_path,
                _account_code_from_params(params),
                str(params.get("conversation_id") or params.get("conversationId") or ""),
                after_id=str(params.get("after_id") or params.get("afterId") or ""),
                limit=int(params.get("limit") or 50),
            )
        if name == "refresh_profiles":
            return self.slots.refresh_profiles(_account_code_from_params(params))
        if name == "get_account_profile":
            return profile_service.get_account_profile(
                self.db_path,
                _account_code_from_params(params),
            )
        if name == "get_conversation_profile":
            return profile_service.get_conversation_profile(
                self.db_path,
                _account_code_from_params(params),
                str(params.get("conversation_id") or params.get("conversationId") or ""),
            )
        raise RpcError("method_not_found", f"unknown method: {name}")

    def handle_line(self, line: str) -> str:
        req_id: Any = None
        try:
            req = parse_request(line)
            req_id = req["id"]
            data = self.dispatch(req["method"], req["params"])
            return encode_ok(req_id, data)
        except RpcError as exc:
            return encode_err(req_id, exc.code, exc.message)
        except Exception as exc:  # pragma: no cover - safety net
            return encode_err(req_id, "internal", str(exc) or "internal error")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Douyin reverse IPC supervisor")
    parser.add_argument("--db-path", required=True, help="Runtime SQLite path")
    args = parser.parse_args(argv)
    supervisor = ReverseSupervisor(args.db_path)
    for line in sys.stdin:
        sys.stdout.write(supervisor.handle_line(line))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
