"""
普通抖音历史消息回补模块（API 优先架构）。

登录成功后，通过两种数据源获取历史消息：
1. WebSocket 拦截：monkey-patch 浏览器 WebSocket，捕获 protobuf 帧
2. Protobuf API：调用 imapi.douyin.com 获取分页历史

3 天历史消息回补不再依赖 DOM 轮询或点击会话。

去重策略：复用 im_message_store.save_inbound_message 的
UNIQUE(account_profile_id, msg_id) 约束，重复拉取不会重复入库。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    _runtime_root = str(Path(__file__).resolve().parent.parent)
    if _runtime_root not in sys.path:
        sys.path.insert(0, _runtime_root)

import base64
import concurrent.futures
import hashlib
import json
import logging
import re
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from utils.im_message_store import (
    ensure_message_tables,
    save_inbound_message,
    save_outbound_message,
    upsert_conversation_profile,
)
from utils.im_identity import (
    is_im_self_sender,
    resolve_peer_participant,
)
from utils.im_media_cache import (
    ensure_douyin_image_cached,
    extract_douyin_image_url,
    extract_douyin_image_url_from_payload,
    normalize_douyin_image_content,
    cache_inline_image_base64,
)
from utils.profile_auto_reply import (
    load_profile_im_uid,
)

logger = logging.getLogger(__name__)

# 3 天时间窗口（秒）
THREE_DAYS_SECONDS = 3 * 24 * 60 * 60

MESSAGE_DIRECTION_OLDER = 1

# 浏览器 bridge 实例（由外部注入）
_browser_bridge = None

# Playwright page 实例（由外部注入）
_playwright_page = None

# 主线程事件循环引用（供 ThreadPoolExecutor 线程调度协程）
_main_event_loop = None


# WebSocket 拦截缓冲区
_ws_buffer: List[Dict[str, Any]] = []
_ws_hook_installed = False

# ---- JS 注入代码 ----

_WS_HOOK_JS = """
() => {
    if (window.__dy_im_ws_hooked) return 'already_hooked';
    window.__dy_im_backfill_buffer = [];

    var OrigWS = window.WebSocket;
    var buffer = window.__dy_im_backfill_buffer;

    // 保存原始构造函数供清理时恢复
    window.__dy_im_original_ws = OrigWS;

    function pushToBuffer(data) {
        try {
            if (data instanceof Blob) {
                var reader = new FileReader();
                reader.onload = function() {
                    try {
                        var bytes = new Uint8Array(reader.result);
                        var binary = '';
                        for (var i = 0; i < bytes.length; i++) {
                            binary += String.fromCharCode(bytes[i]);
                        }
                        buffer.push({type: 'binary', data: btoa(binary), ts: Date.now()});
                    } catch(e) {}
                };
                reader.readAsArrayBuffer(data);
            } else if (data instanceof ArrayBuffer) {
                var bytes = new Uint8Array(data);
                var binary = '';
                for (var i = 0; i < bytes.length; i++) {
                    binary += String.fromCharCode(bytes[i]);
                }
                buffer.push({type: 'binary', data: btoa(binary), ts: Date.now()});
            } else if (typeof data === 'string') {
                buffer.push({type: 'text', data: data, ts: Date.now()});
            }
        } catch(e) {}
    }

    window.WebSocket = function() {
        var args = Array.prototype.slice.call(arguments);
        var ws = new OrigWS.apply(null, args);

        // 保存原生 addEventListener 引用
        var origAddEvent = ws.addEventListener.bind(ws);
        ws.addEventListener = function(type, listener, options) {
            if (type === 'message') {
                var wrapped = function(event) {
                    pushToBuffer(event.data);
                    return listener.call(this, event);
                };
                return origAddEvent(type, wrapped, options);
            }
            return origAddEvent(type, listener, options);
        };

        // 拦截 onmessage 属性 — 真正赋值给底层 WebSocket
        try {
            var realOnMsgDesc = Object.getOwnPropertyDescriptor(OrigWS.prototype, 'onmessage');
            if (realOnMsgDesc && realOnMsgDesc.set) {
                Object.defineProperty(ws, 'onmessage', {
                    set: function(handler) {
                        if (handler) {
                            var wrapped = function(event) {
                                pushToBuffer(event.data);
                                return handler.call(this, event);
                            };
                            realOnMsgDesc.set.call(ws, wrapped);
                        } else {
                            realOnMsgDesc.set.call(ws, null);
                        }
                    },
                    get: function() {
                        return realOnMsgDesc.get ? realOnMsgDesc.get.call(ws) : null;
                    },
                    configurable: true
                });
            }
        } catch(e) {}

        return ws;
    };

    window.WebSocket.CONNECTING = OrigWS.CONNECTING;
    window.WebSocket.OPEN = OrigWS.OPEN;
    window.WebSocket.CLOSING = OrigWS.CLOSING;
    window.WebSocket.CLOSED = OrigWS.CLOSED;
    window.WebSocket.prototype = OrigWS.prototype;

    window.__dy_im_ws_hooked = true;
    return 'hooked';
}
"""

_POLL_WS_BUFFER_JS = """
(maxItems) => {
    var buf = window.__dy_im_backfill_buffer;
    if (!buf || !buf.length) return [];
    var items = buf.splice(0, maxItems || 500);
    return items;
}
"""

_CLEAR_WS_HOOK_JS = """
() => {
    if (window.__dy_im_original_ws) {
        window.WebSocket = window.__dy_im_original_ws;
    }
    window.__dy_im_backfill_buffer = [];
    window.__dy_im_ws_hooked = false;
    return 'cleared';
}
"""

# ============================================================
# 公共接口
# ============================================================

def set_browser_bridge(bridge):
    """设置浏览器 bridge 实例，用于调用抖音 IM API"""
    global _browser_bridge
    _browser_bridge = bridge
    logger.info("浏览器 bridge 已注入历史回补模块")


def set_playwright_page(page):
    """设置 Playwright page 实例，用于执行 JavaScript"""
    global _playwright_page, _main_event_loop
    _playwright_page = page
    # 记录当前事件循环，供 ThreadPoolExecutor 线程调度协程
    try:
        import asyncio
        _main_event_loop = asyncio.get_running_loop()
    except RuntimeError:
        _main_event_loop = None
    logger.info("Playwright page 已注入历史回补模块")


# ============================================================
# 内部工具函数
# ============================================================

def _evaluate_js(js_code):
    """
    执行 JavaScript 代码，支持浏览器 bridge 和 Playwright（同步版本）。
    """
    if _browser_bridge:
        return _browser_bridge.evaluate_script(js_code)
    elif _playwright_page:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _playwright_page.evaluate(js_code))
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(_playwright_page.evaluate(js_code))
        except Exception as e:
            logger.warning("Playwright 执行 JavaScript 失败: %s", e)
            return None
    else:
        logger.warning("未注入浏览器 bridge 或 Playwright page，无法执行 JavaScript")
        return None


async def _async_evaluate_js(js_code):
    """
    执行 JavaScript 代码（异步版本，直接 await Playwright）。
    用于已在异步上下文中的调用方。
    """
    if _browser_bridge:
        return _browser_bridge.evaluate_script(js_code)
    elif _playwright_page:
        try:
            return await _playwright_page.evaluate(js_code)
        except Exception as e:
            logger.warning("Playwright 执行 JavaScript 失败: %s", e)
            return None
    else:
        logger.warning("未注入浏览器 bridge 或 Playwright page，无法执行 JavaScript")
        return None


def _now_ts() -> float:
    return time.time()


def _ts_to_str(ts: float) -> str:
    if ts <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (ValueError, OSError):
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_im_timestamp(value: Any) -> int:
    ts = _safe_int(value, 0)
    if ts <= 0:
        return 0
    if ts > 10**12:
        return ts // 1000
    return ts


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _canonical_self_senders(
    *,
    douyin_uid: str = "",
    cached_im_uid: str = "",
    known_self_senders: Optional[object] = None,
) -> tuple[str, ...]:
    values: list[str] = []
    for raw in (douyin_uid, cached_im_uid):
        text = str(raw or "").strip()
        if text and text not in values:
            values.append(text)
    if known_self_senders:
        for raw in known_self_senders:
            text = str(raw or "").strip()
            if text and text not in values:
                values.append(text)
    return tuple(values)


def _bind_account_identity_to_conversation(
    conversation: Dict[str, Any],
    *,
    account_self_uid: str = "",
    cached_im_uid: str = "",
    known_self_senders: Optional[object] = None,
) -> None:
    conversation["account_self_uid"] = str(account_self_uid or "").strip()
    conversation["cached_im_uid"] = str(cached_im_uid or "").strip()
    conversation["known_self_senders"] = _canonical_self_senders(
        douyin_uid=account_self_uid,
        cached_im_uid=cached_im_uid,
        known_self_senders=known_self_senders,
    )


def _is_placeholder_profile_name(
    value: Any,
    *,
    conversation_id: str = "",
    peer_user_id: str = "",
) -> bool:
    text = _safe_str(value)
    if not text:
        return True
    if text.startswith("0:") or text.startswith("1:"):
        return True
    if conversation_id and text == _safe_str(conversation_id):
        return True
    if peer_user_id and text == _safe_str(peer_user_id):
        return True
    return text.isdigit()


def _resolve_conversation_peer_user_id(conversation: Dict[str, Any]) -> str:
    conversation_id = _safe_str(conversation.get("conversation_id"))
    peer_user_id = _safe_str(conversation.get("peer_user_id"))
    account_self_uid = _safe_str(conversation.get("account_self_uid"))
    cached_im_uid = _safe_str(conversation.get("cached_im_uid"))
    known_self_senders = frozenset(
        str(v or "").strip()
        for v in (conversation.get("known_self_senders") or ())
        if str(v or "").strip()
    )
    peer_is_invalid = (
        not peer_user_id
        or peer_user_id == conversation_id
        or peer_user_id.startswith("0:")
        or peer_user_id.startswith("1:")
        or is_im_self_sender(
            peer_user_id,
            conversation_id,
            douyin_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
            known_self_senders=known_self_senders,
        )
    )
    if conversation_id:
        resolved = _safe_str(
            resolve_peer_participant(
                conversation_id,
                peer_user_id,
                douyin_uid=account_self_uid,
                cached_im_uid=cached_im_uid,
            )
        )
        if resolved and (peer_is_invalid or resolved != peer_user_id):
            peer_user_id = resolved
    return peer_user_id


def _load_conversation_profile_map(
    db_path: str,
    account_code: str,
    conversation_ids: List[str],
) -> Dict[str, Dict[str, str]]:
    ids: List[str] = []
    for raw in conversation_ids:
        cid = _safe_str(raw)
        if cid and cid not in ids:
            ids.append(cid)
    if not db_path or not account_code or not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT conversation_id, display_name, avatar_url, avatar_local_path, source
            FROM conversation_profiles
            WHERE profile_id = ?
              AND is_self = 0
              AND conversation_id IN ({placeholders})
            """,
            (account_code, *ids),
        ).fetchall()
    finally:
        conn.close()
    profile_map: Dict[str, Dict[str, str]] = {}
    for row in rows:
        cid = _safe_str(row["conversation_id"])
        if not cid:
            continue
        profile_map[cid] = {
            "display_name": _safe_str(row["display_name"]),
            "avatar_url": _safe_str(row["avatar_url"]),
            "avatar_local_path": _safe_str(row["avatar_local_path"]),
            "source": _safe_str(row["source"]),
        }
    return profile_map


