"""Standalone HTTP client for optional private-chat preview service."""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

import requests

from channels.private_chat_common import (
    build_private_chat_completion_body,
    parse_private_chat_completion_response,
    summarize_private_chat_completion_response,
)

logger = logging.getLogger(__name__)


def _preview_base_url() -> str:
    raw = (
        os.environ.get("AGENTDESK_PREVIEW_API_BASE", "")
        or os.environ.get("YUNDUO_PREVIEW_API_BASE", "")
    )
    return raw.strip().rstrip("/")


def _preview_path() -> str:
    raw = (
        os.environ.get("AGENTDESK_PREVIEW_PRIVATE_CHAT_PATH", "")
        or os.environ.get("YUNDUO_PREVIEW_PRIVATE_CHAT_PATH", "")
        or "/chat/completion/private"
    )
    return raw if raw.startswith("/") else f"/{raw}"


def resolve_preview_access_token(access_token: str = "") -> str:
    return (
        (access_token or "").strip()
        or os.environ.get("AGENTDESK_PREVIEW_ACCESS_TOKEN", "").strip()
        or os.environ.get("YUNDUO_PREVIEW_ACCESS_TOKEN", "").strip()
    )


def request_private_chat_sync(
    *,
    account_id: str,
    content: str,
    message_list: Optional[List[Dict[str, str]]] = None,
    customer_name: str = "",
    customer_id: str = "",
    access_token: str = "",
    timeout_sec: float = 45.0,
    system_prompt: str = "",
    profile_id: str = "",
    channel: str = "",
) -> tuple[Optional[str], str]:
    """Call an optional private-chat preview service.

    This is not required for the standalone competition demo. If no preview
    service is configured, callers receive a clear degraded error and can fall
    back to fixed replies or the OpenAI-compatible LLM client.
    """

    base_url = _preview_base_url()
    if not base_url:
        return None, "AGENTDESK_PREVIEW_API_BASE is not configured"
    token = resolve_preview_access_token(access_token)
    if not token:
        return None, "preview access token is not configured"

    try:
        body = build_private_chat_completion_body(
            account_id=account_id,
            content=content,
            message_list=message_list,
            customer_name=customer_name,
            customer_id=customer_id,
            system_prompt=system_prompt,
        )
    except ValueError as exc:
        return None, str(exc)

    url = f"{base_url}{_preview_path()}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    try:
        response = requests.post(url, json=body, headers=headers, timeout=float(timeout_sec))
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("private chat preview request failed: %s", exc)
        return None, str(exc)
    except ValueError:
        payload = None

    if response.status_code < 200 or response.status_code >= 300:
        detail = payload if isinstance(payload, dict) else response.text[:500]
        return None, f"HTTP {response.status_code}: {detail}"

    reply, err = parse_private_chat_completion_response(payload, "")
    logger.info("private chat preview response: %s", summarize_private_chat_completion_response(payload))
    return reply, err
