"""Per-account runtime slots for Douyin reverse IPC."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from channels.douyin_reverse_ipc._runtime_path import ensure_reverse_runtime_on_path
from channels.douyin_reverse_ipc.errors import RpcError
from channels.douyin_reverse_ipc.profile_service import get_account_profile
from channels.douyin_reverse_ipc import profile_sync

logger = logging.getLogger(__name__)

RecvFactory = Callable[[str, Any, str], Any]


@dataclass
class _Slot:
    account_code: str
    thread: Optional[threading.Thread] = None
    recv: Any = None
    last_error: str = ""
    last_msg_at: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)


def _require_account_code(account_code: str) -> str:
    code = str(account_code or "").strip()
    if not code:
        raise RpcError("account_required", "account_code is required")
    return code


def _default_recv_factory(
    account_code: str,
    auth: Any,
    db_path: str,
    *,
    status_reporter: Any = None,
) -> Any:
    ensure_reverse_runtime_on_path()
    try:
        from channels.douyin_reverse_ipc.webhook import make_message_event_handler
        from dy_apis.douyin_recv_msg import (
            MANAGED_RUNTIME_MAX_RECONNECT_FAILURES,
            MANAGED_RUNTIME_RECONNECT_DELAY_SECONDS,
            DouyinRecvMsg,
            build_managed_reply_engine,
        )
    except ImportError as exc:
        hint = str(exc or "")
        if "websocket" in hint.lower():
            dep_hint = "请执行 pip install websocket-client"
        else:
            dep_hint = "请完全退出客户端后重试；若仍失败请查看 im_runtime 日志"
        raise RpcError(
            "dependency_missing",
            f"抖音收信运行时依赖缺失: {exc}. {dep_hint}",
        ) from exc

    handler = make_message_event_handler(account_code)
    douyin_uid = ""
    try:
        from utils.im_account_store import load_im_accounts_from_db

        accounts = load_im_accounts_from_db(db_path, account_code=account_code, enabled_only=False)
        if accounts:
            douyin_uid = str(accounts[0].douyin_uid or "").strip()
    except Exception:
        douyin_uid = ""
    reply_engine = build_managed_reply_engine(auth, account_code, db_path)
    logger.info("account_code=%s IM reply engine: %s", account_code, reply_engine.describe())
    return DouyinRecvMsg(
        auth,
        auto_reconnect=True,
        reply_engine=reply_engine,
        account_code=account_code,
        message_db_path=db_path,
        message_event_handler=handler,
        ram_first=True,
        self_user_id=douyin_uid,
        status_reporter=status_reporter,
        max_reconnect_failures=MANAGED_RUNTIME_MAX_RECONNECT_FAILURES,
        reconnect_delay_seconds=MANAGED_RUNTIME_RECONNECT_DELAY_SECONDS,
    )


class AccountSlotManager:
    def __init__(self, db_path: str, *, recv_factory: Optional[RecvFactory] = None):
        self.db_path = str(db_path)
        self._recv_factory = recv_factory or _default_recv_factory
        self._slots: dict[str, _Slot] = {}
        self._lock = threading.RLock()

    def _load_account(self, account_code: str):
        ensure_reverse_runtime_on_path()
        from utils.im_account_store import load_im_accounts_from_db

        accounts = load_im_accounts_from_db(
            self.db_path, account_code=account_code, enabled_only=True
        )
        if not accounts:
            raise RpcError("account_not_found", f"im account not found: {account_code}")
        return accounts[0]

    def _build_auth(self, account) -> Any:
        ensure_reverse_runtime_on_path()
        from utils.common_util import build_im_auth_from_credentials
        from utils.im_account_store import InvalidIMAccountCredentials, validate_im_account_credentials

        try:
            validate_im_account_credentials(account)
            return build_im_auth_from_credentials(
                account.cookies_str,
                account.web_protect_str,
                account.keys_str,
            )
        except InvalidIMAccountCredentials as exc:
            raise RpcError("auth_invalid", str(exc)) from exc
        except Exception as exc:
            raise RpcError("auth_invalid", str(exc) or "auth_invalid") from exc

    def start_account(self, account_code: str) -> dict[str, Any]:
        code = _require_account_code(account_code)
        account = self._load_account(code)
        with self._lock:
            slot = self._slots.get(code)
            if slot and slot.thread and slot.thread.is_alive():
                return self.get_account_status(code)

            auth = None
            # Fake factories may ignore auth; still try build for real path.
            try:
                auth = self._build_auth(account)
            except RpcError:
                if self._recv_factory is _default_recv_factory:
                    raise
                auth = None

            reporter = None
            if self._recv_factory is _default_recv_factory:
                ensure_reverse_runtime_on_path()
                from utils.im_account_manager import RuntimeStatusReporter, RuntimeStatusWatcher

                reporter = RuntimeStatusReporter(self.db_path, code)
                reporter.mark_starting()
                recv = _default_recv_factory(
                    code,
                    auth,
                    self.db_path,
                    status_reporter=reporter,
                )
            else:
                recv = self._recv_factory(code, auth, self.db_path)

            def _runner():
                watcher = None
                try:
                    logger.info("account_code=%s recv start", code)
                    if reporter is not None:
                        from utils.im_account_manager import RuntimeStatusWatcher

                        watcher = RuntimeStatusWatcher(recv, reporter)
                        watcher.start()
                    if hasattr(recv, "start"):
                        recv.start()
                except Exception as exc:
                    with self._lock:
                        s = self._slots.get(code)
                        if s is not None:
                            s.last_error = str(exc) or type(exc).__name__
                    logger.exception("account_code=%s recv crashed", code)
                finally:
                    if watcher is not None:
                        watcher.join(timeout=1.0)

            thread = threading.Thread(
                target=_runner,
                name=f"dy-reverse-recv-{code}",
                daemon=True,
            )
            slot = _Slot(account_code=code, thread=thread, recv=recv, last_error="")
            self._slots[code] = slot
            thread.start()
            if auth is not None:
                profile_sync.refresh_profiles_async(self.db_path, code, auth)
            # Brief settle so status.running is true for fast fake recv.
            time.sleep(0.05)
            return self.get_account_status(code)

    def _request_stop_recv(self, recv: Any) -> None:
        if recv is None:
            return
        try:
            if hasattr(recv, "stop_requested"):
                recv.stop_requested = True
            if hasattr(recv, "request_stop") and callable(recv.request_stop):
                recv.request_stop()
            ws = getattr(recv, "ws", None)
            if ws is not None and hasattr(ws, "close"):
                try:
                    ws.close()
                except Exception:
                    pass
        except Exception:
            logger.exception("failed requesting recv stop")

    def stop_account(self, account_code: str) -> dict[str, Any]:
        code = _require_account_code(account_code)
        with self._lock:
            slot = self._slots.get(code)
            if not slot:
                return {
                    "account_code": code,
                    "running": False,
                    "connected": False,
                    "last_error": "",
                    "last_msg_at": "",
                }
            self._request_stop_recv(slot.recv)
            thread = slot.thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        with self._lock:
            slot = self._slots.get(code)
            if slot:
                slot.thread = None
            return self.get_account_status(code)

    def stop_all(self) -> dict[str, Any]:
        with self._lock:
            codes = list(self._slots.keys())
        stopped = [self.stop_account(code) for code in codes]
        return {"accounts": stopped}

    def reload_credentials(self, account_code: str) -> dict[str, Any]:
        code = _require_account_code(account_code)
        # Warm restart: stop then start with latest DB credentials.
        self.stop_account(code)
        return self.start_account(code)

    def refresh_profiles(self, account_code: str) -> dict[str, Any]:
        code = _require_account_code(account_code)
        account = self._load_account(code)
        auth = self._build_auth(account)
        return profile_sync.refresh_profiles(self.db_path, code, auth)

    def get_account_status(self, account_code: str) -> dict[str, Any]:
        code = _require_account_code(account_code)
        with self._lock:
            slot = self._slots.get(code)
            if not slot:
                # Still validate account exists for clearer errors on status of unknown codes?
                # Spec: get_account_status for started accounts; unknown may be not found.
                try:
                    self._load_account(code)
                except RpcError:
                    raise
                return {
                    "account_code": code,
                    "running": False,
                    "connected": False,
                    "last_error": "",
                    "last_msg_at": "",
                }
            running = bool(slot.thread and slot.thread.is_alive())
            recv = slot.recv
            connected = False
            last_error = slot.last_error
            if recv is not None:
                connected_event = getattr(recv, "connected_event", None)
                if connected_event is not None and hasattr(connected_event, "is_set"):
                    connected = bool(connected_event.is_set())
                elif hasattr(recv, "connected"):
                    connected = bool(getattr(recv, "connected"))
                err = str(getattr(recv, "last_error", "") or "").strip()
                if err:
                    last_error = err
            return {
                "account_code": code,
                "running": running,
                "connected": connected,
                "last_error": last_error,
                "last_msg_at": slot.last_msg_at,
            }

    def list_accounts(self) -> dict[str, Any]:
        ensure_reverse_runtime_on_path()
        from utils.im_account_store import load_im_accounts_from_db

        accounts = load_im_accounts_from_db(self.db_path, enabled_only=False)
        items = []
        for account in accounts:
            code = account.account_code
            with self._lock:
                slot = self._slots.get(code)
                running = bool(slot and slot.thread and slot.thread.is_alive())
                last_error = slot.last_error if slot else ""
                last_msg_at = slot.last_msg_at if slot else ""
                connected = False
                if slot and slot.recv is not None:
                    connected_event = getattr(slot.recv, "connected_event", None)
                    if connected_event is not None and hasattr(connected_event, "is_set"):
                        connected = bool(connected_event.is_set())
                    elif hasattr(slot.recv, "connected"):
                        connected = bool(getattr(slot.recv, "connected"))
            profile = get_account_profile(self.db_path, code)
            items.append(
                {
                    "account_code": code,
                    "nickname": profile.get("nickname") or "",
                    "display_name": profile.get("display_name") or code,
                    "douyin_uid": profile.get("douyin_uid") or "",
                    "avatar_api": profile.get("avatar_api") or "",
                    "running": running,
                    "connected": connected,
                    "last_error": last_error,
                    "last_msg_at": last_msg_at,
                    "enabled": bool(account.enabled),
                }
            )
        return {"accounts": items}
