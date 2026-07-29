from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

_IMAGE_PLACEHOLDER = "[图片]"
_EMOJI_PLACEHOLDER = "[表情]"
_IMAGE_URL_RE = re.compile(r"^\s*\[图片\]\s*(https?://\S+)", re.IGNORECASE)
_EMOJI_URL_RE = re.compile(r"^\s*\[表情\]\s*(https?://\S+)", re.IGNORECASE)


def extract_douyin_image_url(content: str, media_url: str = "") -> str:
    url = str(media_url or "").strip().strip("\"'")
    if url.startswith(("http://", "https://")):
        return url
    text = str(content or "").strip()
    if not text:
        return ""
    matched = _IMAGE_URL_RE.match(text)
    if matched:
        return str(matched.group(1) or "").strip().strip("\"'")
    return ""


def normalize_douyin_image_content(content: str, media_url: str = "") -> str:
    text = str(content or "").strip()
    if extract_douyin_image_url(text, media_url):
        return _IMAGE_PLACEHOLDER
    if text.startswith(_IMAGE_PLACEHOLDER):
        return _IMAGE_PLACEHOLDER
    return text


def extract_douyin_emoji_url_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    url_block = payload.get("url") or {}
    if isinstance(url_block, dict):
        url = _first_http_url_from_container(url_block)
        if url:
            return url
    return ""


def extract_douyin_emoji_url_from_content(content: str, media_url: str = "") -> str:
    url = str(media_url or "").strip().strip("\"'")
    if url.startswith(("http://", "https://")):
        return url
    text = str(content or "").strip()
    if not text:
        return ""
    matched = _EMOJI_URL_RE.match(text)
    if matched:
        return str(matched.group(1) or "").strip().strip("\"'")
    return ""


def normalize_douyin_emoji_content(content: str, media_url: str = "") -> str:
    text = str(content or "").strip()
    if extract_douyin_emoji_url_from_content(text, media_url):
        return _EMOJI_PLACEHOLDER
    if text.startswith(_EMOJI_PLACEHOLDER):
        return _EMOJI_PLACEHOLDER
    return text


