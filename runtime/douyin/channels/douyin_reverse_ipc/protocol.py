"""NDJSON JSON-RPC request/response helpers."""

from __future__ import annotations

import json
from typing import Any

from channels.douyin_reverse_ipc.errors import RpcError


def parse_request(line: str) -> dict[str, Any]:
    text = str(line or "").strip()
    if not text:
        raise RpcError("invalid_request", "empty request line")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RpcError("invalid_request", f"invalid json: {exc}") from exc
    if not isinstance(obj, dict):
        raise RpcError("invalid_request", "request must be a JSON object")
    req_id = obj.get("id")
    method = obj.get("method")
    if req_id is None or method is None or not str(method).strip():
        raise RpcError("invalid_request", "id and method are required")
    params = obj.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RpcError("invalid_request", "params must be an object")
    return {"id": req_id, "method": str(method).strip(), "params": params}


def encode_ok(req_id: Any, data: Any = None) -> str:
    payload = {"id": req_id, "ok": True, "data": {} if data is None else data}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def encode_err(req_id: Any, code: str, message: str) -> str:
    payload = {
        "id": req_id,
        "ok": False,
        "error": {"code": str(code or "internal"), "message": str(message or "")},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