def _refresh_missing_peer_profiles(
    db_path: str,
    account_code: str,
    auth,
    conversations: List[Dict[str, Any]],
    *,
    max_workers: int = 4,
) -> Dict[str, int]:
    try:
        from utils.im_profile_enricher import enrich_peer_profile
    except Exception as exc:
        logger.warning("[%s] 无法导入 peer profile 补全模块: %s", account_code, exc)
        return {"enriched": 0, "skipped": 0, "failed": 0}

    profile_map = _load_conversation_profile_map(
        db_path,
        account_code,
        [_safe_str(conv.get("conversation_id")) for conv in conversations],
    )
    pending: List[tuple[str, str]] = []
    skipped = 0
    seen_conv_ids: set[str] = set()

    for conv in conversations:
        conv_id = _safe_str(conv.get("conversation_id"))
        if not conv_id or conv_id in seen_conv_ids:
            continue
        seen_conv_ids.add(conv_id)

        peer_user_id = _resolve_conversation_peer_user_id(conv)
        if peer_user_id:
            conv["peer_user_id"] = peer_user_id
        display_name = _safe_str(conv.get("display_name"))
        if peer_user_id and _is_placeholder_profile_name(
            display_name,
            conversation_id=conv_id,
            peer_user_id=peer_user_id,
        ):
            conv["display_name"] = peer_user_id

        if not peer_user_id:
            skipped += 1
            continue

        current = profile_map.get(conv_id) or {}
        current_name = _safe_str(current.get("display_name"))
        avatar_url = _safe_str(current.get("avatar_url"))
        avatar_local_path = _safe_str(current.get("avatar_local_path"))
        needs_name = _is_placeholder_profile_name(
            current_name,
            conversation_id=conv_id,
            peer_user_id=peer_user_id,
        )
        needs_avatar = not avatar_url and not avatar_local_path
        if not needs_name and not needs_avatar:
            skipped += 1
            continue
        pending.append((conv_id, peer_user_id))

    if not pending:
        return {"enriched": 0, "skipped": skipped, "failed": 0}

    logger.info(
        "[%s] 准备补全 peer profile: pending=%d skipped=%d workers=%d",
        account_code,
        len(pending),
        skipped,
        max(1, min(int(max_workers or 1), len(pending))),
    )

    enriched = 0
    failed = 0

    def _run(task: tuple[str, str]) -> tuple[str, str, Dict[str, Any]]:
        conv_id, peer_user_id = task
        result = enrich_peer_profile(db_path, account_code, auth, peer_user_id, conv_id)
        return conv_id, peer_user_id, result if isinstance(result, dict) else {}

    workers = max(1, min(int(max_workers or 1), len(pending)))
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="dy-peer-profile",
    ) as executor:
        future_map = {
            executor.submit(_run, task): task
            for task in pending
        }
        for future in concurrent.futures.as_completed(future_map):
            conv_id, peer_user_id = future_map[future]
            try:
                _, _, result = future.result()
            except Exception as exc:
                failed += 1
                logger.warning(
                    "[%s] peer profile 补全失败: conversation_id=%s peer=%s error=%s",
                    account_code,
                    conv_id,
                    peer_user_id,
                    exc,
                )
                continue

            display_name = _safe_str(result.get("display_name"))
            avatar_url = _safe_str(result.get("avatar_url"))
            avatar_local_path = _safe_str(result.get("avatar_local_path"))
            if display_name and not _is_placeholder_profile_name(
                display_name,
                conversation_id=conv_id,
                peer_user_id=peer_user_id,
            ):
                enriched += 1
            elif avatar_url or avatar_local_path:
                enriched += 1
            else:
                failed += 1

    return {"enriched": enriched, "skipped": skipped, "failed": failed}


def _normalize_msg_type(raw_type: int) -> str:
    """将数值型消息类型归一化为存储标签"""
    type_map = {
        7: "text",
        5: "emoji",
        17: "voice",
        27: "image",
        8: "video",
        2: "text",
        100: "text",
        11: "image",
        14: "video",
        30: "video",
    }
    return type_map.get(raw_type, "text")


def _extract_content_by_type(raw_content: Any, msg_type_label: str, raw_msg_type: int) -> str:
    """
    按消息类型提取内容文本。
    对齐 douyin_recv_msg.py 的处理逻辑。
    """
    if not raw_content:
        return ""
    if isinstance(raw_content, str):
        return raw_content
    if not isinstance(raw_content, dict):
        return str(raw_content)

    if msg_type_label == "emoji" or raw_msg_type == 5:
        url_list = (raw_content.get("url") or {}).get("url_list") or []
        return f'[表情] {url_list[0]}' if url_list else "[表情]"

    if msg_type_label == "voice" or raw_msg_type == 17:
        url_list = (raw_content.get("resource_url") or {}).get("url_list") or []
        return f'[语音] {url_list[0]}' if url_list else "[语音]"

    if msg_type_label == "video" or raw_msg_type in {8, 30}:
        video_payload = raw_content.get("video") or {}
        item_id = (
            raw_content.get("itemId")
            or raw_content.get("item_id")
            or (video_payload.get("vid") if isinstance(video_payload, dict) else "")
            or (video_payload.get("tkey") if isinstance(video_payload, dict) else "")
            or ""
        )
        item_id = _safe_str(item_id)
        return f'[视频] {item_id}' if item_id else "[视频]"

    if msg_type_label == "image" or raw_msg_type == 27:
        url_list = (raw_content.get("resource_url") or {}).get("origin_url_list") or []
        if not url_list:
            for key in ("large_url_list", "medium_url_list", "thumb_url_list", "url_list"):
                url_list = (raw_content.get("resource_url") or {}).get(key) or []
                if url_list:
                    break
        return f'[图片] {url_list[0]}' if url_list else "[图片]"

    return _safe_str(
        raw_content.get("text")
        or raw_content.get("content")
        or raw_content.get("display_text")
        or ""
    )


