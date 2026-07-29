import argparse
from collections import deque
import hashlib
import json
import logging
import sqlite3
import sys
import threading
import time
from pathlib import Path

import websocket
from websocket import WebSocketApp

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from utils.log_util import get_logger

get_logger(__name__)
logger = logging.getLogger(__name__)

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from dy_apis.douyin_api import DouyinAPI
from builder.auth import DouyinAuth
from builder.header import HeaderBuilder
from builder.params import Params
from static import Live_pb2, Response_pb2
from utils.common_util import build_im_auth_from_credentials, load_im_auth, load_project_env
from utils.im_account_contract import AccountStatus
from utils.im_account_manager import RuntimeStatusReporter, RuntimeStatusWatcher, prefixed_account_logs
from utils.im_account_store import (
    InvalidIMAccountCredentials,
    load_im_accounts_from_db,
    validate_im_account_credentials,
)
from utils.im_message_store import (
    ensure_message_tables,
    save_inbound_message,
    save_outbound_message,
    upsert_conversation_profile,
    upsert_self_profile,
)
from utils.im_identity import is_im_self_sender, resolve_peer_participant
from utils.im_media_cache import (
    cache_inline_image_base64,
    ensure_douyin_image_cached,
    extract_douyin_image_url,
    extract_douyin_image_url_from_payload,
    normalize_douyin_image_content,
)
from utils.im_profile_enricher import enrich_peer_profile, enrich_self_profile
from utils.im_reply_engine import IMReplyEngine
from utils.im_runtime_config import api_fallback_on_disconnect_allowed
from utils.profile_auto_reply import (
    is_profile_auto_reply_enabled,
    load_profile_im_uid,
    save_profile_im_uid,
)


MANAGED_RUNTIME_MAX_RECONNECT_FAILURES = 5
MANAGED_RUNTIME_RECONNECT_DELAY_SECONDS = 3.0
API_FALLBACK_POLL_INTERVAL_SECONDS = 4.0
API_FALLBACK_RECENT_MESSAGE_LIMIT = 10
API_FALLBACK_SEEN_KEYS_PER_CONVERSATION = 256


def _safe_console_text(text):
    encoding = sys.stdout.encoding or "utf-8"
    return str(text).encode(encoding, errors="backslashreplace").decode(encoding, errors="ignore")


def _parse_im_message_content(raw_content):
    """解析 IM 消息 ``content`` 字段；空串或非 JSON 时返回 ``{}`` 而不抛异常。"""
    if isinstance(raw_content, dict):
        return raw_content
    if isinstance(raw_content, (bytes, bytearray)):
        raw_content = raw_content.decode("utf-8", errors="ignore")
    text = str(raw_content or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("IM message content is not JSON: %r", text[:200])
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {"_value": parsed}


def _extract_realtime_image_text(payload: dict) -> tuple[str, str]:
    """统一提取主站实时图片消息的展示文案与主图 URL。"""
    if not isinstance(payload, dict):
        return "[图片]", ""
    resource = payload.get("resource_url") or {}
    url_list = []
    if isinstance(resource, dict):
        for key in (
            "origin_url_list",
            "large_url_list",
            "medium_url_list",
            "thumb_url_list",
            "url_list",
        ):
            value = resource.get(key) or []
            if isinstance(value, list) and value:
                url_list = value
                break
    primary_url = str(url_list[0] or "").strip() if url_list else ""
    text = f"[图片] {primary_url}" if primary_url else "[图片]"
    return text, primary_url


def _is_realtime_video_message(msg_type, payload: dict) -> bool:
    """主站视频兼容：老类型 8 + 新类型 30（payload 内含 video 对象）。"""
    try:
        numeric_type = int(msg_type)
    except (TypeError, ValueError):
        numeric_type = 0
    if numeric_type == 8:
        return True
    if not isinstance(payload, dict):
        return False
    video_payload = payload.get("video")
    if numeric_type == 30 and isinstance(video_payload, dict):
        return bool(
            str(video_payload.get("vid") or "").strip()
            or str(video_payload.get("tkey") or "").strip()
            or str(video_payload.get("md5") or "").strip()
        )
    return False


def _is_realtime_image_message(msg_type, payload: dict) -> bool:
    """主站实时图片兼容：已知 27/11 + 任意可识别图片载荷。"""
    if _is_realtime_video_message(msg_type, payload):
        return False
    try:
        numeric_type = int(msg_type)
    except (TypeError, ValueError):
        numeric_type = 0
    if numeric_type in {11, 27}:
        return True
    if not isinstance(payload, dict):
        return False
    if str(payload.get("inline_pic") or "").strip():
        return True
    return bool(extract_douyin_image_url_from_payload(payload))


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_im_timestamp(value) -> int:
    ts = _safe_int(value, 0)
    if ts <= 0:
        return 0
    if ts > 10**12:
        return ts // 1000
    return ts


def _build_im_message_identity_key(
    *,
    conversation_id="",
    server_message_id="",
    index_in_conversation=0,
    create_time=0,
    sender_id="",
    msg_type="",
    content="",
) -> str:
    conv = str(conversation_id or "").strip()
    sid = str(server_message_id or "").strip()
    if sid and sid != "0":
        return f"sid:{conv}:{sid}"
    idx = _safe_int(index_in_conversation, 0)
    if idx > 0:
        return f"idx:{conv}:{idx}"
    digest = hashlib.blake2s(
        f"{conv}|{_normalize_im_timestamp(create_time)}|{sender_id}|{msg_type}|{content}".encode(
            "utf-8",
            errors="ignore",
        ),
        digest_size=10,
    ).hexdigest()
    return f"hash:{conv}:{digest}"


def _ts_to_str(ts: int) -> str:
    normalized = _normalize_im_timestamp(ts)
    if normalized <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(normalized))
    except (ValueError, OSError):
        return ""


def _reply_after_inbound(source_created_at: str) -> str:
    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    source = str(source_created_at or "").strip()
    if not source:
        return now_str
    try:
        source_ts = time.mktime(time.strptime(source, "%Y-%m-%d %H:%M:%S"))
        reply_ts = max(time.time(), source_ts + 1.0)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(reply_ts))
    except (ValueError, OSError):
        return now_str


