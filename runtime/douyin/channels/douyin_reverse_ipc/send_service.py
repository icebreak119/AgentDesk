"""Outbound send helpers for Douyin reverse IPC."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path
from channels.douyin_reverse_ipc.errors import RpcError

_IDEMPOTENCY_TTL_SEC = 60.0
_idempotency_lock = threading.Lock()
_idempotency_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _require_account_code(account_code: str) -> str:
    code = str(account_code or "").strip()
    if not code:
        raise RpcError("account_required", "account_code is required")
    return code


def _require_peer(*, conversation_id: str = "", peer_uid: str = "") -> tuple[str, str]:
    cid = str(conversation_id or "").strip()
    uid = str(peer_uid or "").strip()
    if not cid and not uid:
        raise RpcError("peer_required", "conversation_id or peer_uid is required")
    return cid, uid


def _idempotency_key(account_code: str, client_msg_id: str) -> str:
    return f"{account_code}::{client_msg_id}"


def _idempotency_get(account_code: str, client_msg_id: str) -> Optional[dict[str, Any]]:
    token = str(client_msg_id or "").strip()
    if not token:
        return None
    key = _idempotency_key(account_code, token)
    now = time.time()
    with _idempotency_lock:
        hit = _idempotency_cache.get(key)
        if not hit:
            return None
        ts, payload = hit
        if now - ts > _IDEMPOTENCY_TTL_SEC:
            _idempotency_cache.pop(key, None)
            return None
        return dict(payload)


def _idempotency_put(account_code: str, client_msg_id: str, payload: dict[str, Any]) -> None:
    token = str(client_msg_id or "").strip()
    if not token:
        return
    key = _idempotency_key(account_code, token)
    with _idempotency_lock:
        _idempotency_cache[key] = (time.time(), dict(payload))


def _load_auth(db_path: str, account_code: str):
    ensure_reverse_runtime_on_path()
    from utils.common_util import build_im_auth_from_credentials
    from utils.im_account_store import (
        InvalidIMAccountCredentials,
        load_im_accounts_from_db,
        validate_im_account_credentials,
    )

    accounts = load_im_accounts_from_db(db_path, account_code=account_code, enabled_only=True)
    if not accounts:
        raise RpcError("account_not_found", f"im account not found: {account_code}")
    account = accounts[0]
    try:
        validate_im_account_credentials(account)
        auth = build_im_auth_from_credentials(
            account.cookies_str,
            account.web_protect_str,
            account.keys_str,
        )
    except InvalidIMAccountCredentials as exc:
        raise RpcError("auth_invalid", str(exc)) from exc
    except Exception as exc:
        raise RpcError("auth_invalid", str(exc) or "auth_invalid") from exc
    return account, auth


def _account_my_id(account) -> Optional[int]:
    raw = str(getattr(account, "douyin_uid", "") or "").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


def _session_login_ok(auth) -> bool:
    """用 profile/self 判断 cookie 是否仍是已登录态。

    query/user 在未登录时也会返回 visitor uid，不能用来判断登录。
    """
    ensure_reverse_runtime_on_path()
    try:
        from builder.header import HeaderBuilder, HeaderType
        from builder.params import Params
        from dy_apis.douyin_api import _request_json

        url = "https://www.douyin.com/aweme/v1/web/user/profile/self/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_header("referer", "https://www.douyin.com/")
        params = (
            Params()
            .with_web_defaults(auth, "https://www.douyin.com/", {})
            .with_a_bogus()
        )
        resp = _request_json(
            "GET",
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            params=params.get(),
        )
        user = resp.get("user") if isinstance(resp, dict) else None
        if not isinstance(user, dict):
            return False
        return bool(str(user.get("uid") or "").strip())
    except Exception:
        return False


def _resolve_conversation(
    auth,
    conversation_id: str,
    peer_uid: str,
    *,
    my_id: Optional[int] = None,
) -> tuple[str, Any, str, str]:
    ensure_reverse_runtime_on_path()
    from dy_apis.douyin_api import DouyinAPI

    uid = str(peer_uid or "").strip()
    cid = str(conversation_id or "").strip()
    if uid and not uid.isdigit():
        raise RpcError("peer_required", "peer_uid must be numeric")
    try:
        conversation_id_real, conversation_short_id, ticket = DouyinAPI.resolve_or_create_conversation(
            auth,
            int(uid) if uid else 0,
            conversation_id=cid,
            my_id=my_id,
        )
    except Exception as exc:
        detail = str(exc) or "resolve conversation failed"
        # create/list 失败时先区分「登录失效」与协议/风控问题，避免只抛 INVALID_REQUEST。
        if "INVALID_REQUEST" in detail or "conversation_resolve_failed" in detail:
            if not _session_login_ok(auth):
                raise RpcError(
                    "auth_invalid",
                    "抖音登录已失效，请重新启动该托管账号并完成登录后再发送",
                ) from exc
        raise RpcError("send_unconfirmed", detail) from exc
    peer_out = uid
    if not peer_out and conversation_id_real:
        parts = str(conversation_id_real).split(":")
        if len(parts) >= 2:
            peer_out = parts[-1]
    return str(conversation_id_real), conversation_short_id, str(ticket), peer_out


def _validate_send(result: Any) -> None:
    ensure_reverse_runtime_on_path()
    from utils.im_send_result import validate_send_response

    try:
        validate_send_response(result)
    except Exception as exc:
        raise RpcError("send_unconfirmed", str(exc) or "send_unconfirmed") from exc


def _save_outbound(
    db_path: str,
    account_code: str,
    conversation_id: str,
    peer_uid: str,
    content: str,
    *,
    msg_type: str,
    media_url: str = "",
    unique_token: str = "",
    is_ai_reply: bool = False,
    conversation_short_id: int = 0,
) -> None:
    ensure_reverse_runtime_on_path()
    from utils.im_message_store import ensure_message_tables, save_outbound_message, upsert_conversation_profile

    ensure_message_tables(db_path)
    upsert_conversation_profile(
        db_path,
        account_code,
        conversation_id,
        peer_uid,
        conversation_short_id=str(conversation_short_id or "").strip(),
    )
    kwargs: dict[str, Any] = {
        "msg_type": msg_type,
        "status": "sent",
        "unique_token": unique_token or "",
        "skip_ui_notify": True,
        "is_ai_reply": bool(is_ai_reply),
    }
    # media_url supported by save_outbound_message signature
    try:
        save_outbound_message(
            db_path,
            account_code,
            conversation_id,
            peer_uid,
            content,
            media_url=media_url,
            **kwargs,
        )
    except TypeError:
        save_outbound_message(
            db_path,
            account_code,
            conversation_id,
            peer_uid,
            content,
            **kwargs,
        )


def send_text(
    db_path: str,
    account_code: str,
    *,
    text: str,
    conversation_id: str = "",
    peer_uid: str = "",
    client_msg_id: str = "",
    is_ai_reply: bool = False,
) -> dict[str, Any]:
    code = _require_account_code(account_code)
    cid, uid = _require_peer(conversation_id=conversation_id, peer_uid=peer_uid)
    body = str(text or "").strip()
    if not body:
        raise RpcError("text_empty", "text is required")

    cached = _idempotency_get(code, client_msg_id)
    if cached is not None:
        return cached

    account, auth = _load_auth(db_path, code)
    ensure_reverse_runtime_on_path()
    from dy_apis.douyin_api import DouyinAPI

    conversation_id_real, short_id, ticket, peer_out = _resolve_conversation(
        auth,
        cid,
        uid,
        my_id=_account_my_id(account),
    )
    try:
        result = DouyinAPI.send_msg(auth, conversation_id_real, short_id, ticket, body)
    except Exception as exc:
        raise RpcError("send_unconfirmed", str(exc) or "send failed") from exc
    _validate_send(result)
    _save_outbound(
        db_path,
        code,
        conversation_id_real,
        peer_out or uid,
        body,
        msg_type="text",
        unique_token=str(client_msg_id or "").strip(),
        is_ai_reply=is_ai_reply,
        conversation_short_id=int(short_id or 0),
    )
    payload = {
        "account_code": code,
        "conversation_id": conversation_id_real,
        "peer_uid": peer_out or uid,
        "msg_type": "text",
        "status": "sent",
        "client_msg_id": str(client_msg_id or "").strip(),
    }
    _idempotency_put(code, client_msg_id, payload)
    return payload


def send_emoji(
    db_path: str,
    account_code: str,
    *,
    emoji_url: str,
    emoji_name: str = "",
    conversation_id: str = "",
    peer_uid: str = "",
    client_msg_id: str = "",
) -> dict[str, Any]:
    code = _require_account_code(account_code)
    cid, uid = _require_peer(conversation_id=conversation_id, peer_uid=peer_uid)
    url = str(emoji_url or "").strip()
    if not url:
        raise RpcError("emoji_invalid", "emoji_url is required")

    cached = _idempotency_get(code, client_msg_id)
    if cached is not None:
        return cached

    account, auth = _load_auth(db_path, code)
    ensure_reverse_runtime_on_path()
    from dy_apis.douyin_api import DouyinAPI

    conversation_id_real, short_id, ticket, peer_out = _resolve_conversation(
        auth,
        cid,
        uid,
        my_id=_account_my_id(account),
    )
    try:
        result = DouyinAPI.send_emoji(auth, conversation_id_real, short_id, ticket, url)
    except ValueError as exc:
        raise RpcError("emoji_invalid", str(exc) or "emoji_invalid") from exc
    except Exception as exc:
        raise RpcError("send_unconfirmed", str(exc) or "send failed") from exc
    _validate_send(result)
    label = str(emoji_name or "").strip() or "[表情]"
    _save_outbound(
        db_path,
        code,
        conversation_id_real,
        peer_out or uid,
        label,
        msg_type="emoji",
        media_url=url,
        unique_token=str(client_msg_id or "").strip(),
        conversation_short_id=int(short_id or 0),
    )
    payload = {
        "account_code": code,
        "conversation_id": conversation_id_real,
        "peer_uid": peer_out or uid,
        "msg_type": "emoji",
        "status": "sent",
        "client_msg_id": str(client_msg_id or "").strip(),
    }
    _idempotency_put(code, client_msg_id, payload)
    return payload


def send_image(
    db_path: str,
    account_code: str,
    *,
    image_path: str,
    conversation_id: str = "",
    peer_uid: str = "",
    client_msg_id: str = "",
) -> dict[str, Any]:
    code = _require_account_code(account_code)
    cid, uid = _require_peer(conversation_id=conversation_id, peer_uid=peer_uid)
    path = str(image_path or "").strip()
    if not path or not Path(path).is_file():
        raise RpcError("image_invalid", "image_path must be an existing file")

    cached = _idempotency_get(code, client_msg_id)
    if cached is not None:
        return cached

    account, auth = _load_auth(db_path, code)
    ensure_reverse_runtime_on_path()
    from dy_apis.douyin_api import DouyinAPI

    conversation_id_real, short_id, ticket, peer_out = _resolve_conversation(
        auth,
        cid,
        uid,
        my_id=_account_my_id(account),
    )
    try:
        result = DouyinAPI.send_image(auth, conversation_id_real, short_id, ticket, path)
    except ValueError as exc:
        raise RpcError("image_invalid", str(exc) or "image_invalid") from exc
    except Exception as exc:
        raise RpcError("send_unconfirmed", str(exc) or "send failed") from exc
    _validate_send(result)
    _save_outbound(
        db_path,
        code,
        conversation_id_real,
        peer_out or uid,
        "[图片]",
        msg_type="image",
        media_url=path,
        unique_token=str(client_msg_id or "").strip(),
        conversation_short_id=int(short_id or 0),
    )
    payload = {
        "account_code": code,
        "conversation_id": conversation_id_real,
        "peer_uid": peer_out or uid,
        "msg_type": "image",
        "status": "sent",
        "client_msg_id": str(client_msg_id or "").strip(),
    }
    _idempotency_put(code, client_msg_id, payload)
    return payload