def _parse_im_message_content(raw_content) -> dict:
    """解析 IM 消息 content 字段；空串或非 JSON 时返回 {}。"""
    if isinstance(raw_content, dict):
        return raw_content
    if isinstance(raw_content, (bytes, bytearray)):
        raw_content = raw_content.decode("utf-8", errors="ignore")
    text = str(raw_content or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {"_value": parsed}


# ============================================================
# 1. WebSocket 拦截层
# ============================================================

def install_ws_hook() -> bool:
    """注入 WebSocket 拦截 hook。返回是否成功。"""
    global _ws_hook_installed
    if not _browser_bridge and not _playwright_page:
        logger.warning("无法注入 WS hook: 无浏览器实例")
        return False
    try:
        result = _evaluate_js(_WS_HOOK_JS)
        if result == 'hooked':
            _ws_hook_installed = True
            logger.info("WebSocket 拦截 hook 已注入")
            return True
        elif result == 'already_hooked':
            _ws_hook_installed = True
            logger.debug("WebSocket hook 已存在")
            return True
        else:
            logger.warning("WS hook 注入返回: %s", result)
            return False
    except Exception as e:
        logger.warning("WS hook 注入失败: %s", e)
        return False


def poll_ws_buffer(max_items: int = 500) -> List[Dict[str, Any]]:
    """轮询 WS 拦截缓冲区，返回并清空已捕获的消息。"""
    if not _ws_hook_installed:
        return []
    try:
        result = _evaluate_js(_POLL_WS_BUFFER_JS.replace("500", str(max_items)))
        if not result or not isinstance(result, list):
            return []
        return result
    except Exception as e:
        logger.debug("轮询 WS buffer 失败: %s", e)
        return []


def clear_ws_hook() -> None:
    """清理 WS hook。"""
    global _ws_hook_installed, _ws_buffer
    try:
        _evaluate_js(_CLEAR_WS_HOOK_JS)
    except Exception:
        pass
    _ws_hook_installed = False
    _ws_buffer.clear()


def _decode_ws_frame_base64(b64_data: str) -> List[Dict[str, Any]]:
    """
    解码 base64 编码的 WebSocket 二进制帧。
    解析为 PushFrame → Response，提取消息。
    """
    try:
        from static import Live_pb2, Response_pb2
    except ImportError:
        logger.debug("无法导入 protobuf 模块")
        return []

    try:
        raw = base64.b64decode(b64_data)
    except Exception:
        return []

    messages = []
    try:
        frame = Live_pb2.PushFrame()
        frame.ParseFromString(raw)
        if frame.payloadType != 'pb':
            return messages

        response = Response_pb2.Response()
        response.ParseFromString(frame.payload)

        # cmd 500: NewMessageNotify
        if response.body.HasField('new_message_notify'):
            notify = response.body.new_message_notify
            msg = notify.message
            content_raw = msg.content
            msg_type = msg.message_type
            content_parsed = _parse_im_message_content(content_raw)
            msg_type_label = _normalize_msg_type(msg_type)
            content_text = _extract_content_by_type(content_parsed, msg_type_label, msg_type)

            if content_text:
                messages.append({
                    "conversation_id": msg.conversation_id or notify.conversation_id,
                    "conversation_short_id": str(msg.conversation_short_id),
                    "sender": str(msg.sender),
                    "content": content_text,
                    "content_payload": content_parsed,
                    "msg_type": msg_type_label,
                    "raw_msg_type": msg_type,
                    "server_message_id": str(msg.server_message_id),
                    "index_in_conversation": msg.index_in_conversation,
                    "create_time": _normalize_im_timestamp(getattr(msg, "create_time", 0)),
                    "conversation_type": msg.conversation_type or notify.conversation_type,
                    "source": "ws",
                })

        # cmd 301: MessagesInConversationResponse
        if response.body.HasField('messages_in_conversation_body'):
            body = response.body.messages_in_conversation_body
            for msg in body.messages:
                content_parsed = _parse_im_message_content(msg.content)
                msg_type_label = _normalize_msg_type(msg.message_type)
                content_text = _extract_content_by_type(content_parsed, msg_type_label, msg.message_type)
                if content_text:
                    messages.append({
                        "conversation_id": msg.conversation_id,
                        "conversation_short_id": str(msg.conversation_short_id),
                        "sender": str(msg.sender),
                        "content": content_text,
                        "content_payload": content_parsed,
                        "msg_type": msg_type_label,
                        "raw_msg_type": msg.message_type,
                        "server_message_id": str(msg.server_message_id),
                        "index_in_conversation": msg.index_in_conversation,
                        "create_time": _normalize_im_timestamp(getattr(msg, "create_time", 0)),
                        "conversation_type": msg.conversation_type,
                        "source": "ws",
                    })
    except Exception as e:
        logger.debug("WS 帧解码失败: %s", e)

    return messages


def drain_and_decode_ws_buffer() -> List[Dict[str, Any]]:
    """轮询 WS 缓冲区并解码所有已捕获的 protobuf 帧。"""
    raw_items = poll_ws_buffer(max_items=2000)
    all_messages = []
    for item in raw_items:
        if item.get('type') == 'binary' and item.get('data'):
            decoded = _decode_ws_frame_base64(item['data'])
            all_messages.extend(decoded)
        elif item.get('type') == 'text' and item.get('data'):
            # 文本帧通常是 JSON 格式的控制消息
            try:
                text_data = json.loads(item['data'])
                logger.debug("WS 文本帧: %s", str(text_data)[:200])
            except (json.JSONDecodeError, TypeError):
                pass
    return all_messages


def _group_ws_messages_by_conversation(
    messages: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """按 conversation_id 分组 WS 消息。"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for msg in messages:
        cid = msg.get("conversation_id", "")
        if cid:
            groups.setdefault(cid, []).append(msg)
    return groups


# ============================================================
# 2. Protobuf API 层
# ============================================================

def fetch_history_via_api(
    auth,
    conversation_id: str,
    conversation_short_id: int,
    *,
    conversation_type: int = 1,
    anchor_index: int = 0,
    limit: int = 50,
    direction: int = MESSAGE_DIRECTION_OLDER,
) -> Tuple[List[Dict[str, Any]], bool, int]:
    """
    通过 protobuf API 获取历史消息。
    返回 (messages, has_more, next_cursor)。
    """
    from dy_apis.douyin_api import DouyinAPI
    try:
        resp = DouyinAPI.get_messages_by_conversation(
            auth,
            conversation_id,
            conversation_short_id,
            conversation_type=conversation_type,
            anchor_index=anchor_index,
            limit=limit,
            direction=direction,
        )
    except Exception as e:
        logger.debug("API 获取历史消息失败: %s", e)
        return [], False, 0

    if not resp:
        return [], False, 0

    # 解析响应
    messages = []
    has_more = False
    next_cursor = 0

    try:
        body = resp.get("body") or {}
        resp_body = body.get("messages_in_conversation_body") or {}
        msg_list = resp_body.get("messages") or []
        has_more = bool(resp_body.get("has_more", False))
        next_cursor = _safe_int(resp_body.get("next_cursor", 0))

        for msg in msg_list:
            content_raw = msg.get("content", "")
            content_parsed = _parse_im_message_content(content_raw)
            msg_type = _safe_int(msg.get("message_type", 0))
            msg_type_label = _normalize_msg_type(msg_type)
            content_text = _extract_content_by_type(content_parsed, msg_type_label, msg_type)

            if not content_text:
                continue

            messages.append({
                "conversation_id": msg.get("conversation_id", conversation_id),
                "conversation_short_id": str(msg.get("conversation_short_id", conversation_short_id)),
                "sender": str(msg.get("sender", "")),
                "content": content_text,
                "content_payload": content_parsed,
                "msg_type": msg_type_label,
                "raw_msg_type": msg_type,
                "server_message_id": str(msg.get("server_message_id", "")),
                "index_in_conversation": msg.get("index_in_conversation", 0),
                "create_time": _normalize_im_timestamp(msg.get("create_time", 0)),
                "conversation_type": msg.get("conversation_type", 1),
                "source": "api",
            })
    except Exception as e:
        logger.debug("解析 API 响应失败: %s", e)

    return messages, bool(has_more), int(next_cursor)


def backfill_conversation_via_api(
    auth,
    conversation_id: str,
    conversation_short_id: int,
    cutoff_ts: float,
    *,
    conversation_type: int = 1,
    max_pages: int = 10,
) -> List[Dict[str, Any]]:
    """
    通过 API 分页拉取单个会话的全部历史消息。
    停止条件: 无更多消息 或 达到最大页数。
    """
    all_messages = []
    cursor = 0

    for page in range(max_pages):
        messages, has_more, next_cursor = fetch_history_via_api(
            auth,
            conversation_id,
            conversation_short_id,
            conversation_type=conversation_type,
            anchor_index=cursor,
            limit=50,
            direction=MESSAGE_DIRECTION_OLDER,
        )
        if not messages:
            break
        all_messages.extend(messages)

        oldest_ts = min(
            (_normalize_im_timestamp(msg.get("create_time", 0)) for msg in messages if msg.get("create_time")),
            default=0,
        )
        if oldest_ts and oldest_ts < cutoff_ts:
            break

        if not has_more or next_cursor <= 0 or next_cursor == cursor:
            break
        cursor = next_cursor

    return all_messages


def fetch_stranger_messages_via_api(
    auth,
    conversation_short_id: int,
) -> List[Dict[str, Any]]:
    from dy_apis.douyin_api import DouyinAPI

    try:
        resp = DouyinAPI.get_stranger_messages(auth, conversation_short_id=conversation_short_id)
    except Exception as e:
        logger.debug("API 获取陌生人历史消息失败: %s", e)
        return []

    messages: List[Dict[str, Any]] = []
    try:
        body = resp.get("body") or {}
        resp_body = body.get("get_stranger_messages_body") or {}
        msg_list = resp_body.get("messages") or []
        for msg in msg_list:
            content_raw = msg.get("content", "")
            content_parsed = _parse_im_message_content(content_raw)
            msg_type = _safe_int(msg.get("message_type", 0))
            msg_type_label = _normalize_msg_type(msg_type)
            content_text = _extract_content_by_type(content_parsed, msg_type_label, msg_type)
            if not content_text:
                continue
            messages.append({
                "conversation_id": msg.get("conversation_id", ""),
                "conversation_short_id": str(msg.get("conversation_short_id", conversation_short_id)),
                "sender": str(msg.get("sender", "")),
                "content": content_text,
                "content_payload": content_parsed,
                "msg_type": msg_type_label,
                "raw_msg_type": msg_type,
                "server_message_id": str(msg.get("server_message_id", "")),
                "index_in_conversation": msg.get("index_in_conversation", 0),
                "create_time": _safe_int(msg.get("create_time", 0)),
                "conversation_type": msg.get("conversation_type", 1),
                "source": "api",
            })
    except Exception as e:
        logger.debug("解析陌生人 API 响应失败: %s", e)
    return messages


def _select_peer_participant(
    participants: List[Dict[str, Any]],
    *,
    conversation_id: str,
    self_uid: str = "",
    cached_im_uid: str = "",
) -> Dict[str, Any]:
    """
    从 participants 列表中排除 self，选出真实 peer participant。
    不再盲信 participants[0]。
    """
    self_uid = _safe_str(self_uid)
    cached_im_uid = _safe_str(cached_im_uid)
    self_set = {uid for uid in (self_uid, cached_im_uid) if uid}

    non_self_candidates: List[Dict[str, Any]] = []
    for candidate in participants or []:
        if not isinstance(candidate, dict):
            continue
        uid = _safe_str(candidate.get("user_id"))
        if uid and uid in self_set:
            continue
        non_self_candidates.append(candidate)

    if non_self_candidates:
        return non_self_candidates[0]
    # 极端情况：participants 全是自己（理论上不应该），回退第一个
    if participants:
        first = participants[0]
        if isinstance(first, dict):
            return first
    return {}


def _build_api_conversation_record_from_normal(
    item: Dict[str, Any],
    *,
    self_uid: str = "",
    cached_im_uid: str = "",
) -> Dict[str, Any]:
    core_info = item.get("conversation_core_info") or {}
    participants_page = item.get("first_page_participants") or {}
    participants = participants_page.get("participants") or []
    conversation_id = _safe_str(item.get("conversation_id"))
    peer = _select_peer_participant(
        participants,
        conversation_id=conversation_id,
        self_uid=self_uid,
        cached_im_uid=cached_im_uid,
    )
    peer_user_id = _safe_str(peer.get("user_id"))
    raw_alias = _safe_str(peer.get("alias"))
    core_name = _safe_str(core_info.get("name"))
    display_name = core_name or raw_alias
    # 如果 alias 是纯数字 uid、占位符、或等于 conversation_id/peer uid，视为无真实昵称
    if _is_placeholder_profile_name(
        display_name,
        conversation_id=conversation_id,
        peer_user_id=peer_user_id,
    ):
        display_name = ""  # 留空等 enrich_peer_profile 补全，不污染首屏
    return {
        "conversation_id": conversation_id,
        "conversation_short_id": _safe_str(item.get("conversation_short_id")),
        "peer_user_id": peer_user_id,
        "display_name": display_name,
        "last_message_content": "",
        "last_message_time": _safe_int(core_info.get("create_time"), 0),
        "unread_count": _safe_int(item.get("badge_count"), 0),
        "conversation_type": _safe_int(item.get("conversation_type"), 1),
        "time_text": "",
        "href": "",
        "dom_index": -1,
        "source": "api_normal",
    }


def _build_api_conversation_record_from_stranger(
    item: Dict[str, Any],
    *,
    self_uid: str = "",
    cached_im_uid: str = "",
) -> Dict[str, Any]:
    last_message = item.get("last_message") or {}
    participants = item.get("participants") or []
    conversation_id = _safe_str(item.get("conversation_id"))
    peer = _select_peer_participant(
        participants,
        conversation_id=conversation_id,
        self_uid=self_uid,
        cached_im_uid=cached_im_uid,
    )
    peer_user_id = _safe_str(peer.get("user_id"))
    raw_alias = _safe_str(peer.get("alias"))
    display_name = raw_alias
    if _is_placeholder_profile_name(
        display_name,
        conversation_id=conversation_id,
        peer_user_id=peer_user_id,
    ):
        display_name = ""
    return {
        "conversation_id": conversation_id,
        "conversation_short_id": _safe_str(item.get("conversation_short_id")),
        "peer_user_id": peer_user_id,
        "display_name": display_name,
        "last_message_content": "",
        "last_message_time": _safe_int(last_message.get("create_time"), 0),
        "unread_count": _safe_int(item.get("unread"), 0),
        "conversation_type": 2,
        "time_text": "",
        "href": "",
        "dom_index": -1,
        "source": "api_stranger",
    }


def fetch_conversation_list_via_api(
    auth,
    *,
    self_uid: str = "",
    cached_im_uid: str = "",
    target_conv_ids_provider: Optional[Callable[[], Optional[Set[str]]]] = None,
    early_stop_hit_ratio: float = 0.95,
    max_normal_pages: int = 20,
) -> List[Dict[str, Any]]:
    """
    通过主站 IM API 获取会话列表。
    普通会话来自 v1/conversation/list，陌生人会话来自 v1/stranger/get_conversation_list。

    优化参数（用于 unreplied_scan 场景，其它调用方保持默认即可）：
    - target_conv_ids_provider: 每页拉完后调用一次，返回浏览器/其它来源的活跃 conv_id 集合；
      触发早停判断。第一次调用允许阻塞（例如等待并行 spawn 的 browser_reader）。
    - early_stop_hit_ratio: 已拉到的 normal 会话覆盖 target 的比例达到该阈值即提前 break。
    - max_normal_pages: normal 分页上限（默认 20 页，兼容原行为）。
    """
    from dy_apis.douyin_api import DouyinAPI

    conversations: List[Dict[str, Any]] = []
    target_ids: Optional[Set[str]] = None
    early_stopped = False

    # normal 分页：手动 for，以便每页判断早停
    cursor = 0
    fetched_pages = 0
    try:
        for _page_idx in range(max(1, int(max_normal_pages))):
            resp = DouyinAPI.get_conversation_list(
                auth,
                cursor=cursor,
                limit=50,
                conversation_type=1,
                sort_type=1,
            )
            fetched_pages += 1
            body = (resp.get("body") or {}).get("get_conversation_list_body") or {}
            chunk = body.get("list") or []
            if not chunk and fetched_pages == 1:
                # 首页无数据，直接结束 normal
                break
            for item in chunk:
                conv = _build_api_conversation_record_from_normal(
                    item,
                    self_uid=self_uid,
                    cached_im_uid=cached_im_uid,
                )
                if conv.get("conversation_id") and conv.get("conversation_short_id"):
                    conversations.append(conv)

            # 早停：每页判断一次命中率
            if target_conv_ids_provider is not None:
                if target_ids is None:
                    try:
                        _got = target_conv_ids_provider()
                    except Exception as _exc:
                        logger.warning("target_conv_ids_provider 异常: %s", _exc)
                        _got = None
                    target_ids = set(_got) if _got else set()
                if target_ids:
                    have = {_safe_str(c.get("conversation_id")) for c in conversations}
                    hit = len(have & target_ids)
                    ratio = hit / len(target_ids)
                    if ratio >= float(early_stop_hit_ratio):
                        early_stopped = True
                        logger.info(
                            "通过 API 获取普通会话 %d 个 (早停 page=%d hit=%d/%d ratio=%.0f%%)",
                            len(conversations),
                            fetched_pages,
                            hit,
                            len(target_ids),
                            ratio * 100,
                        )
                        break

            if not body.get("has_more"):
                break
            next_cursor = body.get("next_cursor", 0)
            try:
                next_cursor = int(next_cursor)
            except (TypeError, ValueError):
                next_cursor = 0
            if next_cursor <= 0 or next_cursor == cursor:
                break
            cursor = next_cursor
        if not early_stopped:
            logger.info(
                "通过 API 获取普通会话 %d 个 (pages=%d)", len(conversations), fetched_pages,
            )
    except Exception as e:
        logger.warning("通过 API 获取普通会话失败: %s", e)

    try:
        stranger_items = DouyinAPI.get_all_stranger_conversation_list(auth, count=50)
        before = len(conversations)
        for item in stranger_items:
            conv = _build_api_conversation_record_from_stranger(
                item,
                self_uid=self_uid,
                cached_im_uid=cached_im_uid,
            )
            if conv.get("conversation_id") and conv.get("conversation_short_id"):
                conversations.append(conv)
        logger.info("通过 API 获取陌生人会话 %d 个", len(conversations) - before)
    except Exception as e:
        logger.warning("通过 API 获取陌生人会话失败: %s", e)

    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for conv in conversations:
        key = (
            _safe_str(conv.get("conversation_id")),
            _safe_str(conv.get("conversation_short_id")),
        )
        if not any(key):
            continue
        deduped[key] = conv
    return list(deduped.values())


def _persist_api_only_conversation(
    db_path: str,
    account_code: str,
    conversation: Dict[str, Any],
) -> None:
    conv_id = _safe_str(conversation.get("conversation_id"))
    conv_short_id = _safe_str(conversation.get("conversation_short_id"))
    peer_user_id = _resolve_conversation_peer_user_id(conversation)
    display_name = _safe_str(conversation.get("display_name"))
    if peer_user_id:
        conversation["peer_user_id"] = peer_user_id
    is_placeholder = _is_placeholder_profile_name(
        display_name,
        conversation_id=conv_id,
        peer_user_id=peer_user_id,
    )
    if is_placeholder:
        # 不要把纯数字 uid / 占位符当成最终展示名持久化，
        # 保留空名等 enrich_peer_profile 补全，避免污染首屏。
        display_name = ""
        conversation["display_name"] = ""
    if not conv_id or not conv_short_id:
        return
    # 只在有真实昵称或 peer_user_id 时写 profile；纯占位符不覆盖
    if display_name or peer_user_id:
        upsert_conversation_profile(
            db_path,
            account_code,
            conv_id,
            display_name=display_name,
            source="history_backfill",
            conversation_short_id=conv_short_id,
        )


# ============================================================
# 3. 双源合并去重
# ============================================================

def merge_and_dedup_messages(
    ws_messages: List[Dict[str, Any]],
    api_messages: List[Dict[str, Any]],
    dom_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    合并三个数据源的消息并去重。
    优先级: WS > API > DOM。
    去重键: server_message_id > index_in_conversation > content hash
    """
    merged: Dict[str, Dict[str, Any]] = {}
    content_hash_map: Dict[str, str] = {}  # content_hash -> dedup_key

    def _content_hash(msg: Dict[str, Any]) -> str:
        content = str(msg.get("content") or "")
        ts = msg.get("create_time", 0)
        return hashlib.blake2s(
            f"{ts}:{content}".encode("utf-8", errors="ignore"),
            digest_size=8,
        ).hexdigest()

    def _dedup_key(msg: Dict[str, Any]) -> str:
        sid = str(msg.get("server_message_id") or "").strip()
        if sid and sid != "0":
            return f"sid:{sid}"
        idx = msg.get("index_in_conversation")
        if idx:
            cid = str(msg.get("conversation_id") or "")
            return f"idx:{cid}:{idx}"
        return f"hash:{_content_hash(msg)}"

    def _insert(msg: Dict[str, Any]) -> None:
        key = _dedup_key(msg)
        ch = _content_hash(msg)
        # 如果同一内容已有低优先级条目，替换它
        if ch in content_hash_map:
            old_key = content_hash_map[ch]
            if old_key != key and old_key in merged:
                del merged[old_key]
        merged[key] = msg
        content_hash_map[ch] = key

    # 按优先级从低到高处理: DOM → API → WS
    # 高优先级会覆盖低优先级的同内容消息
    for msg in dom_messages:
        _insert(msg)
    for msg in api_messages:
        _insert(msg)
    for msg in ws_messages:
        _insert(msg)

    return list(merged.values())


# ============================================================
# 4. 消息持久化
# ============================================================

def _save_message_to_db(
    db_path: str,
    account_code: str,
    conversation_id: str,
    msg: Dict[str, Any],
    peer_user_id: str,
    stats: Dict[str, Any],
    *,
    touch_realtime_activity: bool = True,
    allow_content_window_dedupe: bool = False,
) -> bool:
    """将单条消息保存到数据库。返回是否为新消息。"""
    content = msg.get("content", "")
    if not content:
        stats["messages_filtered_empty"] += 1
        return False

    # 过滤 3 天外消息
    create_time = _normalize_im_timestamp(msg.get("create_time", 0))
    created_at_str = _ts_to_str(create_time) if create_time > 0 else ""
    cutoff_ts = _now_ts() - THREE_DAYS_SECONDS
    if create_time > 0 and create_time < cutoff_ts:
        stats["messages_filtered_3d"] += 1
        return False

    # 生成 unique_token
    server_message_id = msg.get("server_message_id", "")
    index_in_conv = msg.get("index_in_conversation", 0)
    if server_message_id and server_message_id != "0":
        unique_token = server_message_id
    elif index_in_conv:
        unique_token = str(index_in_conv)
    else:
        content_digest = hashlib.blake2s(
            content.encode("utf-8", errors="ignore"),
            digest_size=8,
        ).hexdigest()
        unique_token = f"hist_{create_time}_{content_digest}"

    msg_type = msg.get("msg_type", "text")
    media_url = ""
    media_local_path = ""
    media_video_url = ""
    media_video_local_path = ""
    normalized_content = str(content or "").strip()
    if str(msg_type or "").strip() == "image":
        raw_payload = msg.get("content_payload") or {}
        inline_pic = ""
        if isinstance(raw_payload, dict):
            inline_pic = str(raw_payload.get("inline_pic") or "").strip()
            media_url = extract_douyin_image_url_from_payload(raw_payload)
            normalized_content = normalized_content or "[图片]"
        else:
            media_url = extract_douyin_image_url(content)
        if inline_pic:
            media_local_path = cache_inline_image_base64(
                inline_pic,
                db_path=db_path,
                account_code=account_code,
                preferred_name=str(unique_token or ""),
            )
        if not media_local_path and media_url:
            media_local_path = ensure_douyin_image_cached(
                media_url,
                db_path=db_path,
                account_code=account_code,
            )
        normalized_content = normalize_douyin_image_content(normalized_content or "[图片]", media_url)
    elif str(msg_type or "").strip() == "emoji":
        raw_payload = msg.get("content_payload") or {}
        normalized_content = "[表情]"
        from utils.im_media_cache import (
            ensure_douyin_emoji_cached,
            extract_douyin_emoji_url_from_content,
            extract_douyin_emoji_url_from_payload,
            normalize_douyin_emoji_content,
        )

        if isinstance(raw_payload, dict) and raw_payload:
            media_url = extract_douyin_emoji_url_from_payload(raw_payload)
        else:
            media_url = extract_douyin_emoji_url_from_content(content)
        if media_url:
            media_local_path = ensure_douyin_emoji_cached(
                media_url,
                db_path=db_path,
                account_code=account_code,
                preferred_name=str(unique_token or ""),
            )
        normalized_content = normalize_douyin_emoji_content(
            normalized_content or str(content or ""),
            media_url,
        )
    elif str(msg_type or "").strip() == "text":
        try:
            from child_mata.chat_item.douyin_emoji_catalog import (
                resolve_douyin_emoji_local_by_shortcut,
            )

            catalog_path = resolve_douyin_emoji_local_by_shortcut(normalized_content)
            if catalog_path:
                media_local_path = catalog_path
        except Exception:
            pass
    elif str(msg_type or "").strip() == "video":
        raw_payload = msg.get("content_payload") or {}
        normalized_content = "[视频]"
        if isinstance(raw_payload, dict):
            from utils.im_media_cache import (
                extract_douyin_video_cover_url_from_payload,
                resolve_douyin_video_play_url,
            )

            inline_pic = str(raw_payload.get("inline_pic") or "").strip()
            repair_auth = stats.get("_auth") if isinstance(stats, dict) else None
            media_video_url = resolve_douyin_video_play_url(repair_auth, raw_payload)
            cover_url = extract_douyin_video_cover_url_from_payload(raw_payload)
            if cover_url:
                media_url = cover_url
            if inline_pic:
                media_local_path = cache_inline_image_base64(
                    inline_pic,
                    db_path=db_path,
                    account_code=account_code,
                    preferred_name=str(unique_token or ""),
                )
            if not media_local_path and media_url:
                media_local_path = ensure_douyin_image_cached(
                    media_url,
                    db_path=db_path,
                    account_code=account_code,
                )
    sender = str(msg.get("sender", "") or "").strip()
    account_self_uid = str(msg.get("account_self_uid", "") or "").strip()
    cached_im_uid = str(msg.get("cached_im_uid", "") or "").strip()
    known_self_senders = msg.get("known_self_senders") or ()
    is_self = bool(msg.get("is_self", False))
    if not is_self and sender:
        is_self = is_im_self_sender(
            sender,
            conversation_id,
            douyin_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
            known_self_senders=frozenset(str(v or "").strip() for v in known_self_senders if str(v or "").strip()),
        )
    resolved_peer_user_id = str(peer_user_id or "").strip()
    if conversation_id:
        resolved_peer_user_id = resolve_peer_participant(
            conversation_id,
            sender,
            douyin_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
        ) or resolved_peer_user_id
    if not resolved_peer_user_id and not is_self:
        resolved_peer_user_id = sender

    # 统计消息类型
    type_key = f"type_{msg_type}"
    if type_key in stats:
        stats[type_key] += 1

    if is_self:
        existing_msg_id = save_outbound_message(
            db_path,
            account_code,
            conversation_id,
            resolved_peer_user_id or sender,
            normalized_content,
            msg_type=msg_type,
            sender_id=sender or account_self_uid or "我",
            unique_token=unique_token,
            media_url=media_url,
            media_local_path=media_local_path,
            media_video_url=media_video_url,
            media_video_local_path=media_video_local_path,
            created_at=created_at_str,
            replied_at=created_at_str,
            # 全量历史回补默认关闭内容窗去重；单会话懒拉可打开，避免刚发出的消息被二次入库。
            allow_content_window_dedupe=allow_content_window_dedupe,
            touch_realtime_activity=touch_realtime_activity,
        )
    else:
        existing_msg_id = save_inbound_message(
            db_path, account_code, conversation_id, sender, normalized_content,
            msg_type=msg_type,
            unique_token=unique_token,
            peer_user_id=resolved_peer_user_id or sender,
            media_url=media_url,
            media_local_path=media_local_path,
            media_video_url=media_video_url,
            media_video_local_path=media_video_local_path,
            read_status="read",
            known_self_uids=_canonical_self_senders(
                douyin_uid=account_self_uid,
                cached_im_uid=cached_im_uid,
                known_self_senders=known_self_senders,
            ),
            created_at=created_at_str,
            allow_content_window_dedupe=allow_content_window_dedupe,
            touch_realtime_activity=touch_realtime_activity,
        )

    if existing_msg_id:
        stats["messages_saved"] += 1
        return True
    else:
        stats["messages_duplicate"] += 1
        return False


def _repair_misclassified_self_messages(
    db_path: str,
    account_code: str,
    *,
    account_self_uid: str = "",
    cached_im_uid: str = "",
    known_self_senders: Optional[object] = None,
) -> int:
    """
    将历史回补中误写为 inbound 的“自己发送”消息翻正为 outbound。

    这一步用于兜底旧脏数据，以及补偿历史回补重复执行时不会重写旧行的问题。
    """
    canonical_self_senders = _canonical_self_senders(
        douyin_uid=account_self_uid,
        cached_im_uid=cached_im_uid,
        known_self_senders=known_self_senders,
    )
    if not account_code or not canonical_self_senders:
        return 0

    placeholders = ",".join("?" for _ in canonical_self_senders)
    repaired = 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT id, conversation_id, from_user_id, to_user_id
            FROM messages
            WHERE account_profile_id = ?
              AND direction = 'inbound'
              AND TRIM(from_user_id) IN ({placeholders})
            ORDER BY id ASC
            """,
            (account_code, *canonical_self_senders),
        ).fetchall()
        for row in rows:
            conversation_id = str(row["conversation_id"] or "").strip()
            sender_id = str(row["from_user_id"] or "").strip()
            if not conversation_id or not sender_id:
                continue
            peer_user_id = resolve_peer_participant(
                conversation_id,
                sender_id,
                douyin_uid=account_self_uid,
                cached_im_uid=cached_im_uid,
            )
            peer_user_id = str(peer_user_id or "").strip()
            if not peer_user_id or peer_user_id == sender_id:
                fallback = str(row["to_user_id"] or "").strip()
                if fallback and fallback not in {"我", sender_id}:
                    peer_user_id = fallback
            if not peer_user_id or peer_user_id == sender_id:
                continue
            conn.execute(
                """
                UPDATE messages
                SET to_user_id = ?,
                    direction = 'outbound',
                    read_status = 'read',
                    status = CASE
                        WHEN IFNULL(status, '') = '' THEN 'sent'
                        ELSE status
                    END
                WHERE id = ?
                """,
                (peer_user_id, int(row["id"] or 0)),
            )
            repaired += 1
        if repaired:
            conn.commit()
    finally:
        conn.close()
    return repaired


def _collapse_history_direction_duplicates(db_path: str, account_code: str) -> int:
    """
    合并同一 unique_token 的 network/outbound 双记录。

    历史原因：
    1. 旧回补先以 network/inbound 写入
    2. 后续方向修复后又按 outbound 新插一条
    两者 unique_token 相同，但 direction 前缀不同，导致 UI 看到重复。
    """
    if not db_path or not account_code:
        return 0

    collapsed = 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, msg_id, direction
            FROM messages
            WHERE account_profile_id = ?
            ORDER BY id ASC
            """,
            (account_code,),
        ).fetchall()
        grouped: Dict[str, List[sqlite3.Row]] = {}
        for row in rows:
            msg_id = str(row["msg_id"] or "").strip()
            parts = msg_id.split("\x1e", 3)
            if len(parts) != 4:
                continue
            key = f"{parts[0]}\x1e{parts[2]}\x1e{parts[3]}"
            grouped.setdefault(key, []).append(row)

        delete_ids: List[int] = []
        update_rows: List[tuple[str, int]] = []
        for same_token_rows in grouped.values():
            if len(same_token_rows) < 2:
                continue
            keeper = next(
                (
                    row
                    for row in same_token_rows
                    if str(row["direction"] or "").strip() == "outbound"
                ),
                same_token_rows[-1],
            )
            keeper_id = int(keeper["id"] or 0)
            keeper_msg_id = str(keeper["msg_id"] or "").strip()
            keeper_parts = keeper_msg_id.split("\x1e", 3)
            for row in same_token_rows:
                row_id = int(row["id"] or 0)
                if row_id and row_id != keeper_id:
                    delete_ids.append(row_id)
                    collapsed += 1
            if len(keeper_parts) == 4 and keeper_parts[1] != "outbound":
                keeper_parts[1] = "outbound"
                update_rows.append(("\x1e".join(keeper_parts), keeper_id))

        if delete_ids:
            placeholders = ",".join("?" for _ in delete_ids)
            conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})",
                delete_ids,
            )
        if update_rows:
            conn.executemany(
                "UPDATE messages SET msg_id = ?, direction = 'outbound' WHERE id = ?",
                update_rows,
            )
        if update_rows or delete_ids:
            conn.commit()
    finally:
        conn.close()
    return collapsed