def _auto_reply_outbound_times(task) -> tuple[str, str]:
    confirmed_ts = int(getattr(task, "confirmed_create_time", 0) or 0)
    if confirmed_ts > 0:
        created_at = _ts_to_str(confirmed_ts)
    else:
        created_at = _reply_after_inbound(getattr(task, "source_created_at", ""))
    if not created_at:
        created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    return created_at, created_at


class DouyinRecvMsg:
    appKey = "e1bd35ec9db7b8d846de66ed140b1ad9"
    fpId = '9'

    def __init__(
        self,
        auth: DouyinAuth,
        auto_reconnect=True,
        reply_engine=None,
        account_code="default",
        status_reporter=None,
        max_reconnect_failures=0,
        reconnect_delay_seconds=3.0,
        message_db_path="",
        self_user_id="",
        message_event_handler=None,
        ram_first=False,
    ):
        self.auto_reconnect = auto_reconnect
        self.auth = auth
        self.ws = None
        self.reply_engine = reply_engine
        self.account_code = account_code or "default"
        self.status_reporter = status_reporter
        self.max_reconnect_failures = max(0, int(max_reconnect_failures or 0))
        self.reconnect_delay_seconds = max(0.0, float(reconnect_delay_seconds or 0.0))
        self.message_db_path = str(message_db_path or "").strip()
        self.self_user_id = str(self_user_id or "").strip()
        self._cached_im_uid = ""
        self._known_self_im_uids: set[str] = set()
        if self.self_user_id:
            self._known_self_im_uids.add(self.self_user_id)
        if self.account_code and self.account_code != "default":
            self._cached_im_uid = load_profile_im_uid(self.account_code)
            if self._cached_im_uid:
                self._known_self_im_uids.add(self._cached_im_uid)
        self._load_known_self_im_uids_from_db()
        self._recent_message_keys: dict[str, set[str]] = {}
        self._recent_message_order: dict[str, deque[str]] = {}
        self._api_fallback_thread = None
        self._api_fallback_stop = threading.Event()
        # fallback 时间闸门：仅落"WebSocket 启动之后"的消息，阻断首次拉取时服务端保留的
        # 3 天历史消息灌入本地库。0 表示未初始化；在 _start_api_fallback_thread 首次启动时置位。
        # 老于此时间戳的消息仍会被登记进 _recent_message_keys 去重集合，避免后续 WebSocket
        # 推到相同消息时二次落库。单位：秒（Unix 时间戳）。
        self._api_fallback_min_create_time: int = 0
        self.message_event_handler = message_event_handler
        self.ram_first = bool(ram_first)
        self._profile_refreshing = set()
        self.connected_event = threading.Event()
        self.closed_event = threading.Event()
        self.error_event = threading.Event()
        self.opened_at = None
        self.last_error = ""
        self.stop_requested = False
        self._last_close_detail = ""
        self._connected_once_in_cycle = False
        self._connection_open_count = 0
        self._consecutive_reconnect_failures = 0
        deviceId = DouyinAPI.get_device_id(auth=self.auth)
        accessKey = f'{self.fpId + self.appKey + deviceId}f8a69f1719916z'
        accessKey = hashlib.md5(accessKey.encode(encoding='UTF-8')).hexdigest()
        params = Params()
        (params
         .add_param("aid", "6383")
         .add_param("device_platform", "douyin_pc")
         .add_param("fpid", self.fpId)
         .add_param("device_id", deviceId)
         .add_param("token", self.auth.cookie["sessionid"])
         .add_param("access_key", accessKey)
         )
        self.url = f"wss://frontier-im.douyin.com/ws/v2?{params.toString()}"

    def _log(self, text):
        logger.info("[%s] %s", self.account_code, _safe_console_text(text))

    def _remember_message_key(self, conversation_id: str, key: str) -> None:
        conv = str(conversation_id or "").strip()
        dedup_key = str(key or "").strip()
        if not conv or not dedup_key:
            return
        seen = self._recent_message_keys.setdefault(conv, set())
        order = self._recent_message_order.setdefault(
            conv,
            deque(maxlen=API_FALLBACK_SEEN_KEYS_PER_CONVERSATION),
        )
        if dedup_key in seen:
            return
        if len(order) >= order.maxlen:
            oldest = order.popleft()
            if oldest:
                seen.discard(oldest)
        order.append(dedup_key)
        seen.add(dedup_key)

    def _has_seen_message_key(self, conversation_id: str, key: str) -> bool:
        conv = str(conversation_id or "").strip()
        dedup_key = str(key or "").strip()
        if not conv or not dedup_key:
            return False
        return dedup_key in self._recent_message_keys.get(conv, set())

    def _load_api_fallback_helpers(self):
        from dy_apis.im_history_backfill import (
            fetch_conversation_list_via_api,
            fetch_history_via_api,
            fetch_stranger_messages_via_api,
            _persist_api_only_conversation,
        )

        return (
            fetch_conversation_list_via_api,
            fetch_history_via_api,
            fetch_stranger_messages_via_api,
            _persist_api_only_conversation,
        )

    def _persist_api_message(self, conversation: dict, msg: dict) -> bool:
        conversation_id = str(conversation.get("conversation_id") or msg.get("conversation_id") or "").strip()
        sender = str(msg.get("sender") or "").strip()
        if not conversation_id or not sender:
            return False
        if self._is_conversation_blocked(conversation_id):
            return False
        server_message_id = str(msg.get("server_message_id") or "").strip()
        index_in_conversation = _safe_int(msg.get("index_in_conversation"), 0)
        create_time = _normalize_im_timestamp(msg.get("create_time", 0))
        msg_type = str(msg.get("msg_type") or "text").strip() or "text"
        content = msg.get("content_payload") if msg_type == "image" else msg.get("content")
        identity_key = _build_im_message_identity_key(
            conversation_id=conversation_id,
            server_message_id=server_message_id,
            index_in_conversation=index_in_conversation,
            create_time=create_time,
            sender_id=sender,
            msg_type=msg_type,
            content=str(msg.get("content") or ""),
        )
        if self._has_seen_message_key(conversation_id, identity_key):
            return False
        # 时间闸门：老于 WebSocket 启动时刻的消息一律不落库（等效于"不做 3 天历史回补"）。
        # 但仍登记 identity_key 到去重集合，避免后续 WebSocket 推到同一条消息时二次落库。
        if (
            self._api_fallback_min_create_time > 0
            and create_time > 0
            and create_time < self._api_fallback_min_create_time
        ):
            self._remember_message_key(conversation_id, identity_key)
            return False
        unique_token = server_message_id or str(index_in_conversation or "").strip()
        if not unique_token:
            unique_token = identity_key
        self._persist_message(
            conversation_id,
            sender,
            content,
            msg_type,
            unique_token,
            conversation_short_id=str(
                conversation.get("conversation_short_id")
                or msg.get("conversation_short_id")
                or ""
            ).strip(),
            created_at=_ts_to_str(create_time),
        )
        self._remember_message_key(conversation_id, identity_key)
        return True

    def _poll_recent_messages_via_api(self) -> None:
        if not self.message_db_path or not self.account_code or self.stop_requested:
            return
        (
            fetch_conversation_list_via_api,
            fetch_history_via_api,
            fetch_stranger_messages_via_api,
            persist_api_only_conversation,
        ) = self._load_api_fallback_helpers()
        conversations = fetch_conversation_list_via_api(
            self.auth,
            self_uid=self.self_user_id,
            cached_im_uid=self._cached_im_uid,
        )
        if not conversations:
            return
        for conv in conversations:
            conv["account_self_uid"] = self.self_user_id
            conv["cached_im_uid"] = self._cached_im_uid
            conv["known_self_senders"] = tuple(sorted(self._known_self_im_uids))

        ranked = sorted(
            conversations,
            key=lambda item: (
                _safe_int(item.get("unread_count"), 0),
                _safe_int(item.get("last_message_time"), 0),
            ),
            reverse=True,
        )
        total_persisted = 0
        for conversation in ranked[:8]:
            conversation_id = str(conversation.get("conversation_id") or "").strip()
            conversation_short_id = str(conversation.get("conversation_short_id") or "").strip()
            if not conversation_id or not conversation_short_id:
                continue
            try:
                persist_api_only_conversation(
                    self.message_db_path,
                    self.account_code,
                    conversation,
                )
            except Exception:
                pass

            try:
                if conversation.get("source") == "api_stranger":
                    messages = fetch_stranger_messages_via_api(
                        self.auth,
                        int(conversation_short_id),
                    )
                else:
                    messages, _has_more, _next_cursor = fetch_history_via_api(
                        self.auth,
                        conversation_id,
                        int(conversation_short_id),
                        conversation_type=_safe_int(conversation.get("conversation_type"), 1),
                        anchor_index=0,
                        limit=API_FALLBACK_RECENT_MESSAGE_LIMIT,
                        direction=1,
                    )
            except Exception as exc:
                logger.debug(
                    "[%s] API fallback 拉取消息失败 conversation_id=%s error=%s",
                    self.account_code,
                    conversation_id,
                    exc,
                )
                continue

            if not messages:
                continue

            persisted = 0
            for msg in sorted(
                messages,
                key=lambda item: (
                    _normalize_im_timestamp(item.get("create_time", 0)),
                    _safe_int(item.get("index_in_conversation"), 0),
                ),
            ):
                if self._persist_api_message(conversation, msg):
                    persisted += 1
            if persisted:
                total_persisted += persisted
                self._log(
                    f"API 增量补漏: conversation_id={conversation_id} "
                    f"新增消息 {persisted} 条"
                )
        if total_persisted:
            self._log(f"API 增量补漏完成: 新增 {total_persisted} 条消息")

    def _api_fallback_loop(self) -> None:
        # 启动后稍等，避免和登录初始化/历史回补阶段抢请求。
        if self._api_fallback_stop.wait(API_FALLBACK_POLL_INTERVAL_SECONDS):
            return
        while not self.stop_requested and not self._api_fallback_stop.is_set():
            try:
                self._poll_recent_messages_via_api()
            except Exception as exc:
                logger.debug("[%s] API fallback poll 失败: %s", self.account_code, exc)
            if self._api_fallback_stop.wait(API_FALLBACK_POLL_INTERVAL_SECONDS):
                break

    def _start_api_fallback_thread(self) -> None:
        # 说明：本线程每 4 秒调 fetch_conversation_list_via_api + fetch_history_via_api，
        # 拉 top-8 活跃会话的最新 10 条消息作为 WebSocket 的实时兜底。
        # 仅在 WebSocket 断线时启用；连接正常时由 on_open 停止轮询。
        if not api_fallback_on_disconnect_allowed():
            return
        if self._api_fallback_thread is not None and self._api_fallback_thread.is_alive():
            return
        # 首次启动时初始化时间闸门：过滤掉 WebSocket 启动之前的历史消息，与来客 realtime_only 语义对齐。
        # buffer=60s 覆盖启动过程的时钟漂移，避免误伤刚刚才产生但时间戳略早的实时消息。
        if self._api_fallback_min_create_time <= 0:
            self._api_fallback_min_create_time = int(time.time()) - 60
            try:
                self._log(
                    "API fallback 时间闸门已启用: min_create_time=%d (仅落此时间之后的消息)"
                    % self._api_fallback_min_create_time
                )
            except Exception:
                pass
        self._api_fallback_stop.clear()
        self._api_fallback_thread = threading.Thread(
            target=self._api_fallback_loop,
            name=f"dy-api-fallback-{self.account_code}",
            daemon=True,
        )
        self._api_fallback_thread.start()

    def _stop_api_fallback_thread(self) -> None:
        self._api_fallback_stop.set()
        thread = self._api_fallback_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._api_fallback_thread = None

    def _maybe_start_api_fallback_on_disconnect(self) -> None:
        if self.stop_requested or self.connected_event.is_set():
            return
        self._start_api_fallback_thread()

    def on_open(self, ws):
        self.opened_at = time.monotonic()
        self.connected_event.set()
        self._connected_once_in_cycle = True
        self._connection_open_count += 1
        self._consecutive_reconnect_failures = 0
        self.last_error = ""
        self._last_close_detail = ""
        self._log("WebSocket connection open.")
        if self._connection_open_count > 1:
            self._log("WebSocket reconnected.")
        self._stop_api_fallback_thread()

    def on_message(self, ws, message):
        try:
            frame = Live_pb2.PushFrame()
            frame.ParseFromString(message)
            if frame.payloadType == 'pb':
                response = Response_pb2.Response()
                response.ParseFromString(frame.payload)
                sender = response.body.new_message_notify.message.sender
                content = response.body.new_message_notify.message.content
                msg_type = response.body.new_message_notify.message.message_type
                conversation_id = response.body.new_message_notify.message.conversation_id
                conversation_type = response.body.new_message_notify.message.conversation_type
                # conversation_type: 1=私聊(含陌生人), 2=群聊; 只跳过群聊
                if conversation_type == 2:
                    return
                if self._is_conversation_blocked(conversation_id):
                    self._log(
                        f"消息跳过（会话已拉黑）: account={self.account_code} conversation_id={conversation_id}"
                    )
                    return
                index = response.body.new_message_notify.message.index_in_conversation
                server_message_id = response.body.new_message_notify.message.server_message_id
                conversation_short_id = response.body.new_message_notify.message.conversation_short_id
                create_time = getattr(
                    response.body.new_message_notify.message,
                    "create_time",
                    0,
                )
                raw_content = content
                identity_key = _build_im_message_identity_key(
                    conversation_id=conversation_id,
                    server_message_id=server_message_id,
                    index_in_conversation=index,
                    create_time=create_time,
                    sender_id=sender,
                    msg_type=msg_type,
                    content=str(raw_content or ""),
                )
                content = _parse_im_message_content(content)
                if msg_type == 7:
                    text = str(content.get("text") or "").strip()
                    if not text:
                        return
                    self._log(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】文本消息:{text}')
                    own_message = self._is_self_sender(sender, conversation_id)
                    persisted = self._persist_message(
                        conversation_id,
                        sender,
                        text,
                        "text",
                        server_message_id or index,
                        conversation_short_id=conversation_short_id,
                        created_at=_ts_to_str(create_time),
                    )
                    if persisted:
                        self._remember_message_key(conversation_id, identity_key)
                    if self.reply_engine is None:
                        self._log(
                            f"文本消息未进入自动回复: 未配置 reply_engine sender={sender} conversation_id={conversation_id}"
                        )
                    elif own_message:
                        if self.reply_engine is not None:
                            try:
                                self.reply_engine.notify_outbound_echo(
                                    conversation_id=conversation_id,
                                    sender=sender,
                                    text=text,
                                    index=index,
                                    server_message_id=server_message_id,
                                    conversation_short_id=conversation_short_id,
                                )
                            except Exception as exc:
                                self._log(f"自动回复回显确认失败: {exc}")
                        self._log(
                            f"文本消息跳过自动回复: 当前是账号自身消息 sender={sender} conversation_id={conversation_id}"
                        )
                    elif self.ram_first:
                        self._log(
                            f"文本消息跳过子进程自动回复: ram_first 由主进程 AI 管线处理 account={self.account_code} conversation_id={conversation_id}"
                        )
                    elif not self._is_auto_reply_enabled():
                        self._log(
                            f"文本消息跳过自动回复: auto_reply 已关闭 account={self.account_code} conversation_id={conversation_id}"
                        )
                    elif self._is_conversation_dismissed(conversation_id):
                        self._log(
                            f"文本消息跳过自动回复: 会话已结束 account={self.account_code} conversation_id={conversation_id}"
                        )
                    elif self._is_conversation_blocked(conversation_id):
                        self._log(
                            f"文本消息跳过自动回复: 会话已拉黑 account={self.account_code} conversation_id={conversation_id}"
                        )
                    else:
                        self._log(
                            f"文本消息准备进入自动回复: sender={sender} conversation_id={conversation_id} text_len={len(text)}"
                        )
                        self.reply_engine.enqueue_text_message(
                            sender,
                            conversation_id,
                            index,
                            text,
                            source_created_at=_ts_to_str(create_time),
                        )
                elif msg_type == 5:
                    url_list = (content.get("url") or {}).get("url_list") or []
                    if not url_list:
                        return
                    text = f'[表情] {url_list[0]}'
                    self._log(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】用户表情包消息:{url_list[0]}')
                    if self._persist_message(
                        conversation_id,
                        sender,
                        text,
                        "emoji",
                        server_message_id or index,
                        conversation_short_id=conversation_short_id,
                        created_at=_ts_to_str(create_time),
                    ):
                        self._remember_message_key(conversation_id, identity_key)
                elif msg_type == 17:
                    url_list = (content.get("resource_url") or {}).get("url_list") or []
                    if not url_list:
                        return
                    text = f'[语音] {url_list[0]}'
                    self._log(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】语音信息:{url_list[0]}')
                    if self._persist_message(
                        conversation_id,
                        sender,
                        text,
                        "voice",
                        server_message_id or index,
                        conversation_short_id=conversation_short_id,
                        created_at=_ts_to_str(create_time),
                    ):
                        self._remember_message_key(conversation_id, identity_key)
                elif _is_realtime_video_message(msg_type, content):
                    video_payload = content.get("video") if isinstance(content, dict) else {}
                    item_id = str(
                        content.get("itemId")
                        or content.get("item_id")
                        or (video_payload or {}).get("vid")
                        or (video_payload or {}).get("tkey")
                        or ""
                    ).strip()
                    text = f'[视频] {item_id}' if item_id else '[视频]'
                    self._log(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】视频信息:{item_id or "no_id"}')
                    video_store_payload = dict(content) if isinstance(content, dict) else {}
                    video_store_payload["_display_text"] = text
                    if self._persist_message(
                        conversation_id,
                        sender,
                        video_store_payload,
                        "video",
                        server_message_id or index,
                        conversation_short_id=conversation_short_id,
                        created_at=_ts_to_str(create_time),
                    ):
                        self._remember_message_key(conversation_id, identity_key)
                elif _is_realtime_image_message(msg_type, content):
                    text, primary_url = _extract_realtime_image_text(content)
                    inline_pic = str(content.get("inline_pic") or "").strip()
                    if not primary_url and not inline_pic:
                        return
                    self._log(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】图片信息:{primary_url or "inline_pic"}')
                    image_payload = dict(content)
                    image_payload["_display_text"] = text
                    if self._persist_message(
                        conversation_id,
                        sender,
                        image_payload,
                        "image",
                        server_message_id or index,
                        conversation_short_id=conversation_short_id,
                        created_at=_ts_to_str(create_time),
                    ):
                        self._remember_message_key(conversation_id, identity_key)
                elif msg_type == 8:
                    item_id = str(content.get("itemId") or "").strip()
                    if not item_id:
                        return
                    text = f'[分享视频] 视频ID {item_id}'
                    self._log(f'【消息编号:{index}】【聊天室ID:{conversation_id}】【来自:{sender}】分享视频信息:视频ID{item_id}')
                    if self._persist_message(
                        conversation_id,
                        sender,
                        text,
                        "video",
                        server_message_id or index,
                        conversation_short_id=conversation_short_id,
                        created_at=_ts_to_str(create_time),
                    ):
                        self._remember_message_key(conversation_id, identity_key)
                elif msg_type == 50001:
                    read_index = content.get("read_index")
                    if read_index is not None:
                        self._log(f'对方已读，消息标号:{read_index}')
                    else:
                        self._log(f'对方已读事件:{content}')
                elif msg_type not in {1, 2}:
                    self._log(
                        f"未识别消息类型: type={msg_type} conversation_id={conversation_id} "
                        f"sender={sender} content={str(content)[:300]}"
                    )
            elif frame.payloadType == 'text/json':
                payload_text = (frame.payload or b"").decode("utf-8", errors="ignore").strip()
                if payload_text:
                    try:
                        self._log(json.loads(payload_text))
                    except json.JSONDecodeError:
                        self._log(payload_text)
        except Exception as e:
            self._log(f"处理私信消息失败: {e}")
            logger.exception("[%s] 处理私信消息失败", self.account_code)

    def _load_known_self_im_uids_from_db(self) -> None:
        if not self.message_db_path or not self.account_code:
            return
        try:
            with sqlite3.connect(self.message_db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT from_user_id
                    FROM messages
                    WHERE account_profile_id = ?
                      AND direction = 'outbound'
                      AND IFNULL(from_user_id, '') != ''
                    """,
                    (self.account_code,),
                ).fetchall()
            for row in rows:
                uid = str(row[0] or "").strip()
                if uid:
                    self._known_self_im_uids.add(uid)
        except Exception:
            pass

    def _remember_im_uid(self, sender_id: str) -> None:
        sender = str(sender_id or "").strip()
        if not sender:
            return
        self._known_self_im_uids.add(sender)
        if sender == self._cached_im_uid:
            return
        self._cached_im_uid = sender
        if self.account_code and self.account_code != "default":
            save_profile_im_uid(self.account_code, sender)

    def _is_self_sender(self, sender_id, conversation_id=""):
        return is_im_self_sender(
            sender_id,
            conversation_id,
            douyin_uid=self.self_user_id,
            cached_im_uid=self._cached_im_uid,
            known_self_senders=frozenset(self._known_self_im_uids),
        )

    def _is_auto_reply_enabled(self) -> bool:
        """读取侧栏 ``cb_ai`` 持久化的 ``profile_meta.auto_reply``。"""
        return is_profile_auto_reply_enabled(self.account_code)

    def _is_conversation_dismissed(self, conversation_id: str) -> bool:
        conv_id = str(conversation_id or "").strip()
        account_code = str(self.account_code or "").strip()
        if not account_code or not conv_id:
            return False
        try:
            from channels.douyin_all_user.reverse_runtime.utils.aggregate_dismiss_check import (
                is_douyin_conversation_dismissed,
            )

            return is_douyin_conversation_dismissed(account_code, conv_id)
        except Exception:
            return False

    def _is_conversation_blocked(self, conversation_id: str) -> bool:
        conv_id = str(conversation_id or "").strip()
        account_code = str(self.account_code or "").strip()
        if not account_code or not conv_id:
            return False
        try:
            from channels.douyin_all_user.reverse_runtime.utils.aggregate_block_check import (
                is_douyin_conversation_blocked,
            )

            return is_douyin_conversation_blocked(account_code, conv_id)
        except Exception:
            return False

    def _peer_user_id(self, conversation_id, sender_id):
        return resolve_peer_participant(
            conversation_id,
            sender_id,
            douyin_uid=self.self_user_id,
            cached_im_uid=self._cached_im_uid,
        )

    def _schedule_peer_profile_refresh(self, peer_user_id, conversation_id):
        peer = str(peer_user_id or "").strip()
        if not peer or not self.message_db_path or peer in self._profile_refreshing:
            return
        self._profile_refreshing.add(peer)

        def worker():
            try:
                enrich_peer_profile(
                    self.message_db_path,
                    self.account_code,
                    self.auth,
                    peer,
                    conversation_id,
                )
            except Exception as exc:
                self._log(f"补全私信用户资料失败:{peer} {exc}")
            finally:
                self._profile_refreshing.discard(peer)

        threading.Thread(target=worker, name=f"dy-profile-{peer}", daemon=True).start()

    def _peer_profile_needs_refresh(self, conversation_id, peer_user_id) -> bool:
        """出站 self 回显时：仅弱名/无头像会话才补全，对齐入站 enrich 策略。"""
        conv = str(conversation_id or "").strip()
        peer = str(peer_user_id or "").strip()
        if not conv or not peer.isdigit() or not self.message_db_path:
            return False
        try:
            from utils.im_message_store import _find_existing_profile

            existing = _find_existing_profile(
                self.message_db_path,
                self.account_code,
                conv,
            )
        except Exception:
            existing = ""
        text = str(existing or "").strip()
        if not text:
            return True
        if text.startswith("0:") or text.startswith("1:"):
            return True
        if text == conv or text == peer:
            return True
        return text.isdigit()

    def _persist_message(
        self,
        conversation_id,
        sender_id,
        text,
        msg_type,
        unique_token,
        *,
        conversation_short_id="",
        created_at="",
    ):
        if not self.message_db_path or not self.account_code or text is None:
            return False
        peer_user_id = self._peer_user_id(conversation_id, sender_id)
        media_url = ""
        media_local_path = ""
        media_video_url = ""
        media_video_local_path = ""
        normalized_text = str(text or "").strip()
        if str(msg_type or "").strip() == "image":
            inline_pic = ""
            if isinstance(text, dict):
                inline_pic = str(text.get("inline_pic") or "").strip()
                media_url = extract_douyin_image_url_from_payload(text)
                normalized_text = str(text.get("_display_text") or "").strip() or "[图片]"
            else:
                media_url = extract_douyin_image_url(text)
                normalized_text = str(text or "").strip() or "[图片]"
            if inline_pic:
                media_local_path = cache_inline_image_base64(
                    inline_pic,
                    db_path=self.message_db_path,
                    account_code=self.account_code,
                    preferred_name=str(unique_token or ""),
                )
            if not media_local_path and media_url:
                media_local_path = ensure_douyin_image_cached(
                    media_url,
                    db_path=self.message_db_path,
                    account_code=self.account_code,
                )
            normalized_text = normalize_douyin_image_content(normalized_text, media_url)
        elif str(msg_type or "").strip() == "emoji":
            raw_payload = text if isinstance(text, dict) else {}
            normalized_text = "[表情]"
            if isinstance(raw_payload, dict) and raw_payload:
                from utils.im_media_cache import (
                    ensure_douyin_emoji_cached,
                    extract_douyin_emoji_url_from_payload,
                    normalize_douyin_emoji_content,
                )

                media_url = extract_douyin_emoji_url_from_payload(raw_payload)
            else:
                from utils.im_media_cache import (
                    ensure_douyin_emoji_cached,
                    extract_douyin_emoji_url_from_content,
                    normalize_douyin_emoji_content,
                )

                media_url = extract_douyin_emoji_url_from_content(str(text or ""))
            if media_url:
                media_local_path = ensure_douyin_emoji_cached(
                    media_url,
                    db_path=self.message_db_path,
                    account_code=self.account_code,
                    preferred_name=str(unique_token or ""),
                )
            normalized_text = normalize_douyin_emoji_content(
                str(text or "") if not isinstance(text, dict) else "[表情]",
                media_url,
            )
        elif str(msg_type or "").strip() == "text":
            try:
                from child_mata.chat_item.douyin_emoji_catalog import (
                    resolve_douyin_emoji_local_by_shortcut,
                )

                catalog_path = resolve_douyin_emoji_local_by_shortcut(normalized_text)
                if catalog_path:
                    media_local_path = catalog_path
            except Exception:
                pass
        elif str(msg_type or "").strip() == "video":
            raw_payload = text if isinstance(text, dict) else {}
            if isinstance(raw_payload, dict):
                normalized_text = str(raw_payload.get("_display_text") or "").strip() or "[视频]"
                if normalized_text.startswith("[视频]"):
                    tail = normalized_text[4:].strip()
                    if tail and not tail.startswith(("http://", "https://")):
                        normalized_text = "[视频]"
                from utils.im_media_cache import (
                    extract_douyin_video_cover_url_from_payload,
                    resolve_douyin_video_play_url,
                )

                inline_pic = str(raw_payload.get("inline_pic") or "").strip()
                media_video_url = resolve_douyin_video_play_url(self.auth, raw_payload)
                cover_url = extract_douyin_video_cover_url_from_payload(raw_payload)
                if cover_url:
                    media_url = cover_url
                if inline_pic:
                    media_local_path = cache_inline_image_base64(
                        inline_pic,
                        db_path=self.message_db_path,
                        account_code=self.account_code,
                        preferred_name=str(unique_token or ""),
                    )
                if not media_local_path and media_url:
                    media_local_path = ensure_douyin_image_cached(
                        media_url,
                        db_path=self.message_db_path,
                        account_code=self.account_code,
                    )
            else:
                normalized_text = str(text or "").strip() or "[视频]"
                if normalized_text.startswith("[视频]"):
                    tail = normalized_text[4:].strip()
                    if tail and not tail.startswith(("http://", "https://")):
                        normalized_text = "[视频]"
        if not normalized_text:
            return False
        is_outbound = self._is_self_sender(sender_id, conversation_id)
        if self.ram_first and self.message_event_handler is not None:
            is_ai_reply = False
            if is_outbound and self.reply_engine is not None:
                consume = getattr(self.reply_engine, "consume_ai_outbound_pending", None)
                if callable(consume):
                    is_ai_reply = bool(consume(conversation_id, normalized_text))
            event = {
                "conversation_id": str(conversation_id or ""),
                "direction": "outbound" if is_outbound else "inbound",
                "msg_id": str(unique_token or ""),
                "msg_type": str(msg_type or "text"),
                "content": normalized_text,
                "created_at": str(created_at or ""),
                "peer_user_id": str(peer_user_id or ""),
                "sender_id": str(sender_id or ""),
                "conversation_short_id": str(conversation_short_id or ""),
                "media_url": media_url,
                "media_local_path": media_local_path,
                "media_video_url": media_video_url,
                "media_video_local_path": media_video_local_path,
            }
            if is_ai_reply:
                event["is_ai_reply"] = True
                event["source"] = "reply_engine"
            self.message_event_handler(event)
            return True
        try:
            if conversation_id:
                upsert_conversation_profile(
                    self.message_db_path,
                    self.account_code,
                    conversation_id,
                    display_name="",
                    source="reverse_runtime",
                    conversation_short_id=str(conversation_short_id or "").strip(),
                )
            if self._is_self_sender(sender_id, conversation_id):
                self._remember_im_uid(sender_id)
                save_outbound_message(
                    self.message_db_path,
                    self.account_code,
                    conversation_id,
                    peer_user_id,
                    normalized_text,
                    msg_type=msg_type,
                    unique_token=unique_token,
                    media_url=media_url,
                    media_local_path=media_local_path,
                    media_video_url=media_video_url,
                    media_video_local_path=media_video_local_path,
                    created_at=created_at,
                )
                peer = str(peer_user_id or "").strip()
                if peer.isdigit() and conversation_id and self._peer_profile_needs_refresh(
                    conversation_id,
                    peer,
                ):
                    self._schedule_peer_profile_refresh(peer, conversation_id)
            else:
                save_inbound_message(
                    self.message_db_path,
                    self.account_code,
                    conversation_id,
                    sender_id,
                    normalized_text,
                    msg_type=msg_type,
                    unique_token=unique_token,
                    peer_user_id=peer_user_id,
                    display_name="",
                    media_url=media_url,
                    media_local_path=media_local_path,
                    media_video_url=media_video_url,
                    media_video_local_path=media_video_local_path,
                    created_at=created_at,
                )
                self._schedule_peer_profile_refresh(peer_user_id, conversation_id)
            return True
        except Exception as exc:
            self._log(f"落库私信消息失败: {exc}")
            return False

    def on_error(self, ws, error):
        self.last_error = str(error)
        self.connected_event.clear()
        self._maybe_start_api_fallback_on_disconnect()
        self._log("\033[31m### error ###")
        self._log(error)
        self._log("### ===error=== ###\033[m")

    def on_close(self, ws, close_status_code, close_msg):
        self.connected_event.clear()
        self._maybe_start_api_fallback_on_disconnect()
        close_detail = f"status_code: {close_status_code}, msg: {close_msg}"
        self._last_close_detail = close_detail
        self._log("\033[31m### closed ###")
        self._log(close_detail)
        self._log("### ===closed=== ###\033[m")

    def _disconnect_detail(self):
        detail = (self.last_error or "").strip()
        if detail:
            return detail
        detail = (self._last_close_detail or "").strip()
        if detail:
            return detail
        return "WebSocket runtime stopped unexpectedly"

    def _mark_terminal_failure(self, detail):
        self.last_error = str(detail or "WebSocket runtime stopped unexpectedly")
        self.error_event.set()
        self.closed_event.set()
        self.connected_event.clear()
        if self.status_reporter is not None and not self.stop_requested:
            self.status_reporter.mark_runtime_failure(self.last_error)

    def start(self):
        self.stop_requested = False
        self.closed_event.clear()
        self.error_event.clear()
        self.connected_event.clear()

        while not self.stop_requested:
            self._connected_once_in_cycle = False
            self.last_error = ""
            self._last_close_detail = ""
            self.connected_event.clear()
            self.ws = WebSocketApp(
                url=self.url,
                header={
                    'Pragma': 'no-cache',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                    'User-Agent': HeaderBuilder.ua,
                    'Cache-Control': 'no-cache',
                    'Sec-WebSocket-Protocol': 'binary, base64, pbbp2',
                    'Sec-WebSocket-Extensions': 'permessage-deflate; client_max_window_bits'
                },
                cookie=self.auth.cookie_str,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open
            )
            try:
                self.ws.run_forever(origin='https://www.douyin.com')
            except KeyboardInterrupt:
                self.stop_requested = True
                try:
                    self.ws.close()
                except Exception:
                    pass
                break
            except Exception as exc:
                self.last_error = str(exc)
                self.connected_event.clear()
                self._log(f"WebSocket runner exception: {exc}")

            if self.stop_requested:
                break

            disconnect_detail = self._disconnect_detail()
            self._maybe_start_api_fallback_on_disconnect()

            if not self.auto_reconnect:
                self._mark_terminal_failure(disconnect_detail)
                return

            if not self._connected_once_in_cycle:
                self._consecutive_reconnect_failures += 1
                if (
                    self.max_reconnect_failures > 0
                    and self._consecutive_reconnect_failures >= self.max_reconnect_failures
                ):
                    self._mark_terminal_failure(
                        f"{disconnect_detail}; reconnect failed {self._consecutive_reconnect_failures} times"
                    )
                    return
            else:
                self._consecutive_reconnect_failures = 0

            reconnect_log = f"WebSocket disconnected, will reconnect after {self.reconnect_delay_seconds:.1f}s"
            if self._consecutive_reconnect_failures > 0:
                if self.max_reconnect_failures > 0:
                    reconnect_log += (
                        f" (consecutive failed reconnects: "
                        f"{self._consecutive_reconnect_failures}/{self.max_reconnect_failures})"
                    )
                else:
                    reconnect_log += (
                        f" (consecutive failed reconnects: {self._consecutive_reconnect_failures})"
                    )
            self._log(reconnect_log)
            if self.reconnect_delay_seconds > 0:
                time.sleep(self.reconnect_delay_seconds)

        self.connected_event.clear()
        self.closed_event.set()
        self._stop_api_fallback_thread()


def _default_db_path():
    return Path(__file__).resolve().parent.parent / "_douyin_im_accounts.db"


def _parse_args():
    parser = argparse.ArgumentParser(description="Start Douyin IM auto-reply runtime.")
    parser.add_argument("--db-path", default="", help="SQLite database path. Defaults to project/_douyin_im_accounts.db")
    parser.add_argument("--account-code", default="", help="Start one managed account runtime from SQLite.")
    return parser.parse_args()


def _load_managed_account(db_path, account_code):
    accounts = load_im_accounts_from_db(db_path, account_code=account_code, enabled_only=True)
    if not accounts:
        raise RuntimeError(f"no enabled im account found in SQLite: {account_code}")
    if len(accounts) > 1:
        raise RuntimeError("managed runtime expects exactly one account_code")
    return accounts[0]


def _run_env_runtime():
    load_project_env()
    auth_, auth_source = load_im_auth()
    reply_engine = IMReplyEngine(auth_)
    logger.info("IM auth loaded from: %s", auth_source)
    logger.info("IM reply engine: %s", reply_engine.describe())
    logger.info("Waiting for Douyin IM messages...")
    douyinMsg = DouyinRecvMsg(auth_, reply_engine=reply_engine)
    douyinMsg.start()


def build_managed_reply_engine(auth, account_code, db_path):
    """Create IMReplyEngine with outbound persist callbacks for managed/IPC runtime."""
    db_path = str(db_path)
    account_code = str(account_code)

    def _on_auto_reply_sent(task, reply_text):
        created_at, replied_at = _auto_reply_outbound_times(task)
        unique_token = str(
            getattr(task, "confirmed_server_message_id", "") or ""
        ).strip()
        if not unique_token:
            confirmed_index = int(
                getattr(task, "confirmed_index_in_conversation", 0) or 0
            )
            if confirmed_index > 0:
                unique_token = str(confirmed_index)
        save_outbound_message(
            db_path,
            account_code,
            task.conversation_id,
            task.sender,
            reply_text,
            status="sent",
            unique_token=unique_token,
            allow_content_window_dedupe=False,
            created_at=created_at,
            replied_at=replied_at,
            is_ai_reply=True,
        )

    def _on_auto_reply_failed(task, exc):
        reason = str(exc or "").strip() or "发送失败"
        logger.error(
            "自动回复发送失败: sender=%s conversation_id=%s reason=%s",
            task.sender,
            task.conversation_id,
            reason,
        )
        _SEND_LIMIT_KEYWORDS = (
            "次数", "上限", "已用完", "今日", "临时", "频繁", "频率", "limit", "rate",
            "实名", "认证", "verify", "real_name",
            "隐私", "privacy", "无法回复",
            "多闪", "本地区", "地区", "region",
        )
        _is_limit = any(kw in reason for kw in _SEND_LIMIT_KEYWORDS)
        if _is_limit:
            content_text = f"抖音私信受限，自动回复未发出：{reason}"
        else:
            content_text = f"自动回复发送失败：{reason}"
        created_at, _ = _auto_reply_outbound_times(task)
        save_outbound_message(
            db_path,
            account_code,
            task.conversation_id,
            task.sender,
            content_text,
            status="failed",
            error_msg=content_text,
            allow_content_window_dedupe=False,
            created_at=created_at,
        )

    reply_engine = IMReplyEngine(
        auth,
        account_id=account_code,
        message_db_path=db_path,
    )
    reply_engine.on_reply_sent = _on_auto_reply_sent
    reply_engine.on_reply_failed = _on_auto_reply_failed
    return reply_engine


def _run_managed_account_runtime(db_path, account_code):
    load_project_env()
    account = _load_managed_account(db_path, account_code)
    reporter = RuntimeStatusReporter(db_path, account.account_code)
    ensure_message_tables(db_path)

    with prefixed_account_logs(account.account_code):
        reporter.mark_starting()
        try:
            validate_im_account_credentials(account)
            auth_ = build_im_auth_from_credentials(
                account.cookies_str,
                account.web_protect_str,
                account.keys_str,
            )
            try:
                douyin_uid = str(auth_.get_uid())
            except Exception as exc:
                reporter.mark_need_refresh(f"auth.get_uid failed: {exc}")
                raise
            reporter.set_status(AccountStatus.STARTING, "", douyin_uid=douyin_uid)
            upsert_self_profile(db_path, account.account_code, display_name=douyin_uid, user_id=douyin_uid)
            threading.Thread(
                target=lambda: enrich_self_profile(db_path, account.account_code, auth_),
                name=f"dy-self-profile-{account.account_code}",
                daemon=True,
            ).start()

            reply_engine = build_managed_reply_engine(auth_, account.account_code, db_path)
            logger.info("IM reply engine: %s", reply_engine.describe())

            logger.info("Waiting for Douyin IM messages from SQLite: %s", db_path)
            douyinMsg = DouyinRecvMsg(
                auth_,
                auto_reconnect=True,
                reply_engine=reply_engine,
                account_code=account.account_code,
                status_reporter=reporter,
                max_reconnect_failures=MANAGED_RUNTIME_MAX_RECONNECT_FAILURES,
                reconnect_delay_seconds=MANAGED_RUNTIME_RECONNECT_DELAY_SECONDS,
                message_db_path=str(db_path),
                self_user_id=douyin_uid,
            )
            watcher = RuntimeStatusWatcher(douyinMsg, reporter)
            watcher.start()
            douyinMsg.start()
            watcher.join(timeout=1)
            if douyinMsg.error_event.is_set():
                raise SystemExit(1)
            if not douyinMsg.stop_requested and not douyinMsg.closed_event.is_set():
                reporter.mark_runtime_failure("WebSocket runtime stopped unexpectedly")
                raise SystemExit(1)
        except InvalidIMAccountCredentials as exc:
            reporter.mark_need_refresh(exc)
            logger.error("runtime start failed: %s", exc)
            raise SystemExit(1)
        except Exception as exc:
            if reporter.current_status not in (AccountStatus.NEED_REFRESH, AccountStatus.ERROR):
                reporter.mark_runtime_failure(exc)
            logger.error("runtime start failed: %s", exc)
            raise SystemExit(1)


def main():
    # websocket.enableTrace(True)
    args = _parse_args()
    if args.account_code:
        db_path = Path(args.db_path).expanduser().resolve() if args.db_path else _default_db_path()
        _run_managed_account_runtime(db_path, args.account_code)
    elif args.db_path:
        raise SystemExit("--account-code is required when --db-path is provided")
    else:
        _run_env_runtime()


if __name__ == '__main__':
    main()
