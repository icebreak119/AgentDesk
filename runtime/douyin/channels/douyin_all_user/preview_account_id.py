"""Resolve AgentDesk account_code/profile_id to Douyin preview accountId."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import OrderedDict
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_CACHE_MAX = 256
_cache: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_cache_lock = Lock()


def _looks_like_douyin_account_id(value: str) -> bool:
    text = (value or "").strip()
    return bool(text) and text.isdigit() and len(text) >= 6


def _pick_douyin_account_id(*candidates: Any) -> str:
    for raw in candidates:
        text = str(raw or "").strip()
        if text and _looks_like_douyin_account_id(text):
            return text
    for raw in candidates:
        text = str(raw or "").strip()
        if text and not _UUID_RE.match(text):
            return text
    return ""


def _from_profile_meta(profile_id: str) -> str:
    try:
        from channels.douyin_all_user import session_store

        meta = session_store.load_profile_meta(profile_id)
        if isinstance(meta, dict):
            return _pick_douyin_account_id(
                meta.get("douyin_id"),
                meta.get("douyin_uid"),
                meta.get("user_id"),
                meta.get("im_uid"),
            )
    except Exception:
        return ""
    return ""


def _from_im_accounts_db(profile_id: str, db_path: str) -> str:
    pid = (profile_id or "").strip()
    path = (db_path or "").strip()
    if not pid or not path:
        return ""
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT douyin_uid FROM im_accounts WHERE account_code = ? LIMIT 1",
                (pid,),
            ).fetchone()
            if row is not None:
                return _pick_douyin_account_id(row[0])
    except Exception as exc:
        logger.debug("read im_accounts.douyin_uid failed profile=%s: %s", pid, exc)
    return ""


def invalidate_preview_account_id_cache(profile_id: str = "") -> None:
    pid = (profile_id or "").strip()
    with _cache_lock:
        if not pid:
            _cache.clear()
            return
        for key in [item for item in _cache if item[0] == pid]:
            _cache.pop(key, None)


def _cache_get(profile_id: str, db_path: str) -> str | None:
    key = (profile_id, db_path)
    with _cache_lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


def _cache_set(profile_id: str, db_path: str, value: str) -> None:
    key = (profile_id, db_path)
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def resolve_preview_account_id(profile_id: str, *, db_path: str = "") -> str:
    pid = (profile_id or "").strip()
    if not pid:
        return ""
    path = (db_path or "").strip()
    cached = _cache_get(pid, path)
    if cached is not None:
        return cached
    if _looks_like_douyin_account_id(pid):
        resolved = pid
    else:
        resolved = _from_profile_meta(pid) or _from_im_accounts_db(pid, path) or pid
    if resolved == pid and _UUID_RE.match(pid):
        logger.warning("profile_id=%s was not resolved to a Douyin account id", pid)
    _cache_set(pid, path, resolved)
    return resolved


def warm_preview_account_id_cache(profile_ids: list[str]) -> int:
    warmed = 0
    for raw in profile_ids or []:
        if resolve_preview_account_id(str(raw or "").strip()):
            warmed += 1
    return warmed