# ============================================================
# 5. 入口函数
# ============================================================

def backfill_account_history(
    db_path: str,
    account_code: str,
    auth,
    *,
    three_days_seconds: int = THREE_DAYS_SECONDS,
    playwright_page=None,
    explicit_self_uid: str = "",
    known_self_senders: Optional[object] = None,
) -> Dict[str, Any]:
    """
    登录后历史回补入口（WS + API）。

    流程:
    1. 安装 WS 拦截 hook，等待预热
    2. 通过 API 枚举会话列表
    3. 轮询 WS 缓冲区获取已捕获的会话消息
    4. 遍历每个会话: WS → API 双源回补
    5. 最终轮询 WS 缓冲区处理尾部消息
    6. 清理 hook，返回统计

    身份优先级：explicit_self_uid -> auth.get_uid() -> im_accounts.douyin_uid -> profile_meta.im_uid
    """
    # 参数重命名以避免与局部 set 冲突
    known_self_senders_param = known_self_senders
    started_at = time.monotonic()
    cutoff_ts = _now_ts() - three_days_seconds
    account_self_uid = ""
    cached_im_uid = ""
    known_set: set[str] = set()

    if playwright_page:
        set_playwright_page(playwright_page)

    # 优先级 1：显式传入的 self uid
    explicit_uid_text = _safe_str(explicit_self_uid)
    if explicit_uid_text:
        account_self_uid = explicit_uid_text

    # 优先级 2：auth.get_uid()
    if not account_self_uid and auth is not None:
        try:
            account_self_uid = _safe_str(auth.get_uid())
        except Exception:
            pass

    # 优先级 3：im_accounts.douyin_uid
    if not account_self_uid:
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT douyin_uid FROM im_accounts WHERE account_code = ? LIMIT 1",
                    (account_code,),
                ).fetchone()
                if row and row[0]:
                    account_self_uid = _safe_str(row[0])
        except Exception:
            pass

    # 优先级 4：profile_meta.im_uid
    cached_im_uid = load_profile_im_uid(account_code)

    # 合并 known_self_senders
    known_self_senders: set[str] = set()
    for value in (account_self_uid, cached_im_uid):
        text = _safe_str(value)
        if text:
            known_self_senders.add(text)
    if known_self_senders_param:
        for raw in known_self_senders_param:
            text = _safe_str(raw)
            if text:
                known_self_senders.add(text)

    stats = {
        "_auth": auth,
        "total_conversations": 0,
        "normal_conversations": 0,
        "stranger_conversations": 0,
        "total_messages_pulled": 0,
        "source_ws": 0,
        "source_api": 0,
        "source_dom": 0,
        "messages_saved": 0,
        "messages_duplicate": 0,
        "messages_filtered_3d": 0,
        "messages_filtered_empty": 0,
        "type_text": 0,
        "type_emoji": 0,
        "type_voice": 0,
        "type_image": 0,
        "type_video": 0,
        "profiles_enriched": 0,
        "profiles_skipped": 0,
        "profiles_failed": 0,
        "errors": 0,
        "elapsed_seconds": 0,
    }

    logger.info(
        "[%s] 开始历史回补: 3天截止=%s (距今 %d 秒)",
        account_code, _ts_to_str(cutoff_ts), three_days_seconds,
    )

    # Phase 1: 安装 WS 拦截 hook
    ws_installed = install_ws_hook()
    if ws_installed:
        logger.info("[%s] WS hook 已安装，等待 5 秒预热...", account_code)
        time.sleep(5)

    # Phase 2: 获取会话列表（仅 API）
    conversations = fetch_conversation_list_via_api(
        auth,
        self_uid=account_self_uid,
        cached_im_uid=cached_im_uid,
    )
    if conversations:
        logger.info("[%s] 会话枚举: 仅 API 成功，共 %d 个会话", account_code, len(conversations))

    if not conversations:
        logger.warning("[%s] 会话列表为空，跳过历史回补", account_code)
        clear_ws_hook()
        stats["elapsed_seconds"] = round(time.monotonic() - started_at, 2)
        return stats

    stats["total_conversations"] = len(conversations)
    for conv in conversations:
        if conv.get("conversation_type", 1) != 1:
            stats["stranger_conversations"] += 1
        else:
            stats["normal_conversations"] += 1

    logger.info(
        "[%s] 会话列表: 总计=%d 普通=%d 陌生人=%d",
        account_code,
        stats["total_conversations"],
        stats["normal_conversations"],
        stats["stranger_conversations"],
    )

    for conv in conversations:
        _bind_account_identity_to_conversation(
            conv,
            account_self_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
            known_self_senders=known_self_senders,
        )
        _persist_api_only_conversation(db_path, account_code, conv)

    # Phase 3: 轮询 WS 缓冲区获取已捕获的消息
    ws_messages_all = drain_and_decode_ws_buffer()
    ws_by_conversation = _group_ws_messages_by_conversation(ws_messages_all)
    logger.info("[%s] WS 捕获到 %d 个会话的消息", account_code, len(ws_by_conversation))

    # Phase 4: 遍历每个 API 会话
    for conv in conversations:
        conv_id = conv.get("conversation_id", "")
        display_name = conv.get("display_name", "")

        try:
            if conv_id and not conv.get("conversation_short_id"):
                ws_msgs = ws_by_conversation.get(conv_id, [])
                if ws_msgs:
                    short_from_ws = ws_msgs[0].get("conversation_short_id", "")
                    if short_from_ws:
                        conv["conversation_short_id"] = short_from_ws

            if not conv.get("conversation_id") or not conv.get("conversation_short_id"):
                logger.warning(
                    "[%s] 跳过缺少会话标识的会话: name=%s conv_id=%s short_id=%s",
                    account_code,
                    display_name or "(无名)",
                    conv.get("conversation_id", ""),
                    conv.get("conversation_short_id", ""),
                )
                continue

            _backfill_single_conversation_hybrid(
                db_path=db_path,
                account_code=account_code,
                auth=auth,
                conversation=conv,
                cutoff_ts=cutoff_ts,
                ws_messages=ws_by_conversation.get(conv_id, []),
                stats=stats,
            )
        except Exception as e:
            logger.error("[%s] 会话 %s 回补失败: %s", account_code, display_name or conv_id, e)
            stats["errors"] += 1

    profile_refresh = _refresh_missing_peer_profiles(
        db_path,
        account_code,
        auth,
        conversations,
    )
    stats["profiles_enriched"] += int(profile_refresh.get("enriched", 0) or 0)
    stats["profiles_skipped"] += int(profile_refresh.get("skipped", 0) or 0)
    stats["profiles_failed"] += int(profile_refresh.get("failed", 0) or 0)
    if stats["profiles_enriched"] or stats["profiles_failed"]:
        logger.info(
            "[%s] peer profile 补全完成: enriched=%d skipped=%d failed=%d",
            account_code,
            stats["profiles_enriched"],
            stats["profiles_skipped"],
            stats["profiles_failed"],
        )

    # Phase 5: 最终 WS 轮询处理尾部消息
    ws_final = drain_and_decode_ws_buffer()
    for msg in ws_final:
        cid = msg.get("conversation_id", "")
        if not cid:
            continue
        msg["account_self_uid"] = account_self_uid
        msg["cached_im_uid"] = cached_im_uid
        msg["known_self_senders"] = tuple(sorted(known_self_senders))
        _save_message_to_db(db_path, account_code, cid, msg, msg.get("sender", ""), stats)
        stats["source_ws"] += 1

    repaired_count = _repair_misclassified_self_messages(
        db_path,
        account_code,
        account_self_uid=account_self_uid,
        cached_im_uid=cached_im_uid,
        known_self_senders=known_self_senders,
    )
    if repaired_count:
        logger.info("[%s] 历史消息方向纠偏完成: repaired=%d", account_code, repaired_count)

    collapsed_count = _collapse_history_direction_duplicates(db_path, account_code)
    if collapsed_count:
        logger.info("[%s] 历史消息重复折叠完成: collapsed=%d", account_code, collapsed_count)

    # 清理
    clear_ws_hook()
    stats["elapsed_seconds"] = round(time.monotonic() - started_at, 2)

    logger.info(
        "[%s] 历史回补完成: 会话=%d 拉取=%d 入库=%d 重复=%d "
        "3天外=%d 空=%d 来源[ws=%d,api=%d,dom=%d] "
        "资料[ok=%d,skip=%d,fail=%d] "
        "类型[text=%d,emoji=%d,voice=%d,image=%d,video=%d] "
        "错误=%d 耗时=%.2fs",
        account_code,
        stats["total_conversations"],
        stats["total_messages_pulled"],
        stats["messages_saved"],
        stats["messages_duplicate"],
        stats["messages_filtered_3d"],
        stats["messages_filtered_empty"],
        stats["source_ws"],
        stats["source_api"],
        stats["source_dom"],
        stats["profiles_enriched"],
        stats["profiles_skipped"],
        stats["profiles_failed"],
        stats["type_text"],
        stats["type_emoji"],
        stats["type_voice"],
        stats["type_image"],
        stats["type_video"],
        stats["errors"],
        stats["elapsed_seconds"],
    )

    return stats


