import logging
import mimetypes
import sqlite3
import time
from pathlib import Path

import requests

from dy_apis.douyin_api import DouyinAPI
from utils.im_message_store import upsert_conversation_profile, upsert_self_profile

logger = logging.getLogger("dy.profile_enricher")


def _first_url(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        urls = value.get("url_list") or value.get("urlList") or []
        if isinstance(urls, list):
            for item in urls:
                text = str(item or "").strip()
                if text:
                    return text
        for key in ("uri", "url"):
            text = str(value.get(key) or "").strip()
            if text.startswith("http"):
                return text
    return ""


def _extract_user_record(item):
    if not isinstance(item, dict):
        return {}
    for key in ("user_info", "user", "aweme_info"):
        nested = item.get(key)
        if isinstance(nested, dict):
            if key == "aweme_info":
                nested = nested.get("author")
            if isinstance(nested, dict):
                return nested
    return item


def _user_id_matches(user, target_user_id):
    target = str(target_user_id or "").strip()
    if not target:
        return False
    for key in ("uid", "id", "user_id", "uid_str", "short_id", "unique_id"):
        if str(user.get(key) or "").strip() == target:
            return True
    return False


def _extract_profile(user):
    nickname = str(user.get("nickname") or user.get("display_name") or "").strip()
    if not nickname:
        for key in ("remark_name", "unique_id", "short_id"):
            candidate = str(user.get(key) or "").strip()
            if not candidate:
                continue
            if candidate.isdigit() and len(candidate) >= 6:
                continue
            nickname = candidate
            break
    avatar_url = (
        _first_url(user.get("avatar_thumb"))
        or _first_url(user.get("avatar_medium"))
        or _first_url(user.get("avatar_larger"))
        or _first_url(user.get("avatar_url"))
    )
    return nickname, avatar_url


def _fetch_user_by_uid(auth, user_id):
    uid = str(user_id or "").strip()
    if not uid:
        return {}
    try:
        data = DouyinAPI.get_user_info_by_uid(auth, uid)
    except Exception:
        return {}
    if not isinstance(data, dict) or int(data.get("status_code") or 0) != 0:
        return {}
    user = data.get("user")
    if not isinstance(user, dict):
        return {}
    return user if _user_id_matches(user, uid) else {}


def _search_user_by_uid(auth, user_id):
    uid = str(user_id or "").strip()
    if not uid:
        return {}
    try:
        data = DouyinAPI.search_user(auth, uid, offset="0", num="10")
        candidates = data.get("user_list") if isinstance(data, dict) else []
    except Exception:
        candidates = []

    if not isinstance(candidates, list):
        return {}
    for item in candidates:
        user = _extract_user_record(item)
        if _user_id_matches(user, uid):
            return user
    return {}


def _fetch_peer_user(auth, peer_user_id, account_code):
    peer = str(peer_user_id or "").strip()
    if not peer:
        return {}

    user = _fetch_user_by_uid(auth, peer)
    if user:
        logger.info("[%s] peer profile 使用 uid 直查成功: peer=%s", account_code, peer)
        return user

    user = _search_user_by_uid(auth, peer)
    if user:
        logger.info("[%s] peer profile 使用 uid 搜索成功: peer=%s", account_code, peer)
        return user

    logger.warning("[%s] peer profile 未命中: peer=%s", account_code, peer)
    return {}


def _fetch_self_user(auth, user_id, account_code):
    """优先通过 sec_uid 获取本人资料，失败时回退到 uid 查询。"""
    last_error = None

    try:
        sec_uid = DouyinAPI.get_my_sec_uid(auth)
        data = DouyinAPI.get_user_info(auth, f"https://www.douyin.com/user/{sec_uid}")
        user = data.get("user") if isinstance(data, dict) else {}
        if isinstance(user, dict) and user:
            logger.info("[%s] self profile 使用 sec_uid 通路成功", account_code)
            return user
        logger.warning("[%s] self profile sec_uid 通路返回空 user", account_code)
    except Exception as exc:
        last_error = exc
        logger.warning("[%s] self profile sec_uid 通路失败: %s", account_code, exc)

    uid = str(user_id or "").strip()
    if uid:
        user = _fetch_user_by_uid(auth, uid)
        if user:
            logger.info("[%s] self profile 使用 uid 通路成功: uid=%s", account_code, uid)
            return user

        user = _search_user_by_uid(auth, uid)
        if user:
            logger.info("[%s] self profile 使用 uid 搜索通路成功: uid=%s", account_code, uid)
            return user

        logger.warning("[%s] self profile uid 通路未命中: uid=%s", account_code, uid)

    if last_error is not None:
        raise last_error
    return {}


def _download_avatar(avatar_url, db_path, account_code, user_id, auth=None):
    url = str(avatar_url or "").strip()
    if not url:
        return ""
    avatar_dir = Path(db_path).expanduser().resolve().parent / "avatars" / str(account_code or "")
    avatar_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?", 1)[0]).suffix
    if not suffix or len(suffix) > 8:
        suffix = mimetypes.guess_extension("image/jpeg") or ".jpg"
    target = avatar_dir / f"{str(user_id or 'avatar').strip()}{suffix}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.douyin.com/",
    }
    cookies = getattr(auth, "cookie", None) if auth is not None else None
    for attempt in range(1, 4):
        try:
            resp = requests.get(
                url,
                timeout=20,
                verify=False,
                headers=headers,
                cookies=cookies,
            )
            resp.raise_for_status()
            target.write_bytes(resp.content)
            if target.is_file() and target.stat().st_size > 0:
                return str(target)
            logger.warning(
                "[%s] avatar download produced empty file: user_id=%s attempt=%s url=%s",
                account_code,
                user_id,
                attempt,
                url,
            )
        except Exception as exc:
            logger.warning(
                "[%s] avatar download failed: user_id=%s attempt=%s error=%s",
                account_code,
                user_id,
                attempt,
                exc,
            )
        if attempt < 3:
            time.sleep(0.5 * attempt)
    return ""


