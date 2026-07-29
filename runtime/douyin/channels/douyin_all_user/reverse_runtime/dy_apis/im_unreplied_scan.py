"""抖音私信：近 3 天未回复会话轻量扫描（短窗 5 条，不触发自动回复）。

在 messaging_ready 且实时空闲后由主进程串行队列调用；可取消、可让路。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set

from dy_apis.im_history_backfill import (
    THREE_DAYS_SECONDS,
    _is_placeholder_profile_name,
    _normalize_im_timestamp,
    _now_ts,
    _resolve_conversation_peer_user_id,
    _safe_int,
    _safe_str,
    _ts_to_str,
    fetch_conversation_list_via_api,
    fetch_history_via_api,
    fetch_stranger_messages_via_api,
)
from utils.im_identity import is_im_self_sender, resolve_peer_participant
from utils.im_message_store import (
    save_inbound_message,
    save_outbound_message,
    upsert_conversation_profile,
)
from utils.profile_auto_reply import load_profile_im_uid

logger = logging.getLogger(__name__)

SHORT_WINDOW = 3
LOOKBACK_SECONDS = 3 * 24 * 3600
SHORT_WINDOW_CONCURRENCY = 8
EARLY_STOP_HIT_RATIO = 0.80
CONSECUTIVE_ERROR_LIMIT = 3
REALTIME_IDLE_SECONDS = 3.0
PAUSE_POLL_SECONDS = 0.5


def _pending_reply_count(
    messages: List[Dict[str, Any]],
    *,
    conversation_id: str,
    account_self_uid: str,
    cached_im_uid: str,
    known_self_senders: set[str],
) -> int:
    """与云朵 pending_reply 一致：自末条我方消息后的连续对方条数。"""
    if not messages:
        return 0
    ordered = sorted(
        messages,
        key=lambda m: (
            _normalize_im_timestamp(m.get("create_time", 0)),
            _safe_int(m.get("index_in_conversation"), 0),
        ),
    )
    count = 0
    for msg in reversed(ordered):
        sender = _safe_str(msg.get("sender"))
        is_self = bool(msg.get("is_self"))
        if not is_self and sender:
            is_self = is_im_self_sender(
                sender,
                conversation_id,
                douyin_uid=account_self_uid,
                cached_im_uid=cached_im_uid,
                known_self_senders=frozenset(known_self_senders),
            )
        if is_self:
            break
        content = _safe_str(msg.get("content"))
        if not content:
            continue
        count += 1
    return count


def _newest_create_ts(messages: List[Dict[str, Any]]) -> float:
    newest = 0.0
    for msg in messages:
        ts = float(_normalize_im_timestamp(msg.get("create_time", 0)) or 0)
        if ts > newest:
            newest = ts
    return newest


def _fetch_short_window(
    auth,
    conversation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    conv_id = _safe_str(conversation.get("conversation_id"))
    short_id = _safe_int(conversation.get("conversation_short_id"), 0)
    source = _safe_str(conversation.get("source")).lower()
    if source == "api_stranger":
        messages = fetch_stranger_messages_via_api(auth, short_id)
    else:
        messages, _, _ = fetch_history_via_api(
            auth,
            conv_id,
            short_id,
            conversation_type=1,
            anchor_index=0,
            limit=SHORT_WINDOW,
        )
    if not messages:
        return []
    ordered = sorted(
        messages,
        key=lambda m: (
            _normalize_im_timestamp(m.get("create_time", 0)),
            _safe_int(m.get("index_in_conversation"), 0),
        ),
    )
    return ordered[-SHORT_WINDOW:]


def _persist_short_window(
    db_path: str,
    account_code: str,
    conversation: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    account_self_uid: str,
    cached_im_uid: str,
    known_self_senders: set[str],
    cutoff_ts: float,
) -> int:
    conv_id = _safe_str(conversation.get("conversation_id"))
    peer_user_id = _resolve_conversation_peer_user_id(conversation) or _safe_str(
        conversation.get("peer_user_id")
    )
    display_name = _safe_str(conversation.get("display_name"))
    # 对齐 history_backfill：uid/CID 占位不落库，留空等 enrich，避免首屏污染。
    if _is_placeholder_profile_name(
        display_name,
        conversation_id=conv_id,
        peer_user_id=peer_user_id,
    ):
        display_name = ""
    if peer_user_id or display_name:
        upsert_conversation_profile(
            db_path,
            account_code,
            conv_id,
            display_name,
            source="unreplied_scan",
        )

    saved = 0
    for msg in messages:
        create_time = _normalize_im_timestamp(msg.get("create_time", 0))
        if create_time > 0 and create_time < cutoff_ts:
            continue
        content = _safe_str(msg.get("content"))
        if not content:
            continue
        sender = _safe_str(msg.get("sender"))
        server_message_id = _safe_str(msg.get("server_message_id"))
        index_in_conv = _safe_int(msg.get("index_in_conversation"), 0)
        if server_message_id and server_message_id != "0":
            unique_token = server_message_id
        elif index_in_conv:
            unique_token = str(index_in_conv)
        else:
            unique_token = f"unreplied_{create_time}_{abs(hash(content)) % 10_000_000}"

        is_self = is_im_self_sender(
            sender,
            conv_id,
            douyin_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
            known_self_senders=frozenset(known_self_senders),
        )
        resolved_peer = resolve_peer_participant(
            conv_id,
            sender,
            douyin_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
        ) or peer_user_id
        created_at = _ts_to_str(create_time) if create_time > 0 else ""
        msg_type = _safe_str(msg.get("msg_type")) or "text"

        if is_self:
            msg_id = save_outbound_message(
                db_path,
                account_code,
                conv_id,
                resolved_peer or sender,
                content,
                msg_type=msg_type,
                sender_id=sender or account_self_uid or "我",
                unique_token=unique_token,
                created_at=created_at,
                replied_at=created_at,
                allow_content_window_dedupe=False,
                touch_realtime_activity=False,
            )
        else:
            msg_id = save_inbound_message(
                db_path,
                account_code,
                conv_id,
                sender,
                content,
                msg_type=msg_type,
                unique_token=unique_token,
                peer_user_id=resolved_peer or sender,
                display_name=display_name,
                created_at=created_at,
                known_self_uids=known_self_senders,
                allow_content_window_dedupe=False,
                touch_realtime_activity=False,
            )
        if msg_id:
            saved += 1
    return saved


def _wait_for_realtime_idle(
    db_path: str,
    account_code: str,
    *,
    should_cancel: Callable[[], bool],
    idle_seconds: float = REALTIME_IDLE_SECONDS,
) -> bool:
    """实时有写入时暂停；返回 False 表示被取消。"""
    from utils.im_message_store import seconds_since_runtime_activity

    while True:
        if should_cancel():
            return False
        age = seconds_since_runtime_activity(db_path, account_code)
        if age >= idle_seconds:
            return True
        time.sleep(PAUSE_POLL_SECONDS)


def scan_account_unreplied(
    db_path: str,
    account_code: str,
    auth,
    *,
    explicit_self_uid: str = "",
    should_cancel: Optional[Callable[[], bool]] = None,
    lookback_seconds: int = LOOKBACK_SECONDS,
    concurrency: int = SHORT_WINDOW_CONCURRENCY,
    browser_conv_reader: Optional[Callable[[], Optional[List[str]]]] = None,
    # 兼容旧调用（如 test / 老代码传 three_days_seconds）
    three_days_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """扫描近 3 天未回复会话并短窗落库。不触发自动回复。"""
    cancel = should_cancel or (lambda: False)
    code = _safe_str(account_code)
    path = _safe_str(db_path)
    stats: Dict[str, Any] = {
        "account_code": code,
        "conversations_listed": 0,
        "conversations_checked": 0,
        "unreplied_saved": 0,
        "messages_saved": 0,
        "skipped_replied": 0,
        "skipped_stale": 0,
        "cancelled": False,
        "errors": 0,
    }
    if not code or not path or auth is None:
        stats["errors"] += 1
        return stats

    account_self_uid = _safe_str(explicit_self_uid)
    if not account_self_uid:
        try:
            account_self_uid = _safe_str(auth.get_uid())
        except Exception:
            account_self_uid = ""
    if not account_self_uid:
        try:
            with sqlite3.connect(path) as conn:
                row = conn.execute(
                    "SELECT douyin_uid FROM im_accounts WHERE account_code = ? LIMIT 1",
                    (code,),
                ).fetchone()
                if row and row[0]:
                    account_self_uid = _safe_str(row[0])
        except Exception:
            pass
    cached_im_uid = load_profile_im_uid(code)
    known_self: set[str] = set()
    for value in (account_self_uid, cached_im_uid):
        text = _safe_str(value)
        if text:
            known_self.add(text)

    window_seconds = (
        int(three_days_seconds)
        if three_days_seconds is not None
        else int(lookback_seconds)
    )
    cutoff_ts = _now_ts() - max(1, window_seconds)
    logger.info(
        "[%s] unreplied_scan 开始: cutoff=%s short_window=%s",
        code,
        _ts_to_str(cutoff_ts),
        SHORT_WINDOW,
    )

    # ---- Y: 提前把 browser_reader 派到后台，跟 API 分页并行 ----
    browser_pool: Optional[ThreadPoolExecutor] = None
    browser_future = None
    browser_start_ts = 0.0
    if browser_conv_reader is not None:
        browser_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="unrep-browser")
        browser_start_ts = time.monotonic()
        browser_future = browser_pool.submit(browser_conv_reader)

    # provider：第一次调用阻塞拿 browser 结果（缓存后续快速返回）
    browser_result_cache: Dict[str, Any] = {"resolved": False, "ids": None, "wait_ms": -1}

    def _resolve_browser_ids() -> Optional[Set[str]]:
        if browser_result_cache["resolved"]:
            return browser_result_cache["ids"]
        if browser_future is None:
            browser_result_cache["resolved"] = True
            return None
        _wait_start = time.monotonic()
        try:
            raw = browser_future.result(timeout=45)
        except Exception as _exc:
            logger.warning("[%s] unreplied_scan browser_future 异常: %s", code, _exc)
            raw = None
        browser_result_cache["wait_ms"] = int((time.monotonic() - _wait_start) * 1000.0)
        ids = set(str(cid) for cid in raw if cid) if raw else None
        browser_result_cache["ids"] = ids
        browser_result_cache["resolved"] = True
        return ids

    t_list = time.monotonic()
    try:
        conversations = fetch_conversation_list_via_api(
            auth,
            self_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
            target_conv_ids_provider=_resolve_browser_ids if browser_future is not None else None,
            early_stop_hit_ratio=EARLY_STOP_HIT_RATIO,
        )
    except Exception as exc:
        logger.warning("[%s] unreplied_scan 拉会话列表失败: %s", code, exc)
        stats["errors"] += 1
        if browser_pool is not None:
            try:
                browser_pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                browser_pool.shutdown(wait=False)
        return stats
    list_ms = int((time.monotonic() - t_list) * 1000.0)

    stats["conversations_listed"] = len(conversations)

    # 确保 browser 结果已回收（provider 未被调用时也要拿一次做过滤）
    browser_ids = _resolve_browser_ids()
    browser_reader_ms = -1
    if browser_start_ts > 0:
        browser_reader_ms = int((time.monotonic() - browser_start_ts) * 1000.0)
    if browser_pool is not None:
        try:
            browser_pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            browser_pool.shutdown(wait=False)

    browser_conv_hit = -1
    filtered_from = len(conversations)
    if browser_conv_reader is not None:
        if browser_ids:
            browser_conv_hit = len(browser_ids)
            before = len(conversations)
            conversations = [
                c for c in conversations
                if _safe_str(c.get("conversation_id")) in browser_ids
            ]
            logger.info(
                "[%s] unreplied_scan 浏览器候选过滤: %s -> %s (browser_ids=%s)",
                code, before, len(conversations), len(browser_ids),
            )
        else:
            logger.info("[%s] unreplied_scan 浏览器候选缺失，走 API 全量", code)

    list_normal = 0
    list_stranger = 0
    with_last_time = 0
    with_unread = 0
    for _c in conversations:
        src = _safe_str(_c.get("source"))
        if src == "api_stranger":
            list_stranger += 1
        else:
            list_normal += 1
        if _safe_int(_c.get("last_message_time"), 0) > 0:
            with_last_time += 1
        if _safe_int(_c.get("unread_count"), 0) > 0:
            with_unread += 1

    idle_wait_total_ms = 0
    idle_wait_count = 0
    idle_wait_max_ms = 0
    http_total_ms = 0
    http_count = 0
    http_max_ms = 0
    actual_concurrency = max(1, int(concurrency))
    consecutive_errors = 0
    throttled = False

    # 计算候选（已按浏览器过滤或全量）
    candidates = []
    for conv in conversations:
        cid = _safe_str(conv.get("conversation_id"))
        if cid:
            candidates.append(conv)

    # 每批开始前只等一次 idle
    _t = time.monotonic()
    _idle_ok = _wait_for_realtime_idle(path, code, should_cancel=cancel)
    _elapsed = int((time.monotonic() - _t) * 1000.0)
    idle_wait_total_ms += _elapsed
    idle_wait_count += 1
    if _elapsed > idle_wait_max_ms:
        idle_wait_max_ms = _elapsed
    if not _idle_ok:
        stats["cancelled"] = True
        logger.info("[%s] unreplied_scan 结束: %s", code, stats)
        http_avg_ms = int(http_total_ms / http_count) if http_count else 0
        try:
            logger.info(
                "[perf-unrep] account=%s list_ms=%s listed=%s list_normal=%s list_stranger=%s "
                "with_last_time=%s with_unread=%s",
                code, list_ms, filtered_from, list_normal, list_stranger,
                with_last_time, with_unread,
            )
            logger.info(
                "[perf-unrep] account=%s idle_wait_total_ms=%s idle_wait_count=%s idle_wait_max_ms=%s",
                code, idle_wait_total_ms, idle_wait_count, idle_wait_max_ms,
            )
            logger.info(
                "[perf-unrep] account=%s http_short_window_total_ms=%s http_count=%s "
                "http_avg_ms=%s http_max_ms=%s",
                code, http_total_ms, http_count, http_avg_ms, http_max_ms,
            )
            logger.info(
                "[perf-unrep] account=%s browser_reader_ms=%s browser_wait_ms=%s "
                "browser_conv_hit=%s filtered_from=%s concurrency=%s throttled=%s",
                code, browser_reader_ms, browser_result_cache.get("wait_ms", -1),
                browser_conv_hit, filtered_from,
                actual_concurrency, 1 if throttled else 0,
            )
        except Exception:
            pass
        return stats

    # 短窗并发拉取
    def _probe_one(conversation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """线程内：拉短窗 + 判 pending。返回 dict 或 None（无待回复/失败）。"""
        conv_id = _safe_str(conversation.get("conversation_id"))
        _t = time.monotonic()
        try:
            messages = _fetch_short_window(auth, conversation)
        except Exception as exc:
            return {"conv": conversation, "http_ms": int((time.monotonic() - _t) * 1000), "error": str(exc)}
        http_ms = int((time.monotonic() - _t) * 1000.0)
        if not messages:
            return {"conv": conversation, "http_ms": http_ms, "empty": True}
        newest_ts = _newest_create_ts(messages)
        if newest_ts > 0 and newest_ts < cutoff_ts:
            return {"conv": conversation, "http_ms": http_ms, "stale": True}
        pending = _pending_reply_count(
            messages,
            conversation_id=conv_id,
            account_self_uid=account_self_uid,
            cached_im_uid=cached_im_uid,
            known_self_senders=known_self,
        )
        if pending <= 0:
            return {"conv": conversation, "http_ms": http_ms, "replied": True}
        return {
            "conv": conversation,
            "http_ms": http_ms,
            "messages": messages,
            "pending": pending,
        }

    persist_queue: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=actual_concurrency) as pool:
        futures = {pool.submit(_probe_one, conv): conv for conv in candidates}
        for fut in as_completed(futures):
            if cancel():
                stats["cancelled"] = True
                break
            try:
                res = fut.result()
            except Exception as exc:
                stats["errors"] += 1
                consecutive_errors += 1
                logger.debug("[%s] unreplied_scan probe 异常: %s", code, exc)
                if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT and not throttled:
                    throttled = True
                    logger.warning("[%s] unreplied_scan 连续错误，降级为串行", code)
                continue
            if not res:
                continue
            stats["conversations_checked"] += 1
            http_total_ms += res.get("http_ms", 0)
            http_count += 1
            http_max_ms = max(http_max_ms, res.get("http_ms", 0))
            if res.get("error"):
                stats["errors"] += 1
                consecutive_errors += 1
                if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT and not throttled:
                    throttled = True
                    logger.warning("[%s] unreplied_scan 连续错误，降级为串行", code)
                continue
            consecutive_errors = 0
            if res.get("empty"):
                continue
            if res.get("stale"):
                stats["skipped_stale"] += 1
                continue
            if res.get("replied"):
                stats["skipped_replied"] += 1
                continue
            # 待回复：加入落库队列
            persist_queue.append(res)

    # 串行落库
    for res in persist_queue:
        if cancel():
            stats["cancelled"] = True
            break
        conv = res["conv"]
        conv_id = _safe_str(conv.get("conversation_id"))
        try:
            saved = _persist_short_window(
                path, code, conv, res["messages"],
                account_self_uid=account_self_uid,
                cached_im_uid=cached_im_uid,
                known_self_senders=known_self,
                cutoff_ts=cutoff_ts,
            )
            stats["unreplied_saved"] += 1
            stats["messages_saved"] += saved
            logger.info(
                "[%s] unreplied_scan 写入待回复会话: conv=%s pending=%s saved=%s",
                code, conv_id, res["pending"], saved,
            )
        except Exception as exc:
            logger.warning("[%s] unreplied_scan 落库失败 conv=%s err=%s", code, conv_id, exc)
            stats["errors"] += 1

    logger.info("[%s] unreplied_scan 结束: %s", code, stats)
    http_avg_ms = int(http_total_ms / http_count) if http_count else 0
    try:
        logger.info(
            "[perf-unrep] account=%s list_ms=%s listed=%s list_normal=%s list_stranger=%s "
            "with_last_time=%s with_unread=%s",
            code, list_ms, len(conversations), list_normal, list_stranger,
            with_last_time, with_unread,
        )
        logger.info(
            "[perf-unrep] account=%s idle_wait_total_ms=%s idle_wait_count=%s idle_wait_max_ms=%s",
            code, idle_wait_total_ms, idle_wait_count, idle_wait_max_ms,
        )
        logger.info(
            "[perf-unrep] account=%s http_short_window_total_ms=%s http_count=%s "
            "http_avg_ms=%s http_max_ms=%s",
            code, http_total_ms, http_count, http_avg_ms, http_max_ms,
        )
        logger.info(
            "[perf-unrep] account=%s browser_reader_ms=%s browser_wait_ms=%s "
            "browser_conv_hit=%s filtered_from=%s concurrency=%s throttled=%s",
            code, browser_reader_ms, browser_result_cache.get("wait_ms", -1),
            browser_conv_hit, filtered_from,
            actual_concurrency, 1 if throttled else 0,
        )
    except Exception:
        pass
    return stats