# ============================================================
# 6. 异步入口函数（供已持有事件循环的调用方使用）
# ============================================================

async def async_backfill_account_history(
    db_path: str,
    account_code: str,
    auth,
    *,
    three_days_seconds: int = THREE_DAYS_SECONDS,
    max_concurrency: int = 4,
    explicit_self_uid: str = "",
    known_self_senders: Optional[object] = None,
) -> Dict[str, Any]:
    """
    异步版历史回补入口。
    直接 await Playwright 调用，避免 ThreadPoolExecutor 死锁。
    供 collect_account_credentials 等已在异步上下文中的调用方使用。

    身份优先级：explicit_self_uid -> auth.get_uid() -> im_accounts.douyin_uid -> profile_meta.im_uid
    """
    import asyncio as _aio

    known_self_senders_param = known_self_senders
    started_at = time.monotonic()
    cutoff_ts = _now_ts() - three_days_seconds
    account_self_uid = ""
    cached_im_uid = ""

    # 优先级 1：显式传入的 self uid
    explicit_uid_text = _safe_str(explicit_self_uid)
    if explicit_uid_text:
        account_self_uid = explicit_uid_text

    # 优先级 2：auth.get_uid()
    if not account_self_uid and auth is not None:
        try:
            account_self_uid = _safe_str(auth.get_uid())
        except Exception:
            pass

    # 优先级 3：im_accounts.douyin_uid
    if not account_self_uid:
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT douyin_uid FROM im_accounts WHERE account_code = ? LIMIT 1",
                    (account_code,),
                ).fetchone()
                if row and row[0]:
                    account_self_uid = _safe_str(row[0])
        except Exception:
            pass

    # 优先级 4：profile_meta.im_uid
    cached_im_uid = load_profile_im_uid(account_code)

    known_self_senders: set[str] = set()
    for value in (account_self_uid, cached_im_uid):
        text = _safe_str(value)
        if text:
            known_self_senders.add(text)
    if known_self_senders_param:
        for raw in known_self_senders_param:
            text = _safe_str(raw)
            if text:
                known_self_senders.add(text)

    stats: Dict[str, Any] = {
        "total_conversations": 0,
        "normal_conversations": 0,
        "stranger_conversations": 0,
        "total_messages_pulled": 0,
        "source_ws": 0,
        "source_api": 0,
        "source_dom": 0,
        "messages_saved": 0,
        "messages_duplicate": 0,
        "messages_filtered_3d": 0,
        "messages_filtered_empty": 0,
        "type_text": 0,
        "type_emoji": 0,
        "type_voice": 0,
        "type_image": 0,
        "type_video": 0,
        "profiles_enriched": 0,
        "profiles_skipped": 0,
        "profiles_failed": 0,
        "errors": 0,
        "elapsed_seconds": 0,
    }

    logger.info(
        "[%s] 开始历史回补(异步): 3天截止=%s (距今 %d 秒)",
        account_code, _ts_to_str(cutoff_ts), three_days_seconds,
    )

    # Phase 1: 安装 WS 拦截 hook
    ws_installed = False
    if not _browser_bridge and not _playwright_page:
        logger.warning("无法注入 WS hook: 无浏览器实例")
    else:
        try:
            result = await _async_evaluate_js(_WS_HOOK_JS)
            if result in ('hooked', 'already_hooked'):
                ws_installed = True
                logger.info("WebSocket 拦截 hook 已注入")
            else:
                logger.warning("WS hook 注入返回: %s", result)
        except Exception as e:
            logger.warning("WS hook 注入失败: %s", e)

    if ws_installed:
        logger.info("[%s] WS hook 已安装，等待 5 秒预热...", account_code)
        await _aio.sleep(5)

    # Phase 2: 获取会话列表（仅 API）
    api_conversations = await _aio.to_thread(
        fetch_conversation_list_via_api,
        auth,
        self_uid=account_self_uid,
        cached_im_uid=cached_im_uid,
    )
    conversations: List[Dict[str, Any]] = list(api_conversations)
    if api_conversations:
        logger.info("[%s] 会话枚举(异步): 仅 API 成功，共 %d 个会话", account_code, len(conversations))

    if not conversations:
        logger.warning("[%s] 会话列表为空，跳过历史回补", account_code)
        if ws_installed:
            try:
                await _async_evaluate_js(_CLEAR_WS_HOOK_JS)
            except Exception:
                pass
        stats["elapsed_seconds"] = round(time.monotonic() - started_at, 2)
        return stats

    for conv in conversations:
        _bind_account_identity_to_conversation(
            conv,
            account_self_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
            known_self_senders=known_self_senders,
        )
        _persist_api_only_conversation(db_path, account_code, conv)

    stats["total_conversations"] = len(conversations)
    for conv in conversations:
        if conv.get("conversation_type", 1) != 1:
            stats["stranger_conversations"] += 1
        else:
            stats["normal_conversations"] += 1

    logger.info(
        "[%s] 会话列表: 总计=%d 普通=%d 陌生人=%d",
        account_code,
        stats["total_conversations"],
        stats["normal_conversations"],
        stats["stranger_conversations"],
    )

    # Phase 3: 轮询 WS 缓冲区
    ws_messages_all: List[Dict[str, Any]] = []
    try:
        raw_items = await _async_evaluate_js(_POLL_WS_BUFFER_JS.replace("500", "2000"))
        if raw_items and isinstance(raw_items, list):
            for item in raw_items:
                if item.get('type') == 'binary' and item.get('data'):
                    decoded = _decode_ws_frame_base64(item['data'])
                    ws_messages_all.extend(decoded)
    except Exception as e:
        logger.debug("轮询 WS buffer 失败: %s", e)

    ws_by_conversation = _group_ws_messages_by_conversation(ws_messages_all)
    logger.info("[%s] WS 捕获到 %d 个会话的消息", account_code, len(ws_by_conversation))

    # 先用 WS 映射补齐 short_id，尽量减少后续 DOM 点击。
    for conv in conversations:
        conv_id = str(conv.get("conversation_id") or "").strip()
        if conv_id and not conv.get("conversation_short_id"):
            ws_msgs = ws_by_conversation.get(conv_id, [])
            if ws_msgs:
                short_from_ws = ws_msgs[0].get("conversation_short_id", "")
                if short_from_ws:
                    conv["conversation_short_id"] = short_from_ws

    # Phase 4a: 先用 WS 回填缺失的 short_id。
    for conv in conversations:
        display_name = str(conv.get("display_name") or "").strip()
        conv_id = str(conv.get("conversation_id") or "").strip()
        try:
            if conv_id and not conv.get("conversation_short_id"):
                ws_msgs = ws_by_conversation.get(conv_id, [])
                if ws_msgs:
                    short_from_ws = ws_msgs[0].get("conversation_short_id", "")
                    if short_from_ws:
                        conv["conversation_short_id"] = short_from_ws
        except Exception as e:
            logger.error("[%s] 会话 %s 回补失败: %s", account_code, display_name or conv_id, e)
            stats["errors"] += 1

    # Phase 4b: 仅处理具备真实 ID 的会话。
    semaphore = _aio.Semaphore(max(1, int(max_concurrency)))
    api_ready_conversations: List[Dict[str, Any]] = []
    for conv in conversations:
        conv_id = str(conv.get("conversation_id") or "").strip()
        conv_short_id = str(conv.get("conversation_short_id") or "").strip()
        if conv_id and conv_short_id:
            api_ready_conversations.append(conv)
        else:
            logger.warning(
                "[%s] 跳过缺少会话标识的会话: name=%s conv_id=%s short_id=%s",
                account_code,
                str(conv.get("display_name") or "").strip() or "(无名)",
                conv_id,
                conv_short_id,
            )

    async def _run_api_ready_conversation(conv: Dict[str, Any]) -> None:
        conv_id_local = str(conv.get("conversation_id") or "").strip()
        display_name = str(conv.get("display_name") or "").strip()
        try:
            async with semaphore:
                await _async_backfill_single_conversation(
                    db_path=db_path,
                    account_code=account_code,
                    auth=auth,
                    conversation=conv,
                    cutoff_ts=cutoff_ts,
                    ws_messages=ws_by_conversation.get(conv_id_local, []),
                    stats=stats,
                )
        except Exception as e:
            logger.error("[%s] 会话 %s API 回补失败: %s", account_code, display_name or conv_id_local, e)
            stats["errors"] += 1

    if api_ready_conversations:
        logger.info(
            "[%s] API 回补会话=%d，并发=%d",
            account_code,
            len(api_ready_conversations),
            max(1, int(max_concurrency)),
        )
        await _aio.gather(*(_run_api_ready_conversation(conv) for conv in api_ready_conversations))

    profile_refresh = await _aio.to_thread(
        _refresh_missing_peer_profiles,
        db_path,
        account_code,
        auth,
        conversations,
    )
    stats["profiles_enriched"] += int(profile_refresh.get("enriched", 0) or 0)
    stats["profiles_skipped"] += int(profile_refresh.get("skipped", 0) or 0)
    stats["profiles_failed"] += int(profile_refresh.get("failed", 0) or 0)
    if stats["profiles_enriched"] or stats["profiles_failed"]:
        logger.info(
            "[%s] peer profile 补全完成(异步): enriched=%d skipped=%d failed=%d",
            account_code,
            stats["profiles_enriched"],
            stats["profiles_skipped"],
            stats["profiles_failed"],
        )

    # Phase 5: 最终 WS 轮询
    try:
        raw_final = await _async_evaluate_js(_POLL_WS_BUFFER_JS.replace("500", "2000"))
        if raw_final and isinstance(raw_final, list):
            for item in raw_final:
                if item.get('type') == 'binary' and item.get('data'):
                    for msg in _decode_ws_frame_base64(item['data']):
                        cid = msg.get("conversation_id", "")
                        if cid:
                            msg["account_self_uid"] = account_self_uid
                            msg["cached_im_uid"] = cached_im_uid
                            msg["known_self_senders"] = tuple(sorted(known_self_senders))
                            _save_message_to_db(db_path, account_code, cid, msg, msg.get("sender", ""), stats)
                            stats["source_ws"] += 1
    except Exception:
        pass

    # 清理
    if ws_installed:
        try:
            await _async_evaluate_js(_CLEAR_WS_HOOK_JS)
        except Exception:
            pass

    repaired_count = _repair_misclassified_self_messages(
        db_path,
        account_code,
        account_self_uid=account_self_uid,
        cached_im_uid=cached_im_uid,
        known_self_senders=known_self_senders,
    )
    if repaired_count:
        logger.info("[%s] 历史消息方向纠偏完成(异步): repaired=%d", account_code, repaired_count)

    collapsed_count = _collapse_history_direction_duplicates(db_path, account_code)
    if collapsed_count:
        logger.info("[%s] 历史消息重复折叠完成(异步): collapsed=%d", account_code, collapsed_count)

    stats["elapsed_seconds"] = round(time.monotonic() - started_at, 2)

    logger.info(
        "[%s] 历史回补完成(异步): 会话=%d 拉取=%d 入库=%d 重复=%d "
        "3天外=%d 空=%d 来源[ws=%d,api=%d,dom=%d] "
        "资料[ok=%d,skip=%d,fail=%d] "
        "类型[text=%d,emoji=%d,voice=%d,image=%d,video=%d] "
        "错误=%d 耗时=%.2fs",
        account_code,
        stats["total_conversations"],
        stats["total_messages_pulled"],
        stats["messages_saved"],
        stats["messages_duplicate"],
        stats["messages_filtered_3d"],
        stats["messages_filtered_empty"],
        stats["source_ws"],
        stats["source_api"],
        stats["source_dom"],
        stats["profiles_enriched"],
        stats["profiles_skipped"],
        stats["profiles_failed"],
        stats["type_text"],
        stats["type_emoji"],
        stats["type_voice"],
        stats["type_image"],
        stats["type_video"],
        stats["errors"],
        stats["elapsed_seconds"],
    )

    return stats