def enrich_peer_profile(db_path, account_code, auth, peer_user_id, conversation_id=""):
    started_at = time.time()
    peer = str(peer_user_id or "").strip()
    if not peer:
        return {}

    logger.info(
        "[%s] peer profile 刷新开始: peer=%s conversation_id=%s",
        account_code,
        peer,
        str(conversation_id or "").strip(),
    )
    nickname = ""
    avatar_url = ""
    for attempt in range(1, 4):
        matched = _fetch_peer_user(auth, peer, account_code)
        nickname, avatar_url = _extract_profile(matched)
        if nickname or avatar_url:
            logger.info(
                "[%s] peer profile 获取成功: peer=%s attempt=%s nickname=%s avatar=%s",
                account_code,
                peer,
                attempt,
                nickname,
                "yes" if avatar_url else "no",
            )
            break
        logger.warning(
            "[%s] peer profile 第 %d 次尝试结果为空: peer=%s conversation_id=%s",
            account_code,
            attempt,
            peer,
            str(conversation_id or "").strip(),
        )
        if attempt < 3:
            time.sleep(0.5 * attempt)

    avatar_local_path = ""
    if avatar_url:
        avatar_local_path = _download_avatar(avatar_url, db_path, account_code, peer, auth=auth)
        if not avatar_local_path:
            logger.warning("[%s] peer 头像下载失败: peer=%s url=%s", account_code, peer, avatar_url)

    display = str(nickname or "").strip()
    if not display and not avatar_url and not avatar_local_path:
        logger.warning(
            "[%s] peer profile 未补全成功: peer=%s conversation_id=%s elapsed=%ss",
            account_code,
            peer,
            str(conversation_id or "").strip(),
            round(time.time() - started_at, 2),
        )
        return {
            "display_name": "",
            "avatar_url": "",
            "avatar_local_path": "",
        }

    upsert_conversation_profile(
        db_path,
        account_code,
        conversation_id,
        display,
        source="reverse_runtime_profile",
        avatar_url=avatar_url,
        avatar_local_path=avatar_local_path,
    )
    logger.info(
        "[%s] peer profile 已写入 conversation_profiles: peer=%s display=%s has_avatar_url=%s has_avatar_local=%s elapsed=%ss",
        account_code,
        peer,
        display or "-",
        bool(avatar_url),
        bool(avatar_local_path),
        round(time.time() - started_at, 2),
    )
    return {
        "display_name": display,
        "avatar_url": avatar_url,
        "avatar_local_path": avatar_local_path,
    }