def _emoji_cache_dir(db_path: str, account_code: str) -> Path:
    base_dir = (
        Path(db_path).expanduser().resolve().parent
        / "message_emojis"
        / _safe_segment(account_code or "default")
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def ensure_douyin_emoji_cached(
    image_url: str,
    *,
    db_path: str = "",
    account_code: str = "",
    timeout: int = 20,
    preferred_name: str = "",
) -> str:
    url = str(image_url or "").strip().strip("\"'")
    if not url.startswith(("http://", "https://")) or not db_path:
        return ""
    try:
        base_dir = _emoji_cache_dir(db_path, account_code)
    except OSError as exc:
        logger.debug("创建 Douyin 表情缓存目录失败: %s", exc)
        return ""

    cache_key = (
        _safe_segment(preferred_name)
        if preferred_name
        else hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:24]
    )
    existing = _find_existing_cache_file(base_dir, cache_key)
    if existing:
        return existing

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.douyin.com/",
    }
    try:
        response = requests.get(
            url,
            timeout=max(5, int(timeout or 20)),
            verify=False,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.content or b""
        if not payload:
            return ""
        suffix = _guess_suffix_from_response(
            url,
            content_type=str(response.headers.get("Content-Type") or ""),
        )
        return _write_binary_cache_to_dir(
            payload,
            base_dir=base_dir,
            cache_key=cache_key,
            suffix=suffix,
        )
    except Exception as exc:
        logger.debug("Douyin 表情缓存失败 url=%s error=%s", url[:80], exc)
        return ""


def build_douyin_image_inline_payload(image_path: str) -> dict:
    path = Path(str(image_path or "").strip())
    if not path.is_file():
        return {}
    try:
        payload = path.read_bytes()
    except OSError:
        return {}
    if not payload:
        return {}
    encoded = base64.b64encode(payload).decode("ascii")
    return {"inline_pic": encoded}


def build_douyin_emoji_payload(emoji_url: str) -> dict:
    url = str(emoji_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {}
    return {"url": {"url_list": [url]}}


def _first_http_url_from_container(container: object) -> str:
    if not isinstance(container, dict):
        return ""
    for key in (
        "url_list",
        "origin_url_list",
        "large_url_list",
        "medium_url_list",
        "thumb_url_list",
        "play_addr",
        "download_addr",
    ):
        values = container.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    url = str(item.get("url") or item.get("src") or "").strip()
                else:
                    url = str(item or "").strip()
                if url.startswith(("http://", "https://")):
                    return url
        elif isinstance(values, dict):
            nested = _first_http_url_from_container(values)
            if nested:
                return nested
    return ""


def extract_douyin_video_cover_url_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    video_payload = payload.get("video") or {}
    if isinstance(video_payload, dict):
        cover = video_payload.get("cover") or {}
        if isinstance(cover, dict):
            url = _first_http_url_from_container(cover)
            if url:
                return url
    return extract_douyin_image_url_from_payload(payload)


def extract_douyin_video_play_url_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    video_payload = payload.get("video") or {}
    if isinstance(video_payload, dict):
        for key in ("play_addr", "download_addr", "playApi", "play_api"):
            url = _first_http_url_from_container(video_payload.get(key))
            if url:
                return url
        url = _first_http_url_from_container(video_payload)
        if url:
            return url
    resource = payload.get("resource_url") or {}
    if isinstance(resource, dict):
        url = _first_http_url_from_container(resource)
        if url:
            return url
    return ""


def extract_douyin_video_item_id_from_payload(payload: object) -> str:
    """从 IM 视频 payload 提取作品 aweme_id（用于二次解析播放地址）。"""
    if not isinstance(payload, dict):
        return ""
    for key in ("itemId", "item_id", "aweme_id"):
        value = str(payload.get(key) or "").strip()
        if value.isdigit():
            return value
    video_payload = payload.get("video") or {}
    if isinstance(video_payload, dict):
        for key in ("vid", "aweme_id", "item_id", "itemId"):
            value = str(video_payload.get(key) or "").strip()
            if value.isdigit():
                return value
    display = str(payload.get("_display_text") or payload.get("text") or "").strip()
    if display.startswith("[视频]"):
        tail = display[4:].strip()
        if tail.isdigit():
            return tail
    return ""


def resolve_douyin_video_play_url(auth, payload: object) -> str:
    """优先从 payload 直取播放地址；缺失时按 aweme_id 调作品详情 API。"""
    direct = extract_douyin_video_play_url_from_payload(payload)
    if direct:
        return direct
    if auth is None:
        return ""
    item_id = extract_douyin_video_item_id_from_payload(payload)
    if not item_id:
        return ""
    try:
        from dy_apis.douyin_api import DouyinAPI

        resp = DouyinAPI.get_work_info(
            auth,
            f"https://www.douyin.com/video/{item_id}",
        )
        detail = (resp or {}).get("aweme_detail") or {}
        if not isinstance(detail, dict):
            return ""
        video = detail.get("video") or {}
        if isinstance(video, dict):
            url = _first_http_url_from_container(video.get("play_addr"))
            if url:
                return url
            url = _first_http_url_from_container(video.get("download_addr"))
            if url:
                return url
    except Exception as exc:
        logger.debug("按 aweme_id 解析抖音视频播放地址失败 item_id=%s: %s", item_id, exc)
    return ""


def extract_douyin_image_url_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    resource = payload.get("resource_url") or {}
    if not isinstance(resource, dict):
        return ""
    for key in (
        "origin_url_list",
        "large_url_list",
        "medium_url_list",
        "thumb_url_list",
        "url_list",
    ):
        values = resource.get(key) or []
        if not isinstance(values, list):
            continue
        for item in values:
            url = str(item or "").strip()
            if url.startswith(("http://", "https://")):
                return url
    return ""


def cache_inline_image_base64(
    inline_pic: str,
    *,
    db_path: str = "",
    account_code: str = "",
    preferred_name: str = "",
) -> str:
    encoded = str(inline_pic or "").strip()
    if not encoded or not db_path:
        return ""
    try:
        payload = base64.b64decode(encoded)
    except Exception as exc:
        logger.debug("解码 Douyin inline_pic 失败: %s", exc)
        return ""
    if not payload:
        return ""
    signature = hashlib.sha256(payload).hexdigest()
    ext = _guess_image_ext_from_bytes(payload)
    return _write_binary_cache(
        payload,
        db_path=db_path,
        account_code=account_code,
        cache_key=preferred_name or signature[:24],
        suffix=ext,
    )


def ensure_douyin_image_cached(
    image_url: str,
    *,
    db_path: str = "",
    account_code: str = "",
    timeout: int = 20,
) -> str:
    url = extract_douyin_image_url("", image_url)
    if not url or not db_path:
        return ""

    try:
        base_dir = _cache_dir(db_path, account_code)
    except OSError as exc:
        logger.debug("创建 Douyin 图片缓存目录失败: %s", exc)
        return ""

    cache_key = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:24]
    existing = _find_existing_cache_file(base_dir, cache_key)
    if existing:
        return existing

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.douyin.com/",
    }

    try:
        response = requests.get(
            url,
            timeout=max(5, int(timeout or 20)),
            verify=False,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.content or b""
        if not payload:
            return ""
        suffix = _guess_suffix_from_response(
            url,
            content_type=str(response.headers.get("Content-Type") or ""),
        )
        return _write_binary_cache(
            payload,
            db_path=db_path,
            account_code=account_code,
            cache_key=cache_key,
            suffix=suffix,
        )
    except Exception as exc:
        logger.debug("Douyin 图片缓存失败 url=%s error=%s", url[:80], exc)
        return ""


def ensure_douyin_video_cached(
    video_url: str,
    *,
    db_path: str = "",
    account_code: str = "",
    timeout: int = 60,
    preferred_name: str = "",
) -> str:
    url = str(video_url or "").strip().strip("\"'")
    if not url.startswith(("http://", "https://")) or not db_path:
        return ""

    try:
        base_dir = _video_cache_dir(db_path, account_code)
    except OSError as exc:
        logger.debug("创建 Douyin 视频缓存目录失败: %s", exc)
        return ""

    cache_key = _safe_segment(preferred_name) if preferred_name else hashlib.sha256(
        url.encode("utf-8", errors="ignore")
    ).hexdigest()[:24]
    existing = _find_existing_cache_file(base_dir, cache_key)
    if existing:
        return existing

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Referer": "https://www.douyin.com/",
    }

    try:
        response = requests.get(
            url,
            timeout=max(10, int(timeout or 60)),
            verify=False,
            headers=headers,
            stream=True,
        )
        response.raise_for_status()
        payload = response.content or b""
        if not payload:
            return ""
        suffix = _guess_video_suffix_from_response(
            url,
            content_type=str(response.headers.get("Content-Type") or ""),
        )
        return _write_binary_cache_to_dir(
            payload,
            base_dir=base_dir,
            cache_key=cache_key,
            suffix=suffix,
        )
    except Exception as exc:
        logger.debug("Douyin 视频缓存失败 url=%s error=%s", url[:80], exc)
        return ""