async def _async_backfill_single_conversation(
    db_path: str,
    account_code: str,
    auth,
    conversation: Dict[str, Any],
    cutoff_ts: float,
    ws_messages: List[Dict[str, Any]],
    stats: Dict[str, Any],
) -> None:
    """异步版单会话回补（仅 WS + API）。"""
    import asyncio as _aio

    conv_id = str(conversation.get("conversation_id") or "").strip()
    conv_short_id = str(conversation.get("conversation_short_id") or "").strip()
    display_name = str(conversation.get("display_name") or "").strip()
    peer_user_id = str(conversation.get("peer_user_id") or "").strip()
    account_self_uid = str(conversation.get("account_self_uid") or "").strip()
    cached_im_uid = str(conversation.get("cached_im_uid") or "").strip()
    known_self_senders = tuple(
        str(v or "").strip()
        for v in (conversation.get("known_self_senders") or ())
        if str(v or "").strip()
    )

    if not conv_short_id and ws_messages:
        conv_short_id = str(ws_messages[0].get("conversation_short_id") or "").strip()
        if conv_short_id:
            conversation["conversation_short_id"] = conv_short_id

    if not conv_id or not conv_short_id:
        logger.debug("[%s] 会话 %s 缺少 API 标识，跳过回补", account_code, display_name or "(无名)")
        return

    logger.info(
        "[%s] 会话回补开始: name=%s conv_id=%s short_id=%s",
        account_code, display_name or "(无名)", conv_id, conv_short_id,
    )

    # 仅在 display_name 是真实昵称时才写入；纯数字 peer_user_id 不作为最终展示名
    safe_display_name = display_name
    if _is_placeholder_profile_name(
        display_name,
        conversation_id=conv_id,
        peer_user_id=peer_user_id,
    ):
        safe_display_name = ""
    if safe_display_name or peer_user_id:
        upsert_conversation_profile(
            db_path, account_code, conv_id,
            display_name=safe_display_name,
            source="history_backfill",
            conversation_short_id=conv_short_id,
        )

    all_messages: List[Dict[str, Any]] = []

    # Source 1: WS 捕获
    if ws_messages:
        all_messages.extend(ws_messages)
        stats["source_ws"] += len(ws_messages)

    # Source 2: API 分页拉取
    try:
        conv_type = _safe_int(conversation.get("conversation_type"), 1)
        if conversation.get("source") == "api_stranger":
            api_messages = await _aio.to_thread(
                fetch_stranger_messages_via_api,
                auth,
                int(conv_short_id),
            )
        else:
            api_messages = await _aio.to_thread(
                backfill_conversation_via_api,
                auth,
                conv_id,
                int(conv_short_id),
                cutoff_ts,
                conversation_type=conv_type,
            )
        if api_messages:
            all_messages.extend(api_messages)
            stats["source_api"] += len(api_messages)
    except Exception as e:
        logger.debug("[%s] 会话 %s API 拉取失败: %s", account_code, display_name, e)

    if not all_messages:
        return

    # 双源去重
    ws_in_conv = [m for m in all_messages if m.get("source") == "ws"]
    api_in_conv = [m for m in all_messages if m.get("source") == "api"]
    deduped = merge_and_dedup_messages(ws_in_conv, api_in_conv, [])

    stats["total_messages_pulled"] += len(deduped)

    for msg in deduped:
        msg["account_self_uid"] = account_self_uid
        msg["cached_im_uid"] = cached_im_uid
        msg["known_self_senders"] = known_self_senders
        _save_message_to_db(
            db_path, account_code, conv_id, msg,
            peer_user_id or msg.get("sender", ""), stats,
        )

    logger.info(
        "[%s] 会话 %s 回补: 拉取=%d 去重后=%d 入库=%d",
        account_code, display_name or conv_id,
        len(all_messages), len(deduped), stats["messages_saved"],
    )