def enrich_self_profile(db_path, account_code, auth, max_retries=3):
    """获取自己的头像和昵称，带重试机制。

    返回 dict: display_name, user_id, avatar_url, avatar_local_path
    失败时返回已获取到的部分字段（如 user_id），不会完全为空。
    """
    started_at = time.time()
    user_id = ""
    nickname = ""
    avatar_url = ""
    logger.info("[%s] self profile 刷新开始: max_retries=%s", account_code, max_retries)

    # 第一步：获取 uid（不重试，快速失败）
    try:
        user_id = str(auth.get_uid())
        logger.info("[%s] self uid 获取成功: %s", account_code, user_id)
    except Exception as exc:
        logger.warning("[%s] self uid 获取失败: %s", account_code, exc)

    # 第二步：获取昵称和头像 URL（带重试）
    for attempt in range(1, max_retries + 1):
        try:
            user = _fetch_self_user(auth, user_id, account_code)
            if isinstance(user, dict):
                nickname, avatar_url = _extract_profile(user)
            if nickname or avatar_url:
                logger.info(
                    "[%s] self profile 获取成功: attempt=%s nickname=%s avatar=%s",
                    account_code,
                    attempt,
                    nickname,
                    "yes" if avatar_url else "no",
                )
                break
            else:
                logger.warning(
                    "[%s] self profile 第 %d 次尝试: nickname 和 avatar 都为空",
                    account_code,
                    attempt,
                )
        except Exception as exc:
            logger.warning("[%s] self profile 第 %d 次尝试失败: %s", account_code, attempt, exc)
        if attempt < max_retries:
            time.sleep(1.0 * attempt)

    # 第三步：下载头像（不重试，头像缺失不影响核心功能）
    avatar_local_path = ""
    if avatar_url:
        avatar_local_path = _download_avatar(
            avatar_url,
            db_path,
            account_code,
            user_id or account_code,
            auth=auth,
        )
        if not avatar_local_path:
            logger.warning("[%s] self 头像下载失败: url=%s", account_code, avatar_url)

    # 第四步：写回数据库
    display = nickname or user_id or account_code
    try:
        upsert_self_profile(
            db_path,
            account_code,
            display_name=display,
            user_id=user_id,
            avatar_url=avatar_url,
            avatar_local_path=avatar_local_path,
        )
        logger.info(
            "[%s] self profile 已写入 conversation_profiles: display=%s has_avatar_url=%s has_avatar_local=%s",
            account_code,
            display,
            bool(avatar_url),
            bool(avatar_local_path),
        )
    except Exception as exc:
        logger.error("[%s] self profile 写入 conversation_profiles 失败: %s", account_code, exc)

    if nickname:
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE im_accounts
                    SET nickname = ?
                    WHERE account_code = ? AND IFNULL(nickname, '') = ''
                    """,
                    (nickname, account_code),
                )
                conn.commit()
            logger.info("[%s] self nickname 已写入 im_accounts: nickname=%s", account_code, nickname)
        except Exception as exc:
            logger.warning("[%s] self nickname 写入 im_accounts 失败: %s", account_code, exc)
    else:
        logger.warning("[%s] self nickname 仍为空，UI 可能继续显示未识别账号", account_code)

    logger.info(
        "[%s] self profile 最终结果: nickname=%s user_id=%s avatar=%s elapsed=%ss",
        account_code,
        nickname,
        user_id,
        "ok" if avatar_local_path else "empty",
        round(time.time() - started_at, 2),
    )
    return {
        "nickname": nickname,
        "display_name": display,
        "user_id": user_id,
        "avatar_url": avatar_url,
        "avatar_local_path": avatar_local_path,
    }
