"""Python client for Douyin reverse IPC supervisor (stdio NDJSON JSON-RPC)."""

from __future__ import annotations

import atexit
import json
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from channels.douyin_reverse_ipc.errors import RpcError

_DEFAULT_TIMEOUT = 30
_SEND_TIMEOUT = 60


class DouyinReverseClient:
    def __init__(
        self,
        db_path: str = "",
        *,
        python_executable: str = "",
        supervisor_module: str = "channels.douyin_reverse_ipc.supervisor",
    ):
        self.db_path = str(Path(db_path).expanduser().resolve()) if db_path else ""
        self.python_executable = python_executable or sys.executable
        self.supervisor_module = supervisor_module
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.RLock()
        atexit.register(self.close)

    def _ensure_started(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if not self.db_path:
                raise RpcError("invalid_request", "db_path is required")
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(
                [
                    self.python_executable,
                    "-m",
                    self.supervisor_module,
                    "--db-path",
                    self.db_path,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _call(self, method: str, params: Optional[dict[str, Any]] = None, timeout: int = _DEFAULT_TIMEOUT) -> dict[str, Any]:
        req_id = str(uuid.uuid4())
        line = json.dumps(
            {"id": req_id, "method": method, "params": params or {}},
            ensure_ascii=False,
        ) + "\n"
        with self._lock:
            self._ensure_started()
            proc = self._proc
            if proc is None or proc.stdin is None or proc.stdout is None:
                raise RpcError("internal", "supervisor process not available")
            proc.stdin.write(line)
            proc.stdin.flush()
            # Serial client: read one response line
            deadline_timeout = max(1, int(timeout or _DEFAULT_TIMEOUT))
            # Use communicate-style read with threading for timeout
            holder: dict[str, Any] = {}

            def _reader():
                try:
                    holder["line"] = proc.stdout.readline()
                except Exception as exc:
                    holder["error"] = exc

            t = threading.Thread(target=_reader, daemon=True)
            t.start()
            t.join(timeout=deadline_timeout)
            if t.is_alive():
                raise RpcError("internal", f"rpc timeout after {deadline_timeout}s: {method}")
            if "error" in holder:
                raise RpcError("internal", str(holder["error"]))
            raw = str(holder.get("line") or "").strip()
            if not raw:
                err = ""
                try:
                    if proc.stderr:
                        err = (proc.stderr.read() or "")[:500]
                except Exception:
                    pass
                raise RpcError("internal", f"empty supervisor response: {err}")
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RpcError("invalid_request", f"invalid response json: {exc}") from exc
            if obj.get("id") != req_id:
                raise RpcError("internal", "response id mismatch")
            if not obj.get("ok"):
                error = obj.get("error") or {}
                raise RpcError(str(error.get("code") or "internal"), str(error.get("message") or ""))
            data = obj.get("data")
            return data if isinstance(data, dict) else {}

    def ping(self) -> dict[str, Any]:
        return self._call("ping")

    def get_db_path(self) -> dict[str, Any]:
        return self._call("get_db_path")

    def start_account(self, account_code: str) -> dict[str, Any]:
        return self._call("start_account", {"account_code": account_code}, timeout=120)

    def stop_account(self, account_code: str) -> dict[str, Any]:
        return self._call("stop_account", {"account_code": account_code})

    def stop_all(self) -> dict[str, Any]:
        return self._call("stop_all")

    def reload_credentials(self, account_code: str) -> dict[str, Any]:
        return self._call("reload_credentials", {"account_code": account_code}, timeout=120)

    def get_account_status(self, account_code: str) -> dict[str, Any]:
        return self._call("get_account_status", {"account_code": account_code})

    def list_accounts(self) -> dict[str, Any]:
        return self._call("list_accounts")

    def send_text(
        self,
        account_code: str,
        *,
        text: str,
        conversation_id: str = "",
        peer_uid: str = "",
        client_msg_id: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "send_text",
            {
                "account_code": account_code,
                "text": text,
                "conversation_id": conversation_id,
                "peer_uid": peer_uid,
                "client_msg_id": client_msg_id,
            },
            timeout=_SEND_TIMEOUT,
        )

    def send_emoji(
        self,
        account_code: str,
        *,
        emoji_url: str,
        emoji_name: str = "",
        conversation_id: str = "",
        peer_uid: str = "",
        client_msg_id: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "send_emoji",
            {
                "account_code": account_code,
                "emoji_url": emoji_url,
                "emoji_name": emoji_name,
                "conversation_id": conversation_id,
                "peer_uid": peer_uid,
                "client_msg_id": client_msg_id,
            },
            timeout=_SEND_TIMEOUT,
        )

    def send_image(
        self,
        account_code: str,
        *,
        image_path: str,
        conversation_id: str = "",
        peer_uid: str = "",
        client_msg_id: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "send_image",
            {
                "account_code": account_code,
                "image_path": image_path,
                "conversation_id": conversation_id,
                "peer_uid": peer_uid,
                "client_msg_id": client_msg_id,
            },
            timeout=_SEND_TIMEOUT,
        )

    def get_conversations(self, account_code: str, limit: int = 50) -> dict[str, Any]:
        return self._call("get_conversations", {"account_code": account_code, "limit": limit})

    def get_messages(
        self,
        account_code: str,
        conversation_id: str,
        *,
        after_id: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return self._call(
            "get_messages",
            {
                "account_code": account_code,
                "conversation_id": conversation_id,
                "after_id": after_id,
                "limit": limit,
            },
        )