def _backfill_single_conversation_hybrid(
    db_path: str,
    account_code: str,
    auth,
    conversation: Dict[str, Any],
    cutoff_ts: float,
    ws_messages: List[Dict[str, Any]],
    stats: Dict[str, Any],
) -> None:
    """
    回补单个会话（仅 WS + API）。
    """
    conv_id = str(conversation.get("conversation_id") or "").strip()
    conv_short_id = str(conversation.get("conversation_short_id") or "").strip()
    display_name = str(conversation.get("display_name") or "").strip()
    peer_user_id = str(conversation.get("peer_user_id") or "").strip()
    account_self_uid = str(conversation.get("account_self_uid") or "").strip()
    cached_im_uid = str(conversation.get("cached_im_uid") or "").strip()
    known_self_senders = tuple(
        str(v or "").strip()
        for v in (conversation.get("known_self_senders") or ())
        if str(v or "").strip()
    )

    if not conv_short_id and ws_messages:
        conv_short_id = str(ws_messages[0].get("conversation_short_id") or "").strip()
        if conv_short_id:
            conversation["conversation_short_id"] = conv_short_id

    if not conv_id or not conv_short_id:
        logger.debug("[%s] 会话 %s 缺少 API 标识，跳过回补", account_code, display_name or "(无名)")
        return

    # 确保会话 profile 存在（不把纯数字 peer uid 当作最终展示名）
    safe_display_name = display_name
    if _is_placeholder_profile_name(
        display_name,
        conversation_id=conv_id,
        peer_user_id=peer_user_id,
    ):
        safe_display_name = ""
    if safe_display_name or peer_user_id:
        upsert_conversation_profile(
            db_path, account_code, conv_id,
            display_name=safe_display_name,
            source="history_backfill",
            conversation_short_id=conv_short_id,
        )

    all_messages: List[Dict[str, Any]] = []

    # Source 1: WS 捕获的消息
    if ws_messages:
        all_messages.extend(ws_messages)
        stats["source_ws"] += len(ws_messages)
        logger.debug("[%s] 会话 %s: WS 捕获 %d 条", account_code, display_name, len(ws_messages))

    # Source 2: API 分页拉取
    try:
        conv_type = _safe_int(conversation.get("conversation_type"), 1)
        if conversation.get("source") == "api_stranger":
            api_messages = fetch_stranger_messages_via_api(auth, int(conv_short_id))
        else:
            api_messages = backfill_conversation_via_api(
                auth,
                conv_id,
                int(conv_short_id),
                cutoff_ts,
                conversation_type=conv_type,
            )
        if api_messages:
            all_messages.extend(api_messages)
            stats["source_api"] += len(api_messages)
            logger.debug("[%s] 会话 %s: API 获取 %d 条", account_code, display_name, len(api_messages))
    except Exception as e:
        logger.debug("[%s] 会话 %s API 拉取失败: %s", account_code, display_name, e)

    if not all_messages:
        return

    # 双源去重
    ws_in_conv = [m for m in all_messages if m.get("source") == "ws"]
    api_in_conv = [m for m in all_messages if m.get("source") == "api"]
    deduped = merge_and_dedup_messages(ws_in_conv, api_in_conv, [])

    stats["total_messages_pulled"] += len(deduped)

    # 保存到数据库
    for msg in deduped:
        msg["account_self_uid"] = account_self_uid
        msg["cached_im_uid"] = cached_im_uid
        msg["known_self_senders"] = known_self_senders
        _save_message_to_db(
            db_path, account_code, conv_id, msg,
            peer_user_id or msg.get("sender", ""), stats,
        )

    logger.info(
        "[%s] 会话 %s 回补: 拉取=%d 去重后=%d 入库=%d",
        account_code, display_name or conv_id,
        len(all_messages), len(deduped), stats["messages_saved"],
    )


