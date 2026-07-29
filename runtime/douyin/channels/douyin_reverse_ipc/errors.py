"""Stable RPC error codes for Douyin reverse IPC."""

from __future__ import annotations

ERROR_CODES = frozenset(
    {
        "account_required",
        "account_not_found",
        "not_running",
        "auth_invalid",
        "peer_required",
        "text_empty",
        "emoji_invalid",
        "image_invalid",
        "send_unconfirmed",
        "account_mismatch",
        "invalid_request",
        "method_not_found",
        "dependency_missing",
        "internal",
    }
)


class RpcError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = str(code or "").strip() or "internal"
        self.message = str(message or "").strip() or self.code
        super().__init__(self.message)
