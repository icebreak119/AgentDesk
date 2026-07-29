import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from queue import Empty, Full, Queue

import requests

from dy_apis.douyin_api import DouyinAPI, DouyinAPIError
from channels.private_chat_coalescer import InboundAiCoalescer
from utils.im_identity import is_im_self_sender
from utils.im_runtime_config import get_default
from utils.im_send_result import summarize_send_response, validate_send_response
from utils.log_util import get_logger
from utils.profile_auto_reply import is_profile_auto_reply_enabled, load_profile_im_uid

get_logger(__name__)
logger = logging.getLogger(__name__)


def _parse_int(name, default_value, min_value=1):
    raw = os.getenv(name, str(default_value)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default_value
    return max(min_value, value)


def _parse_float(name, default_value, min_value=0.1):
    raw = os.getenv(name, str(default_value)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default_value
    return max(min_value, value)


def _parse_csv(value):
    if not value:
        return set()
    return {item.strip() for item in value.replace("\n", ",").split(",") if item.strip()}


def _parse_keyword_rules(raw_value):
    raw_value = raw_value.strip()
    if not raw_value:
        return []

    try:
        parsed = json.loads(raw_value)
        if isinstance(parsed, dict):
            return [(str(key).strip(), str(value).strip()) for key, value in parsed.items() if str(key).strip()]
        if isinstance(parsed, list):
            rules = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                keyword = str(item.get("keyword", "")).strip()
                reply = str(item.get("reply", "")).strip()
                if keyword and reply:
                    rules.append((keyword, reply))
            return rules
    except json.JSONDecodeError:
        pass

    rules = []
    for part in raw_value.split("||"):
        if "=" not in part:
            continue
        keyword, reply = part.split("=", 1)
        keyword = keyword.strip()
        reply = reply.strip()
        if keyword and reply:
            rules.append((keyword, reply))
    return rules


@dataclass
class ReplyTask:
    sender: str
    conversation_id: str
    index: int
    text: str
    conversation_short_id: int = 0
    server_message_id: str = ""
    source_created_at: str = ""


@dataclass
class ReplyConfig:
    mode: str
    fallback_text: str
    max_reply_chars: int
    queue_size: int
    worker_count: int
    seen_cache_size: int
    whitelist: set
    blacklist: set
    keyword_rules: list
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    llm_max_tokens: int
    llm_temperature: float
    llm_system_prompt: str

    def describe(self) -> str:
        """返回脱敏后的配置摘要，用于日志输出。"""
        llm_status = "off"
        if self.llm_base_url and self.llm_api_key:
            # 只显示 URL 的域名部分，隐藏 API key
            try:
                from urllib.parse import urlparse
                parsed = urlparse(self.llm_base_url)
                llm_status = f"{parsed.netloc} (model={self.llm_model})"
            except Exception:
                llm_status = f"configured (model={self.llm_model})"

        return (
            f"mode={self.mode}, "
            f"workers={self.worker_count}, queue={self.queue_size}, "
            f"max_chars={self.max_reply_chars}, "
            f"llm={llm_status}, "
            f"keyword_rules={len(self.keyword_rules)}, "
            f"whitelist={len(self.whitelist)}, blacklist={len(self.blacklist)}"
        )

    @classmethod
    def from_env(cls):
        """从环境变量构建配置。

        注意：此函数只读取环境变量，不主动注入默认值。
        默认值由 managed_controller._base_env() 通过 apply_dy_im_env_defaults() 注入到子进程环境。
        独立运行时（如测试），环境变量缺失则为空（除了 mode 和 llm_model 有硬编码兜底）。
        """
        max_reply_chars = _parse_int("DY_IM_MAX_REPLY_CHARS", int(get_default("DY_IM_MAX_REPLY_CHARS", "80")), 10)

        # 回复文本：优先 DY_IM_FALLBACK_TEXT，其次 DY_IM_AUTO_REPLY_TEXT
        fallback_text = os.getenv("DY_IM_FALLBACK_TEXT", "").strip()
        if not fallback_text:
            fallback_text = os.getenv("DY_IM_AUTO_REPLY_TEXT", "").strip()

        llm_system_prompt = os.getenv(
            "DY_IM_LLM_SYSTEM_PROMPT",
            f"你是抖音私信助手。请直接回复对方最后一条消息，使用简洁自然的中文，不要使用Markdown，不要编造事实，控制在{max_reply_chars}字以内。"
        ).strip()

        # mode 和 llm_model 有硬编码兜底，因为它们有明确的"最佳默认"
        mode = os.getenv("DY_IM_REPLY_MODE", get_default("DY_IM_REPLY_MODE", "ai")).strip().lower() or "ai"
        llm_model = os.getenv("DY_IM_LLM_MODEL", get_default("DY_IM_LLM_MODEL", "qwen-plus")).strip() or "qwen-plus"

        return cls(
            mode=mode,
            fallback_text=fallback_text,
            max_reply_chars=max_reply_chars,
            queue_size=_parse_int("DY_IM_QUEUE_SIZE", int(get_default("DY_IM_QUEUE_SIZE", "200")), 10),
            worker_count=_parse_int("DY_IM_WORKER_COUNT", int(get_default("DY_IM_WORKER_COUNT", "2")), 1),
            seen_cache_size=_parse_int("DY_IM_SEEN_CACHE_SIZE", int(get_default("DY_IM_SEEN_CACHE_SIZE", "2000")), 100),
            whitelist=_parse_csv(os.getenv("DY_IM_WHITELIST", "")),
            blacklist=_parse_csv(os.getenv("DY_IM_BLACKLIST", "")),
            keyword_rules=_parse_keyword_rules(os.getenv("DY_IM_KEYWORD_RULES", "")),
            llm_base_url=os.getenv("DY_IM_LLM_BASE_URL", "").strip().rstrip("/"),
            llm_api_key=os.getenv("DY_IM_LLM_API_KEY", "").strip(),
            llm_model=llm_model,
            llm_timeout=_parse_float("DY_IM_LLM_TIMEOUT", float(get_default("DY_IM_LLM_TIMEOUT", "8")), 1),
            llm_max_tokens=_parse_int("DY_IM_LLM_MAX_TOKENS", int(get_default("DY_IM_LLM_MAX_TOKENS", "128")), 16),
            llm_temperature=_parse_float("DY_IM_LLM_TEMPERATURE", float(get_default("DY_IM_LLM_TEMPERATURE", "0.4")), 0.0),
            llm_system_prompt=llm_system_prompt,
        )


class BoundedSeenCache:
    def __init__(self, max_size):
        self.max_size = max_size
        self.items = deque()
        self.lookup = set()
        self.lock = threading.Lock()

    def add(self, key):
        with self.lock:
            if key in self.lookup:
                return False
            if len(self.items) >= self.max_size:
                oldest = self.items.popleft()
                self.lookup.discard(oldest)
            self.items.append(key)
            self.lookup.add(key)
            return True


class OpenAICompatibleLLMClient:
    def __init__(self, base_url, api_key, model, timeout, max_tokens, temperature, system_prompt):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.system_prompt = system_prompt

    def generate_reply(self, user_text):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"收到的用户私信：{user_text}\n请直接输出回复内容。"},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        last_exc = None
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code in (429, 503):
                    retry_after = int(response.headers.get("Retry-After", 2 * (attempt + 1)))
                    time.sleep(min(retry_after, 10))
                    continue
                response.raise_for_status()
                break
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        else:
            if last_exc:
                raise last_exc
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected llm response: {data}") from exc

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
            return "".join(parts)
        return str(content)


class PreviewServiceLLMClient:
    def __init__(self, timeout, account_id="", message_db_path=""):
        self.timeout = timeout
        self.account_id = str(account_id or os.getenv("DY_IM_ACCOUNT_ID", "")).strip()
        self.message_db_path = str(message_db_path or os.getenv("DY_IM_MESSAGE_DB_PATH", "")).strip()

    def generate_reply(self, user_text, *, conversation_id="", customer_name="", customer_id=""):
        account_id = self.account_id or os.getenv("DY_IM_ACCOUNT_ID", "").strip()
        if not account_id:
            raise RuntimeError("DY_IM_ACCOUNT_ID is not configured")

        conv_id = str(conversation_id or "").strip()
        if conv_id:
            try:
                from channels.douyin_all_user.reverse_runtime.utils.aggregate_dismiss_check import (
                    is_douyin_conversation_dismissed,
                )

                if is_douyin_conversation_dismissed(account_id, conv_id):
                    logger.info(
                        "预览服 AI 跳过（会话已结束）: accountId=%s conversation_id=%s",
                        account_id,
                        conv_id,
                    )
                    return ""
            except Exception:
                pass
            try:
                from channels.douyin_all_user.reverse_runtime.utils.aggregate_block_check import (
                    is_douyin_conversation_blocked,
                )

                if is_douyin_conversation_blocked(account_id, conv_id):
                    logger.info(
                        "预览服 AI 跳过（会话已拉黑）: accountId=%s conversation_id=%s",
                        account_id,
                        conv_id,
                    )
                    return ""
            except Exception:
                pass

        try:
            from channels.douyin_all_user import session_store

            if not session_store.is_profile_auto_reply_enabled(account_id):
                logger.info(
                    "预览服 AI 跳过（AI 开关已关）: accountId=%s conversation_id=%s",
                    account_id,
                    conversation_id or "-",
                )
                return ""
        except Exception:
            if not is_profile_auto_reply_enabled(account_id):
                logger.info(
                    "预览服 AI 跳过（AI 开关已关）: accountId=%s conversation_id=%s",
                    account_id,
                    conversation_id or "-",
                )
                return ""

        content = str(user_text or "").strip()
        history = None
        conv_id = str(conversation_id or "").strip()
        db_path = self.message_db_path or os.getenv("DY_IM_MESSAGE_DB_PATH", "").strip()
        resolved_customer_name = str(customer_name or "").strip()
        resolved_customer_id = str(customer_id or "").strip()
        if db_path and conv_id:
            try:
                from channels.private_chat_common import split_private_chat_turn
                from utils.im_message_store import (
                    load_peer_display_name,
                    load_private_ai_message_list,
                )

                if not resolved_customer_name:
                    resolved_customer_name = load_peer_display_name(db_path, account_id, conv_id)
                if not resolved_customer_id:
                    try:
                        from channels.douyin_all_user.douyin_message_store import (
                            resolve_peer_user_id,
                        )

                        self_uid = ""
                        try:
                            from channels.douyin_all_user import session_store

                            meta = session_store.load_profile_meta(account_id) or {}
                            self_uid = str(
                                meta.get("douyin_uid")
                                or meta.get("user_id")
                                or meta.get("im_uid")
                                or ""
                            ).strip()
                        except Exception:
                            pass
                        peer_uid = resolve_peer_user_id(conv_id, "", self_uid)
                        if peer_uid:
                            resolved_customer_id = peer_uid
                    except Exception:
                        pass
                if not resolved_customer_id:
                    resolved_customer_id = conv_id

                message_list = load_private_ai_message_list(
                    db_path,
                    account_id,
                    conv_id,
                    max_messages=int(os.getenv("DY_IM_AI_HISTORY_LIMIT", "10")),
                )
                if message_list:
                    history, split_content = split_private_chat_turn(message_list)
                    if split_content:
                        content = split_content
            except Exception as exc:
                logger.warning(
                    "加载私域 AI 历史失败 conversation_id=%s: %s",
                    conv_id,
                    exc,
                )

        from channels.douyin_all_user.preview_account_id import resolve_preview_account_id

        preview_account_id = resolve_preview_account_id(account_id, db_path=db_path)
        logger.info(
            "调用预览服 /chat/completion/private accountId=%s profile=%s conversation_id=%s customerId=%s customerName=%s content_len=%s history=%s",
            preview_account_id,
            account_id,
            conv_id or "-",
            resolved_customer_id or "-",
            resolved_customer_name or "-",
            len(content),
            len(history or []),
        )

        from channels.private_chat_http import request_private_chat_sync

        system_prompt = str(os.getenv("DY_IM_LLM_SYSTEM_PROMPT") or "").strip()
        if not system_prompt:
            try:
                from utils.yunduo_knowledge_prompt import build_dy_im_system_prompt

                system_prompt = build_dy_im_system_prompt()
            except Exception as exc:
                logger.warning("加载知识库 system prompt 失败: %s", exc)

        logger.info(
            "调用预览服 system_prompt_len=%s profile_id=%s",
            len(system_prompt or ""),
            account_id or os.getenv("DY_IM_ACCOUNT_ID", ""),
        )

        reply, error = request_private_chat_sync(
            account_id=preview_account_id,
            content=content,
            message_list=history,
            customer_name=resolved_customer_name,
            customer_id=resolved_customer_id,
            timeout_sec=self.timeout,
            system_prompt=system_prompt,
            profile_id=account_id,
            channel="douyin",
        )
        if error:
            raise RuntimeError(error)
        return reply


class IMReplyEngine:
    def __init__(
        self,
        auth,
        config=None,
        llm_client=None,
        on_reply_sent=None,
        on_reply_failed=None,
        *,
        account_id="",
        message_db_path="",
    ):
        self.auth = auth
        self.config = config or ReplyConfig.from_env()
        self.account_id = str(
            account_id or os.getenv("DY_IM_ACCOUNT_ID", "")
        ).strip()
        self.message_db_path = str(
            message_db_path or os.getenv("DY_IM_MESSAGE_DB_PATH", "")
        ).strip()
        self.llm_client = llm_client or self._build_llm_client()
        self.on_reply_sent = on_reply_sent
        self.on_reply_failed = on_reply_failed
        self.queue = Queue(maxsize=self.config.queue_size)
        self.seen_cache = BoundedSeenCache(self.config.seen_cache_size)
        self.conversation_locks = {}
        self.conversation_locks_guard = threading.Lock()
        self.conversation_cache = {}
        self.conversation_cache_lock = threading.Lock()
        self.pending_echo_lock = threading.Lock()
        self.pending_echo = {}
        self._ai_outbound_pending_lock = threading.Lock()
        self._ai_outbound_pending: dict[tuple[str, str], float] = {}
        self.inbound_coalescer = InboundAiCoalescer(name="douyin-reverse-ai")
        self.uid_lock = threading.Lock()
        self.my_uid = None
        self.workers = []
        self.stop_event = threading.Event()
        self._cached_im_uid = ""
        self._known_self_im_uids: set[str] = set()
        self.enabled = self._is_enabled()
        if self.enabled:
            self._start_workers()
            logger.info("IMReplyEngine 已启用: mode=%s, workers=%d, queue=%d", self.config.mode, self.config.worker_count, self.config.queue_size)
        else:
            logger.info("IMReplyEngine 已禁用: mode=%s, fallback='%s', llm=%s", self.config.mode, self.config.fallback_text, "on" if self.llm_client else "off")

    def _is_enabled(self):
        if self.config.mode == "off":
            return False
        if self.config.keyword_rules or self.config.fallback_text:
            return True
        return self.llm_client is not None

    def _build_llm_client(self):
        if self.config.mode != "ai":
            return None
        preview_setting = os.getenv("DY_IM_USE_PREVIEW_SERVICE", "").strip().lower()
        helper = os.getenv("DY_IM_PREVIEW_HELPER", "").strip()
        client_root = os.getenv("YUNDUO_CLIENT_ROOT", "").strip()
        preview_ready = preview_setting in {"1", "true", "yes", "on"} or (
            client_root and helper and os.path.isfile(helper)
        )
        if preview_ready:
            logger.info(
                "自动回复 LLM 后端=preview(/chat/completion/private) accountId=%s",
                self.account_id or os.getenv("DY_IM_ACCOUNT_ID", ""),
            )
            return PreviewServiceLLMClient(
                self.config.llm_timeout,
                account_id=self.account_id,
                message_db_path=self.message_db_path,
            )
        if not self.config.llm_base_url or not self.config.llm_api_key:
            logger.warning(
                "自动回复 LLM 未配置: preview 未启用且缺少 DY_IM_LLM_BASE_URL/API_KEY"
            )
            return None
        logger.info(
            "自动回复 LLM 后端=dashscope(%s) model=%s",
            self.config.llm_base_url,
            self.config.llm_model,
        )
        return OpenAICompatibleLLMClient(
            self.config.llm_base_url,
            self.config.llm_api_key,
            self.config.llm_model,
            self.config.llm_timeout,
            self.config.llm_max_tokens,
            self.config.llm_temperature,
            self.config.llm_system_prompt,
        )

    def _resolve_cached_im_uid(self) -> str:
        if self._cached_im_uid:
            return self._cached_im_uid
        account_id = self.account_id or os.getenv("DY_IM_ACCOUNT_ID", "").strip()
        if account_id:
            self._cached_im_uid = load_profile_im_uid(account_id)
            if self._cached_im_uid:
                self._known_self_im_uids.add(self._cached_im_uid)
        return self._cached_im_uid

    def _load_known_self_im_uids_from_db(self) -> frozenset[str]:
        if self._known_self_im_uids:
            return frozenset(self._known_self_im_uids)
        account_id = self.account_id or os.getenv("DY_IM_ACCOUNT_ID", "").strip()
        db_path = self.message_db_path or os.getenv("DY_IM_MESSAGE_DB_PATH", "").strip()
        self._resolve_cached_im_uid()
        if account_id and db_path:
            try:
                import sqlite3

                with sqlite3.connect(db_path) as conn:
                    rows = conn.execute(
                        """
                        SELECT DISTINCT from_user_id
                        FROM messages
                        WHERE account_profile_id = ?
                          AND direction = 'outbound'
                          AND IFNULL(from_user_id, '') != ''
                        """,
                        (account_id,),
                    ).fetchall()
                for row in rows:
                    uid = str(row[0] or "").strip()
                    if uid:
                        self._known_self_im_uids.add(uid)
            except Exception:
                pass
        return frozenset(self._known_self_im_uids)

    def describe(self):
        llm_status = "on" if self.llm_client else "off"
        return f"enabled={self.enabled}, mode={self.config.mode}, llm={llm_status}, workers={self.config.worker_count}, queue={self.config.queue_size}"

    def _is_auto_reply_enabled(self) -> bool:
        """读取侧栏 ``cb_ai`` 持久化的 ``profile_meta.auto_reply``。"""
        account_id = self.account_id or os.getenv("DY_IM_ACCOUNT_ID", "").strip()
        if not account_id:
            return True
        return is_profile_auto_reply_enabled(account_id)

    def _is_conversation_dismissed(self, conversation_id: str) -> bool:
        account_id = self.account_id or os.getenv("DY_IM_ACCOUNT_ID", "").strip()
        conv_id = str(conversation_id or "").strip()
        if not account_id or not conv_id:
            return False
        try:
            from channels.douyin_all_user.reverse_runtime.utils.aggregate_dismiss_check import (
                is_douyin_conversation_dismissed,
            )

            return is_douyin_conversation_dismissed(account_id, conv_id)
        except Exception:
            return False

    def _is_conversation_blocked(self, conversation_id: str) -> bool:
        account_id = self.account_id or os.getenv("DY_IM_ACCOUNT_ID", "").strip()
        conv_id = str(conversation_id or "").strip()
        if not account_id or not conv_id:
            return False
        try:
            from channels.douyin_all_user.reverse_runtime.utils.aggregate_block_check import (
                is_douyin_conversation_blocked,
            )

            return is_douyin_conversation_blocked(account_id, conv_id)
        except Exception:
            return False

    def _start_workers(self):
        for index in range(self.config.worker_count):
            worker = threading.Thread(target=self._worker_loop, name=f"dy-im-reply-{index}", daemon=True)
            worker.start()
            self.workers.append(worker)

    def _get_my_uid(self):
        with self.uid_lock:
            if self.my_uid is None:
                self.my_uid = str(self.auth.get_uid())
            return self.my_uid

    def _get_conversation_lock(self, conversation_id):
        with self.conversation_locks_guard:
            if conversation_id not in self.conversation_locks:
                self.conversation_locks[conversation_id] = threading.Lock()
            return self.conversation_locks[conversation_id]

    def enqueue_text_message(
        self,
        sender,
        conversation_id,
        index,
        text,
        *,
        conversation_short_id=0,
        server_message_id="",
        source_created_at="",
    ):
        if not self.enabled:
            logger.info(
                "自动回复未入队: reply_engine disabled sender=%s conversation_id=%s index=%s",
                sender,
                conversation_id,
                index,
            )
            return

        sender = str(sender)
        conversation_id = str(conversation_id)
        text = (text or "").strip()
        if not text:
            logger.info(
                "自动回复未入队: 空文本 sender=%s conversation_id=%s index=%s",
                sender,
                conversation_id,
                index,
            )
            return

        if is_im_self_sender(
            sender,
            conversation_id,
            douyin_uid=self._get_my_uid(),
            cached_im_uid=self._resolve_cached_im_uid(),
            known_self_senders=self._load_known_self_im_uids_from_db(),
        ):
            logger.info(
                "自动回复未入队: 自己发出的消息 sender=%s conversation_id=%s index=%s",
                sender,
                conversation_id,
                index,
            )
            return
        if sender in self.config.blacklist:
            logger.info("自动回复未入队: 黑名单用户 sender=%s", sender)
            return
        if self._is_conversation_dismissed(conversation_id):
            logger.info(
                "自动回复未入队: 会话已结束 sender=%s conversation_id=%s index=%s",
                sender,
                conversation_id,
                index,
            )
            return
        if self._is_conversation_blocked(conversation_id):
            logger.info(
                "自动回复未入队: 会话已拉黑 sender=%s conversation_id=%s index=%s",
                sender,
                conversation_id,
                index,
            )
            return
        if self.config.whitelist and sender not in self.config.whitelist:
            logger.info("自动回复未入队: 非白名单用户 sender=%s", sender)
            return

        msg_key = f"{conversation_id}:{index}"
        if not self.seen_cache.add(msg_key):
            logger.info("自动回复未入队: 重复消息 msg_key=%s", msg_key)
            return

        key = ("douyin", self.account_id or "-", conversation_id, "private")

        def _flush_ai_batch(batch):
            last = batch.last_metadata
            self._enqueue_reply_task(
                sender=str(last.get("sender") or sender),
                conversation_id=conversation_id,
                index=last.get("index", index),
                text=batch.merged_text,
                conversation_short_id=last.get(
                    "conversation_short_id",
                    conversation_short_id,
                ),
                server_message_id=batch.last_msg_id or str(server_message_id or ""),
                source_created_at=str(
                    last.get("source_created_at") or source_created_at or ""
                ).strip(),
                msg_key=f"{conversation_id}:{batch.last_msg_id or index}",
                batch_size=len(batch.messages),
            )

        self.inbound_coalescer.ingest(
            key,
            text=text,
            msg_id=str(server_message_id or index or ""),
            metadata={
                "sender": sender,
                "index": index,
                "conversation_short_id": conversation_short_id,
                "source_created_at": str(source_created_at or "").strip(),
            },
            on_flush=_flush_ai_batch,
        )
        logger.info(
            "自动回复合并窗口已调度: sender=%s conversation_id=%s index=%s short_id=%s text_len=%s",
            sender,
            conversation_id,
            index,
            conversation_short_id or "-",
            len(text),
        )

    def _enqueue_reply_task(
        self,
        *,
        sender,
        conversation_id,
        index,
        text,
        conversation_short_id=0,
        server_message_id="",
        source_created_at="",
        msg_key="",
        batch_size=1,
    ):
        try:
            self.queue.put_nowait(
                ReplyTask(
                    sender=str(sender),
                    conversation_id=str(conversation_id),
                    index=int(index or 0),
                    text=str(text or "").strip(),
                    conversation_short_id=int(conversation_short_id or 0),
                    server_message_id=str(server_message_id or ""),
                    source_created_at=str(source_created_at or "").strip(),
                )
            )
            logger.info(
                "自动回复消息已入队: sender=%s conversation_id=%s index=%s short_id=%s queue_size=%s text_len=%s batch_size=%s",
                sender,
                conversation_id,
                index,
                conversation_short_id or "-",
                self.queue.qsize(),
                len(str(text or "")),
                batch_size,
            )
        except Full:
            logger.warning("回复队列已满，丢弃消息: %s", msg_key or conversation_id)

    def notify_outbound_echo(
        self,
        *,
        conversation_id,
        sender,
        text,
        index="",
        server_message_id="",
        conversation_short_id=0,
    ):
        key = self._pending_echo_key(conversation_id, text)
        with self.pending_echo_lock:
            pending_list = self.pending_echo.get(key) or []
            pending = pending_list.pop(0) if pending_list else None
            if pending_list:
                self.pending_echo[key] = pending_list
            else:
                self.pending_echo.pop(key, None)
        if not pending:
            logger.info(
                "自动回复收到自身回显但未匹配待确认任务: sender=%s conversation_id=%s short_id=%s index=%s server_message_id=%s text_preview=%s",
                sender,
                conversation_id,
                conversation_short_id or "-",
                index or "-",
                server_message_id or "-",
                str(text or "")[:50],
            )
            return False
        pending["echo"] = {
            "sender": str(sender or ""),
            "conversation_id": str(conversation_id or ""),
            "conversation_short_id": int(conversation_short_id or 0),
            "index": str(index or ""),
            "server_message_id": str(server_message_id or ""),
        }
        pending["event"].set()
        logger.info(
            "自动回复发送回显已确认: sender=%s conversation_id=%s short_id=%s index=%s server_message_id=%s",
            sender,
            conversation_id,
            conversation_short_id or "-",
            index or "-",
            server_message_id or "-",
        )
        return True

    def mark_ai_outbound_pending(self, conversation_id: str, text: str) -> None:
        conv = str(conversation_id or "").strip()
        body = str(text or "").strip()
        if not conv or not body:
            return
        with self._ai_outbound_pending_lock:
            self._ai_outbound_pending[(conv, body)] = time.monotonic()

    def consume_ai_outbound_pending(self, conversation_id: str, text: str) -> bool:
        conv = str(conversation_id or "").strip()
        body = str(text or "").strip()
        if not conv or not body:
            return False
        key = (conv, body)
        with self._ai_outbound_pending_lock:
            ts = self._ai_outbound_pending.pop(key, None)
        if ts is None:
            return False
        return (time.monotonic() - float(ts)) <= 120.0

    def _worker_loop(self):
        while not self.stop_event.is_set():
            try:
                task = self.queue.get(timeout=1)
            except Empty:
                continue

            try:
                with self._get_conversation_lock(task.conversation_id):
                    logger.info(
                        "开始处理自动回复任务: sender=%s conversation_id=%s index=%s",
                        task.sender,
                        task.conversation_id,
                        task.index,
                    )
                    self._handle_task(task)
            except Exception as exc:
                if callable(self.on_reply_failed):
                    try:
                        self.on_reply_failed(task, exc)
                    except Exception:
                        pass
                logger.exception("处理回复任务失败，用户: %s，原因: %s", task.sender, exc)
            finally:
                self.queue.task_done()

    def _handle_task(self, task):
        if self._is_conversation_dismissed(task.conversation_id):
            logger.info(
                "自动回复跳过（会话已结束）: sender=%s conversation_id=%s index=%s",
                task.sender,
                task.conversation_id,
                task.index,
            )
            return
        if self._is_conversation_blocked(task.conversation_id):
            logger.info(
                "自动回复跳过（会话已拉黑）: sender=%s conversation_id=%s index=%s",
                task.sender,
                task.conversation_id,
                task.index,
            )
            return
        if not self._is_auto_reply_enabled():
            logger.info(
                "自动回复跳过（AI 开关已关）: sender=%s conversation_id=%s index=%s",
                task.sender,
                task.conversation_id,
                task.index,
            )
            return

        reply_text = self._select_reply(task)
        if not reply_text:
            logger.info("未生成回复，用户: %s", task.sender)
            return

        if not self._is_auto_reply_enabled():
            logger.info(
                "自动回复跳过发送（AI 开关已关）: sender=%s conversation_id=%s index=%s",
                task.sender,
                task.conversation_id,
                task.index,
            )
            return

        conversation_id, conversation_short_id, ticket = self._get_conversation_meta(task)
        logger.info(
            "自动回复准备发送: sender=%s conversation_id=%s short_id=%s reply_len=%s reply_preview=%s",
            task.sender,
            conversation_id,
            conversation_short_id,
            len(reply_text),
            reply_text[:50],
        )
        self.mark_ai_outbound_pending(conversation_id, reply_text)
        try:
            result = self._send_confirmed_with_soft_retry(
                task,
                conversation_id,
                conversation_short_id,
                ticket,
                reply_text,
            )
        except DouyinAPIError as exc:
            raise RuntimeError(str(exc.message or exc).strip() or "发送失败") from exc

        conversation_id = str(result.get("conversation_id") or conversation_id)
        conversation_short_id = result.get("conversation_short_id", conversation_short_id)
        confirmed = result.get("confirmed_message") if isinstance(result, dict) else None
        if isinstance(confirmed, dict):
            try:
                task.confirmed_create_time = int(confirmed.get("create_time") or 0)
            except (TypeError, ValueError):
                task.confirmed_create_time = 0
            task.confirmed_server_message_id = str(
                confirmed.get("server_message_id") or ""
            ).strip()
            try:
                task.confirmed_index_in_conversation = int(
                    confirmed.get("index_in_conversation") or 0
                )
            except (TypeError, ValueError):
                task.confirmed_index_in_conversation = 0

        send_response = result.get("response") if isinstance(result, dict) else result
        echo_meta = {}
        if isinstance(confirmed, dict):
            echo_meta = {
                "server_message_id": getattr(task, "confirmed_server_message_id", ""),
                "index": getattr(task, "confirmed_index_in_conversation", 0),
            }
        meta = {
            "status": "sent",
            "conversation_id": conversation_id,
            "conversation_short_id": conversation_short_id,
            "send_response": send_response,
            "echo": echo_meta,
            "soft_retry": int(result.get("soft_retry") or 0),
        }
        self._notify_reply_sent(task, reply_text, meta)
        logger.info(
            "已自动回复用户（历史确认）: %s server_message_id=%s soft_retry=%s",
            task.sender,
            echo_meta.get("server_message_id") or "-",
            int(result.get("soft_retry") or 0),
        )

    def _invalidate_conversation_cache(self, sender) -> None:
        key = str(sender or "").strip()
        if not key:
            return
        with self.conversation_cache_lock:
            self.conversation_cache.pop(key, None)

    def _get_conversation_meta(self, task, *, force_refresh: bool = False):
        with self.conversation_cache_lock:
            cached = self.conversation_cache.get(task.sender)
        if (
            (not force_refresh)
            and cached
            and cached["conversation_id"] == task.conversation_id
        ):
            return cached["conversation_id"], cached["conversation_short_id"], cached["ticket"]

        # 启动后首发/刷新：优先复用会话列表里的 ticket，避免 create 拿到未热会话态
        try:
            conversation_id, conversation_short_id, ticket = (
                DouyinAPI.resolve_or_create_conversation(
                    self.auth,
                    int(task.sender),
                    conversation_id=str(task.conversation_id or "").strip(),
                )
            )
        except Exception as exc:
            logger.warning(
                "resolve_or_create_conversation 失败，回退 create: sender=%s err=%s",
                task.sender,
                exc,
            )
            conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
                self.auth, int(task.sender)
            )
        record = {
            "conversation_id": conversation_id,
            "conversation_short_id": int(conversation_short_id),
            "ticket": ticket,
        }
        with self.conversation_cache_lock:
            self.conversation_cache[task.sender] = record
        return record["conversation_id"], record["conversation_short_id"], record["ticket"]

    def _send_confirmed_with_soft_retry(
        self,
        task,
        conversation_id,
        conversation_short_id,
        ticket,
        reply_text,
    ):
        """发送并历史确认；软成功时刷新 ticket 后最多再发 1 次（防双发）。"""
        try:
            result = DouyinAPI.send_msg_confirmed(
                self.auth,
                conversation_id,
                conversation_short_id,
                ticket,
                reply_text,
            )
            if isinstance(result, dict):
                result["conversation_id"] = conversation_id
                result["conversation_short_id"] = conversation_short_id
                result["soft_retry"] = 0
            return result
        except DouyinAPIError as exc:
            if getattr(exc, "code", None) != "send_unconfirmed":
                raise
            first_exc = exc

        # 可能只是确认慢：先查历史，已存在则不当失败、也不重发
        late = DouyinAPI.find_recent_outbound_by_text(
            self.auth,
            conversation_id,
            conversation_short_id,
            reply_text,
        )
        if isinstance(late, dict):
            logger.info(
                "自动回复软成功补确认命中（未重发）: sender=%s conversation_id=%s",
                task.sender,
                conversation_id,
            )
            return {
                "response": getattr(first_exc, "raw", None),
                "confirmed_message": late,
                "conversation_id": conversation_id,
                "conversation_short_id": conversation_short_id,
                "soft_retry": 0,
            }

        logger.warning(
            "自动回复疑似软成功，刷新 ticket 后重试一次: sender=%s conversation_id=%s short_id=%s",
            task.sender,
            conversation_id,
            conversation_short_id,
        )
        self._invalidate_conversation_cache(task.sender)
        conversation_id, conversation_short_id, ticket = self._get_conversation_meta(
            task, force_refresh=True
        )

        # 刷新后再查一次，避免 resolve 期间消息晚到导致双发
        late = DouyinAPI.find_recent_outbound_by_text(
            self.auth,
            conversation_id,
            conversation_short_id,
            reply_text,
        )
        if isinstance(late, dict):
            logger.info(
                "自动回复刷新 ticket 后发现已落库（未重发）: sender=%s conversation_id=%s",
                task.sender,
                conversation_id,
            )
            return {
                "response": getattr(first_exc, "raw", None),
                "confirmed_message": late,
                "conversation_id": conversation_id,
                "conversation_short_id": conversation_short_id,
                "soft_retry": 0,
            }

        result = DouyinAPI.send_msg_confirmed(
            self.auth,
            conversation_id,
            conversation_short_id,
            ticket,
            reply_text,
            confirm_timeout=_parse_float("DY_IM_SEND_SOFT_RETRY_CONFIRM_TIMEOUT", 12.0, 3.0),
        )
        if isinstance(result, dict):
            result["conversation_id"] = conversation_id
            result["conversation_short_id"] = conversation_short_id
            result["soft_retry"] = 1
            logger.info(
                "自动回复软成功重发已确认: sender=%s conversation_id=%s",
                task.sender,
                conversation_id,
            )
        return result

    def _send_msg_with_retry(
        self,
        conversation_id,
        conversation_short_id,
        ticket,
        reply_text,
        *,
        task,
    ):
        attempts = _parse_int("DY_IM_SEND_RETRY_ATTEMPTS", 3, 1)
        delay = _parse_float("DY_IM_SEND_RETRY_DELAY", 1.5, 0.0)
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                return DouyinAPI.send_msg(
                    self.auth,
                    conversation_id,
                    conversation_short_id,
                    ticket,
                    reply_text,
                    timeout=_parse_float("DY_IM_SEND_TIMEOUT", 12.0, 1.0),
                )
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                logger.warning(
                    "自动回复发送网络异常，准备重试: sender=%s conversation_id=%s short_id=%s attempt=%s/%s err=%s",
                    task.sender,
                    conversation_id,
                    conversation_short_id,
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(delay * attempt)
        raise last_exc

    @staticmethod
    def _pending_echo_key(conversation_id, text):
        return (str(conversation_id or "").strip(), str(text or "").strip())

    def _register_pending_echo(self, conversation_id, reply_text):
        pending = {
            "event": threading.Event(),
            "conversation_id": str(conversation_id or "").strip(),
            "reply_text": str(reply_text or "").strip(),
            "echo": None,
        }
        key = self._pending_echo_key(conversation_id, reply_text)
        with self.pending_echo_lock:
            self.pending_echo.setdefault(key, []).append(pending)
        return pending

    def _discard_pending_echo(self, pending):
        if not pending:
            return
        key = self._pending_echo_key(pending.get("conversation_id"), pending.get("reply_text"))
        with self.pending_echo_lock:
            pending_list = self.pending_echo.get(key) or []
            pending_list = [item for item in pending_list if item is not pending]
            if pending_list:
                self.pending_echo[key] = pending_list
            else:
                self.pending_echo.pop(key, None)

    def _wait_for_echo(self, pending, task, reply_text):
        timeout = _parse_float("DY_IM_SEND_ECHO_TIMEOUT", 8.0, 0.0)
        if timeout <= 0:
            self._discard_pending_echo(pending)
            return "unknown", None
        if pending["event"].wait(timeout):
            return "sent", pending.get("echo")
        self._discard_pending_echo(pending)
        logger.warning(
            "自动回复发送接口已返回但未等到自身回显: sender=%s conversation_id=%s index=%s timeout=%.1fs reply_preview=%s",
            task.sender,
            task.conversation_id,
            task.index,
            timeout,
            reply_text[:50],
        )
        return "pending", None

    def _notify_reply_sent(self, task, reply_text, meta):
        if not callable(self.on_reply_sent):
            return
        try:
            self.on_reply_sent(task, reply_text, meta)
        except TypeError:
            self.on_reply_sent(task, reply_text)
        except Exception:
            pass

    def _select_reply(self, task):
        keyword_reply = self._match_keyword_rule(task.text)
        if keyword_reply:
            logger.info("自动回复命中关键词规则，用户: %s", task.sender)
            return keyword_reply

        if self.config.mode == "fixed":
            logger.info("自动回复使用固定回复模式，用户: %s", task.sender)
            return self._normalize_reply(self.config.fallback_text)

        if self.llm_client is not None:
            if not self._is_auto_reply_enabled():
                logger.info(
                    "预览服 AI 跳过（AI 开关已关）: user=%s conversation_id=%s",
                    task.sender,
                    task.conversation_id,
                )
            else:
                try:
                    logger.info(
                        "开始调用预览服 /chat/completion/private: user=%s mode=%s text_len=%s",
                        task.sender,
                        self.config.mode,
                        len(task.text or ""),
                    )
                    llm_reply = self._normalize_reply(
                        self.llm_client.generate_reply(
                            task.text,
                            conversation_id=task.conversation_id,
                            customer_id=task.sender,
                        )
                    )
                    if llm_reply:
                        logger.info(
                            "预览服 /chat/completion/private 成功: user=%s reply_len=%s",
                            task.sender,
                            len(llm_reply),
                        )
                        return llm_reply
                except Exception as exc:
                    logger.warning(
                        "预览服 /chat/completion/private 失败: user=%s err=%s",
                        task.sender,
                        exc,
                    )

        if self.config.fallback_text:
            logger.info("自动回复使用兜底回复文本，用户: %s", task.sender)
        return self._normalize_reply(self.config.fallback_text)

    def _match_keyword_rule(self, text):
        for keyword, reply in self.config.keyword_rules:
            if keyword and keyword in text:
                return self._normalize_reply(reply)
        return ""

    def _normalize_reply(self, reply_text):
        reply_text = (reply_text or "").replace("\r", "\n").strip()
        if not reply_text:
            return ""

        lines = [line.strip() for line in reply_text.splitlines() if line.strip()]
        reply_text = "\n".join(lines)
        if len(reply_text) > self.config.max_reply_chars:
            reply_text = reply_text[:self.config.max_reply_chars].rstrip()
        return reply_text