def backfill_and_persist_conversation_history(
    db_path: str,
    account_code: str,
    auth,
    conversation_id: str,
    *,
    conversation_short_id: int = 0,
    peer_user_id: str = "",
    conversation_type: int = 1,
    max_pages: int = 10,
) -> Dict[str, int]:
    """单会话 3 天内历史回补并落库（新会话主动发送后懒拉）。"""
    path = str(db_path or "").strip()
    code = str(account_code or "").strip()
    conv_id = str(conversation_id or "").strip()
    peer = str(peer_user_id or "").strip()
    stats: Dict[str, int] = {
        "messages_saved": 0,
        "messages_duplicate": 0,
        "messages_filtered_empty": 0,
        "messages_filtered_3d": 0,
    }
    if not path or not code or not conv_id:
        return stats

    short_id = int(conversation_short_id or 0)
    if short_id <= 0:
        try:
            ensure_message_tables(path)
            conn = sqlite3.connect(path, timeout=10)
            try:
                row = conn.execute(
                    """
                    SELECT conversation_short_id
                    FROM conversation_profiles
                    WHERE profile_id = ? AND conversation_id = ?
                    LIMIT 1
                    """,
                    (code, conv_id),
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                short_id = _safe_int(row[0], 0)
        except Exception:
            short_id = 0

    if short_id <= 0:
        logger.debug(
            "[%s] 单会话历史回补跳过: conversation_id=%s 缺少 short_id",
            code,
            conv_id,
        )
        return stats

    cutoff_ts = _now_ts() - THREE_DAYS_SECONDS
    try:
        if conversation_type == 0:
            api_messages = fetch_stranger_messages_via_api(auth, short_id)
        else:
            api_messages = backfill_conversation_via_api(
                auth,
                conv_id,
                short_id,
                cutoff_ts,
                conversation_type=conversation_type,
                max_pages=max_pages,
            )
    except Exception as exc:
        logger.warning(
            "[%s] 单会话历史回补 API 失败: conversation_id=%s error=%s",
            code,
            conv_id,
            exc,
        )
        return stats

    if not api_messages:
        return stats

    account_self_uid = ""
    cached_im_uid = ""
    try:
        account_self_uid = str(load_profile_im_uid(path, code) or "").strip()
    except Exception:
        account_self_uid = ""
    if not peer and conv_id:
        parts = conv_id.split(":")
        if len(parts) >= 2 and str(parts[-1]).isdigit():
            peer = str(parts[-1])

    for msg in sorted(
        api_messages,
        key=lambda item: (
            _normalize_im_timestamp(item.get("create_time", 0)),
            _safe_int(item.get("index_in_conversation"), 0),
        ),
    ):
        msg["account_self_uid"] = account_self_uid
        msg["cached_im_uid"] = cached_im_uid
        _save_message_to_db(
            path,
            code,
            conv_id,
            msg,
            peer or str(msg.get("sender") or "").strip(),
            stats,
            touch_realtime_activity=False,
            # 发送后懒拉：打开内容窗去重，避免刚 IPC 落库的出站再被历史 API 写第二份。
            allow_content_window_dedupe=True,
        )

    logger.info(
        "[%s] 单会话历史回补完成: conversation_id=%s saved=%d duplicate=%d",
        code,
        conv_id,
        int(stats.get("messages_saved") or 0),
        int(stats.get("messages_duplicate") or 0),
    )
    return stats


def _cli_main() -> int:
    import argparse
    import sys
    from pathlib import Path

    if __package__ is None or __package__ == "":
        sys.path.append(str(Path(__file__).resolve().parent.parent))

    from utils.common_util import build_im_auth_from_credentials
    from utils.im_account_store import load_im_accounts_from_db, validate_im_account_credentials

    parser = argparse.ArgumentParser(description="抖音私信 3 天历史回补（API 优先）")
    parser.add_argument("--account-code", required=True, help="账号标识")
    parser.add_argument("--db-path", required=True, help="SQLite 数据库路径")
    args = parser.parse_args()

    accounts = load_im_accounts_from_db(
        args.db_path,
        account_code=args.account_code,
        enabled_only=False,
    )
    if not accounts:
        raise SystemExit(f"未找到账号: {args.account_code}")
    if len(accounts) > 1:
        raise SystemExit(f"账号重复: {args.account_code}")
    account = accounts[0]
    validate_im_account_credentials(account)
    auth = build_im_auth_from_credentials(
        account.cookies_str,
        account.web_protect_str,
        account.keys_str,
    )
    result = backfill_account_history(
        args.db_path,
        args.account_code,
        auth,
        explicit_self_uid=str(account.douyin_uid or "").strip(),
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
