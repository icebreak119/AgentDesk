import json


def summarize_send_response(result, *, max_chars=1200):
    """Compact Douyin protobuf response for diagnostics."""
    if not isinstance(result, dict):
        return f"non-dict response: {type(result).__name__}"
    try:
        text = json.dumps(result, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(result)
    limit = max(100, int(max_chars or 1200))
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def _non_empty_text(result, *keys):
    for key in keys:
        value = result.get(key) if isinstance(result, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def validate_send_response(result):
    """Raise when the IM send response is clearly a business failure."""
    if not isinstance(result, dict):
        raise RuntimeError(f"发送响应格式异常: {type(result).__name__}")

    error_desc = _non_empty_text(result, "error_desc")
    if error_desc:
        raise RuntimeError(error_desc)

    status_code = result.get("status_code")
    if status_code not in (None, 0, "0"):
        message = _non_empty_text(result, "status_msg", "message") or f"status_code={status_code}"
        raise RuntimeError(message)

    biz_status_code = result.get("biz_status_code")
    if biz_status_code not in (None, 0, "0"):
        message = _non_empty_text(result, "biz_status_msg", "message") or f"biz_status_code={biz_status_code}"
        raise RuntimeError(message)

    return True
