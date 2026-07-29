import logging
import sys
import threading
import time
from contextlib import contextmanager

from utils.im_account_contract import (
    AccountStatus,
    FULLY_ACTIVE_STABLE_SECONDS,
    READY_FOR_NEXT_STABLE_SECONDS,
)
from utils.im_account_store import update_im_account_status
from utils.log_util import get_logger

logger = get_logger("dy.account_manager")


def _error_text(error):
    if error is None:
        return ""
    return str(error).strip() or repr(error)


def _looks_like_credentials_error(error_message):
    lowered = error_message.lower()
    markers = [
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "sessionid",
        "login",
        "cookie",
        "handshake status",
    ]
    return any(marker in lowered for marker in markers)


class RuntimeStatusReporter:
    def __init__(self, db_path, account_code):
        self.db_path = db_path
        self.account_code = account_code
        self.current_status = None
        self._lock = threading.Lock()

    def set_status(self, status, last_error="", douyin_uid=None):
        with self._lock:
            update_im_account_status(
                self.db_path,
                self.account_code,
                status,
                last_error=last_error,
                douyin_uid=douyin_uid,
            )
            self.current_status = status
        if last_error:
            logger.info("[%s] status -> %s: %s", self.account_code, status, last_error)
        else:
            logger.info("[%s] status -> %s", self.account_code, status)

    def mark_starting(self):
        self.set_status(AccountStatus.STARTING, "")

    def mark_ready_for_next(self):
        self.set_status(AccountStatus.READY_FOR_NEXT, "")

    def mark_fully_active(self):
        self.set_status(AccountStatus.FULLY_ACTIVE, "")

    def mark_need_refresh(self, error):
        self.set_status(AccountStatus.NEED_REFRESH, _error_text(error))

    def mark_runtime_failure(self, error):
        if self.current_status in (AccountStatus.NEED_REFRESH, AccountStatus.ERROR):
            return
        message = _error_text(error)
        status = AccountStatus.NEED_REFRESH if _looks_like_credentials_error(message) else AccountStatus.ERROR
        self.set_status(status, message)


class RuntimeStatusWatcher:
    def __init__(
        self,
        recv_msg,
        reporter,
        ready_seconds=READY_FOR_NEXT_STABLE_SECONDS,
        fully_active_seconds=FULLY_ACTIVE_STABLE_SECONDS,
    ):
        self.recv_msg = recv_msg
        self.reporter = reporter
        self.ready_seconds = ready_seconds
        self.fully_active_seconds = fully_active_seconds
        self.thread = threading.Thread(
            target=self._run,
            name=f"dy-im-status-{reporter.account_code}",
            daemon=True,
        )

    def start(self):
        self.thread.start()

    def join(self, timeout=None):
        self.thread.join(timeout=timeout)

    def _run(self):
        while not self.recv_msg.connected_event.wait(timeout=0.5):
            if self.recv_msg.closed_event.is_set() or self.recv_msg.error_event.is_set():
                return
        opened_at = self.recv_msg.opened_at or time.monotonic()
        last_opened_at = opened_at
        ready_sent = False
        fully_active_sent = False

        while not self.recv_msg.closed_event.is_set() and not self.recv_msg.error_event.is_set():
            if not self.recv_msg.connected_event.wait(timeout=0.5):
                continue

            current_opened_at = self.recv_msg.opened_at or time.monotonic()
            if current_opened_at != last_opened_at:
                opened_at = current_opened_at
                last_opened_at = current_opened_at

            elapsed = time.monotonic() - opened_at
            if not ready_sent and elapsed >= self.ready_seconds:
                self.reporter.mark_ready_for_next()
                ready_sent = True
            if not fully_active_sent and elapsed >= self.fully_active_seconds:
                self.reporter.mark_fully_active()
                fully_active_sent = True
                return
            time.sleep(0.5)


class _AccountLogStream:
    def __init__(self, wrapped, account_code):
        self.wrapped = wrapped
        self.account_code = account_code
        self._line_start = True
        self._lock = threading.Lock()

    def write(self, text):
        if not isinstance(text, str):
            text = str(text)
        prefix = f"[{self.account_code}]"
        with self._lock:
            for chunk in text.splitlines(True):
                if self._line_start and chunk and chunk != "\n":
                    if chunk.startswith(prefix):
                        self.wrapped.write(chunk)
                    else:
                        self.wrapped.write(f"{prefix} {chunk}")
                else:
                    self.wrapped.write(chunk)
                self._line_start = chunk.endswith("\n")
        return len(text)

    def flush(self):
        return self.wrapped.flush()

    def isatty(self):
        return self.wrapped.isatty()

    @property
    def encoding(self):
        return getattr(self.wrapped, "encoding", None)

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


@contextmanager
def prefixed_account_logs(account_code):
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _AccountLogStream(original_stdout, account_code)
    sys.stderr = _AccountLogStream(original_stderr, account_code)
    try:
        yield
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