def _safe_segment(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._") or "default"


def _find_existing_cache_file(base_dir: Path, cache_key: str) -> str:
    try:
        for candidate in sorted(base_dir.glob(f"{cache_key}.*")):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return str(candidate.resolve())
    except OSError:
        return ""
    return ""


def _cache_dir(db_path: str, account_code: str) -> Path:
    base_dir = (
        Path(db_path).expanduser().resolve().parent
        / "message_images"
        / _safe_segment(account_code or "default")
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _video_cache_dir(db_path: str, account_code: str) -> Path:
    base_dir = (
        Path(db_path).expanduser().resolve().parent
        / "message_videos"
        / _safe_segment(account_code or "default")
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _write_binary_cache(
    payload: bytes,
    *,
    db_path: str,
    account_code: str,
    cache_key: str,
    suffix: str,
) -> str:
    if not payload:
        return ""
    base_dir = _cache_dir(db_path, account_code)
    return _write_binary_cache_to_dir(
        payload,
        base_dir=base_dir,
        cache_key=cache_key,
        suffix=suffix,
    )


def _write_binary_cache_to_dir(
    payload: bytes,
    *,
    base_dir: Path,
    cache_key: str,
    suffix: str,
) -> str:
    if not payload:
        return ""
    target = base_dir / f"{_safe_segment(cache_key)}{suffix}"
    if target.is_file() and target.stat().st_size > 0:
        return str(target.resolve())
    tmp = base_dir / f"{_safe_segment(cache_key)}.tmp"
    tmp.write_bytes(payload)
    tmp.replace(target)
    return str(target.resolve())


def _guess_suffix_from_response(url: str, *, content_type: str = "") -> str:
    guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip().lower())
    if guessed and guessed != ".bin":
        return guessed
    return _guess_suffix_from_url(url)


def _guess_suffix_from_url(url: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix and len(suffix) <= 8 and suffix != ".bin":
        return suffix
    return ".jpg"


def _guess_video_suffix_from_response(url: str, *, content_type: str = "") -> str:
    guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip().lower())
    if guessed in {".mp4", ".webm", ".mov", ".m4v", ".mkv"}:
        return guessed
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".mp4", ".webm", ".mov", ".m4v", ".mkv"}:
        return suffix
    return ".mp4"


def _guess_image_ext_from_bytes(data: bytes) -> str:
    header = bytes(data[:16])
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return ".gif"
    if header.startswith(b"RIFF") and b"WEBP" in bytes(data[:16]):
        return ".webp"
    return ".bin"
