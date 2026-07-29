# -*- coding: utf-8 -*-
"""IPC 收信层 → 主进程 Webhook 推送（RAM-first 模式）。"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


def webhook_url() -> str:
    return (
        os.environ.get("DY_IPC_WEBHOOK_URL") or "http://127.0.0.1:5002/api/douyin/recv"
    ).strip()


def post_message_event(payload: Dict[str, Any]) -> None:
    """后台线程 POST，不阻塞 WS 收信线程。"""

    def _post() -> None:
        try:
            import requests

            resp = requests.post(webhook_url(), json=payload, timeout=5)
            if resp.status_code >= 400:
                logger.warning(
                    "douyin ipc webhook failed: status=%s body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as exc:
            logger.debug("douyin ipc webhook post error: %s", exc)

    threading.Thread(target=_post, name="dy-ipc-webhook", daemon=True).start()


def make_message_event_handler(account_code: str) -> Callable[[Dict[str, Any]], None]:
    code = str(account_code or "").strip()

    def _handler(event: Dict[str, Any]) -> None:
        body = dict(event or {})
        body.setdefault("event", "message")
        body["account_code"] = code
        post_message_event(body)

    return _handler
