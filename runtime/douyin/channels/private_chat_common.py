"""Shared contract helpers for private-chat completion calls."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def _history_item_text(item: Dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("msg", "content"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    return ""


def split_private_chat_turn(message_list: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], str]:
    """Split full context into ``(history, current_user_content)``."""

    items = [dict(row) for row in (message_list or []) if isinstance(row, dict)]
    if not items:
        return [], ""

    last = items[-1]
    last_role = str(last.get("role") or "").strip().lower()
    last_text = _history_item_text(last)
    if last_role == "user" and last_text:
        return items[:-1], last_text

    for index in range(len(items) - 1, -1, -1):
        row = items[index]
        role = str(row.get("role") or "").strip().lower()
        text = _history_item_text(row)
        if role == "user" and text:
            return items[:index] + items[index + 1 :], text

    return items, last_text


def build_private_chat_completion_body(
    *,
    account_id: str,
    content: str,
    message_list: Optional[List[Dict[str, str]]] = None,
    customer_name: str = "",
    customer_id: str = "",
    model: str = "",
    save_history: bool = False,
    enable_scene_intent: Optional[bool] = None,
    system_prompt: str = "",
    app_id: str = "",
) -> Dict[str, Any]:
    account = (account_id or "").strip()
    text = (content or "").strip()
    if not account:
        raise ValueError("accountId is required")
    if not text:
        raise ValueError("content is required")

    body: Dict[str, Any] = {
        "accountId": account,
        "content": text,
        "saveHistory": bool(save_history),
    }
    if customer_name.strip():
        body["customerName"] = customer_name.strip()
    if customer_id.strip():
        body["customerId"] = customer_id.strip()
    history = [dict(row) for row in (message_list or []) if isinstance(row, dict)]
    if history:
        body["messageList"] = history
    if model.strip():
        body["model"] = model.strip()
    if system_prompt.strip():
        body["systemPrompt"] = system_prompt.strip()
    if app_id.strip():
        body["appId"] = app_id.strip()
    if enable_scene_intent is not None:
        body["enableSceneIntent"] = bool(enable_scene_intent)
    return body


def _try_parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw or raw[0] not in "{[":
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _reply_text_from_mapping(mapping: Any) -> Optional[str]:
    if not isinstance(mapping, dict):
        return None
    for key in ("msg", "content", "reply_text", "reply"):
        text = str(mapping.get(key) or "").strip()
        if text:
            return text
    return None


def _normalize_completion_text(raw: str) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    parsed = _try_parse_json_object(text)
    if isinstance(parsed, dict):
        reply = _reply_text_from_mapping(parsed)
        if reply:
            return reply
        metadata_keys = {"mobile", "intentConfigIds", "client", "extra", "addSense", "isAddSence", "addSence"}
        if set(str(k) for k in parsed).issubset(metadata_keys):
            return None
    return text


def _extract_completion_content(payload: Any) -> Optional[str]:
    if payload is None:
        return None
    if isinstance(payload, str):
        return _normalize_completion_text(payload)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        structured = data.get("structured")
        reply = _reply_text_from_mapping(structured)
        if reply:
            return reply
        for key in ("content", "reply_text", "reply"):
            cur = data.get(key)
            if isinstance(cur, str) and cur.strip():
                normalized = _normalize_completion_text(cur)
                if normalized:
                    return normalized
    for key in ("content", "reply_text", "reply"):
        cur = payload.get(key)
        if isinstance(cur, str) and cur.strip():
            normalized = _normalize_completion_text(cur)
            if normalized:
                return normalized
    if isinstance(data, str) and data.strip():
        return _normalize_completion_text(data)
    return None


def _truthy_completion_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def extract_add_sense_from_completion(js: Any) -> bool:
    if not isinstance(js, dict):
        return False
    for mapping in (js.get("data"), js):
        if not isinstance(mapping, dict):
            continue
        for key in ("addSense", "isAddSence", "addSence"):
            if key in mapping:
                return _truthy_completion_flag(mapping.get(key))
        structured = mapping.get("structured")
        if isinstance(structured, dict):
            for key in ("addSense", "isAddSence", "addSence"):
                if key in structured:
                    return _truthy_completion_flag(structured.get(key))
    return False


def summarize_private_chat_completion_response(js: Any) -> dict[str, Any]:
    if not isinstance(js, dict):
        return {"raw_type": type(js).__name__, "raw_preview": str(js)[:300]}
    data = js.get("data") if isinstance(js.get("data"), dict) else {}
    structured = data.get("structured") if isinstance(data.get("structured"), dict) else {}
    return {
        "code": js.get("code"),
        "msg": js.get("msg"),
        "addSense": extract_add_sense_from_completion(js),
        "content_len": len(str(data.get("content") or "")),
        "model": data.get("model"),
        "structured_keys": sorted(structured.keys()) if structured else [],
        "data_keys": sorted(data.keys()) if data else [],
    }


def parse_private_chat_completion_response(js: Any, transport_err: str) -> tuple[Optional[str], str]:
    if transport_err:
        return None, transport_err
    if not isinstance(js, dict):
        return None, "invalid response: not a JSON object"
    code = js.get("code")
    if code is not None:
        try:
            if int(code) != 200:
                return None, str(js.get("msg") or js.get("description") or "business failed")
        except (TypeError, ValueError):
            pass
    text = _extract_completion_content(js)
    if not text:
        return None, "response has no reply text"
    return text, ""
