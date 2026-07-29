import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from builder.auth import DouyinAuth
from utils.im_account_contract import AccountStatus
from utils.log_util import get_logger

logger = get_logger("dy.collect_credentials")

HOME_URL = "https://www.douyin.com/"
KEYS_STORAGE_KEY = "security-sdk/s_sdk_crypt_sdk"
WEB_PROTECT_STORAGE_KEY = "security-sdk/s_sdk_sign_data_key/web_protect"
DEFAULT_DB_NAME = "_douyin_im_accounts.db"
STATUS_COLLECTING = AccountStatus.COLLECTING
STATUS_CREDENTIALS_READY = AccountStatus.CREDENTIALS_READY
STATUS_NEED_REFRESH = AccountStatus.NEED_REFRESH
STATUS_ERROR = AccountStatus.ERROR


class CredentialCollectionIncomplete(RuntimeError):
    pass


class CredentialValidationError(ValueError):
    pass


@dataclass
class CapturedCredentials:
    cookies_str: str
    keys_str: str
    web_protect_str: str
    douyin_uid: str


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sanitize_account_code(account_code):
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", account_code).strip("._")
    return sanitized or "account"


def _build_profile_dir(project_root, account_code, profile_dir):
    if profile_dir:
        return Path(profile_dir).expanduser().resolve()
    return (project_root / "profiles" / _sanitize_account_code(account_code)).resolve()


def _build_db_path(project_root, db_path):
    if db_path:
        return Path(db_path).expanduser().resolve()
    return (project_root / DEFAULT_DB_NAME).resolve()


def _load_secret_payload(raw_value, required_fields, label):
    if not raw_value:
        raise CredentialValidationError(f"{label} is empty")
    try:
        outer = json.loads(raw_value)
        if not isinstance(outer, dict):
            raise ValueError("outer value is not an object")
        inner = json.loads(outer.get("data", ""))
    except (TypeError, ValueError) as exc:
        raise CredentialValidationError(f"{label} is not valid JSON") from exc
    if not isinstance(inner, dict):
        raise CredentialValidationError(f"{label} data is not an object")
    missing = [field for field in required_fields if not inner.get(field)]
    if missing:
        raise CredentialValidationError(f"{label} missing fields: {', '.join(missing)}")
    return inner


def _validate_credentials_blob(cookies_str, keys_str, web_protect_str):
    if not cookies_str:
        raise CredentialValidationError("cookies_str is empty")
    if "sessionid=" not in cookies_str:
        raise CredentialValidationError("cookies_str missing sessionid")
    if "s_v_web_id=" not in cookies_str:
        raise CredentialValidationError("cookies_str missing s_v_web_id")
    _load_secret_payload(keys_str, ["ec_privateKey"], "keys_str")
    _load_secret_payload(web_protect_str, ["ticket", "ts_sign", "client_cert"], "web_protect_str")


def _open_db(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn, table_name):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _ensure_column(conn, table_name, column_name, column_sql):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def _ensure_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS im_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_code TEXT NOT NULL UNIQUE,
            nickname TEXT NOT NULL DEFAULT '',
            douyin_uid TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'idle',
            profile_dir TEXT NOT NULL,
            cookies_str TEXT NOT NULL DEFAULT '',
            keys_str TEXT NOT NULL DEFAULT '',
            web_protect_str TEXT NOT NULL DEFAULT '',
            last_captured_at TEXT NOT NULL DEFAULT '',
            last_check_at TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS im_account_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_code TEXT NOT NULL,
            profile_dir TEXT NOT NULL DEFAULT '',
            douyin_uid TEXT NOT NULL DEFAULT '',
            cookies_str TEXT NOT NULL,
            keys_str TEXT NOT NULL,
            web_protect_str TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'credentials_ready',
            is_valid INTEGER NOT NULL DEFAULT 1,
            captured_at TEXT NOT NULL,
            last_captured_at TEXT NOT NULL DEFAULT '',
            last_check_at TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(account_code) REFERENCES im_accounts(account_code)
        )
        """
    )
    _ensure_column(conn, "im_accounts", "profile_dir", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "cookies_str", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "keys_str", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "web_protect_str", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "douyin_uid", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "status", "TEXT NOT NULL DEFAULT 'idle'")
    _ensure_column(conn, "im_accounts", "last_error", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "last_captured_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "last_check_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "created_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_accounts", "updated_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_account_credentials", "profile_dir", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_account_credentials", "douyin_uid", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_account_credentials", "status", "TEXT NOT NULL DEFAULT 'credentials_ready'")
    _ensure_column(conn, "im_account_credentials", "last_captured_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_account_credentials", "last_check_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_account_credentials", "last_error", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_account_credentials", "created_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "im_account_credentials", "updated_at", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_im_account_credentials_account_code
        ON im_account_credentials(account_code, captured_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_im_account_credentials_account_code_last_captured
        ON im_account_credentials(account_code, last_captured_at DESC)
        """
    )
    conn.commit()


def _upsert_account_status(conn, account_code, profile_dir, status, error_message=""):
    now_str = _now_str()
    conn.execute(
        """
        INSERT INTO im_accounts (
            account_code, profile_dir, status, last_check_at, last_error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_code) DO UPDATE SET
            profile_dir=excluded.profile_dir,
            status=excluded.status,
            last_check_at=excluded.last_check_at,
            last_error=excluded.last_error,
            updated_at=excluded.updated_at
        """,
        (account_code, str(profile_dir), status, now_str, error_message, now_str, now_str),
    )
    conn.commit()


def _save_credentials(conn, account_code, profile_dir, credentials):
    _validate_credentials_blob(credentials.cookies_str, credentials.keys_str, credentials.web_protect_str)
    now_str = _now_str()
    with conn:
        conn.execute(
            """
            INSERT INTO im_accounts (
                account_code, douyin_uid, status, profile_dir, cookies_str, keys_str,
                web_protect_str, last_captured_at, last_check_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
            ON CONFLICT(account_code) DO UPDATE SET
                douyin_uid=excluded.douyin_uid,
                status=excluded.status,
                profile_dir=excluded.profile_dir,
                cookies_str=excluded.cookies_str,
                keys_str=excluded.keys_str,
                web_protect_str=excluded.web_protect_str,
                last_captured_at=excluded.last_captured_at,
                last_check_at=excluded.last_check_at,
                last_error='',
                updated_at=excluded.updated_at
            """,
            (
                account_code,
                credentials.douyin_uid,
                STATUS_CREDENTIALS_READY,
                str(profile_dir),
                credentials.cookies_str,
                credentials.keys_str,
                credentials.web_protect_str,
                now_str,
                now_str,
                now_str,
                now_str,
            ),
        )
        conn.execute(
            """
            UPDATE im_account_credentials
            SET is_valid=0, updated_at=?
            WHERE account_code=? AND is_valid=1
            """,
            (now_str, account_code),
        )
        conn.execute(
            """
            INSERT INTO im_account_credentials (
                account_code, profile_dir, douyin_uid, cookies_str, keys_str, web_protect_str,
                status, is_valid, captured_at, last_captured_at, last_check_at, last_error,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, '', ?, ?)
            """,
            (
                account_code,
                str(profile_dir),
                credentials.douyin_uid,
                credentials.cookies_str,
                credentials.keys_str,
                credentials.web_protect_str,
                STATUS_CREDENTIALS_READY,
                now_str,
                now_str,
                now_str,
                now_str,
                now_str,
            ),
        )


def _cookie_dict_to_str(cookie_dict):
    return "; ".join(f"{name}={value}" for name, value in cookie_dict.items())


async def _ensure_home_page(context):
    if context.pages:
        page = context.pages[0]
    else:
        page = await context.new_page()
    if not page.url.startswith("https://www.douyin.com"):
        await page.goto(HOME_URL, wait_until="domcontentloaded")
    return page


async def _read_local_storage_value(page, key):
    if not page.url.startswith("https://www.douyin.com"):
        return ""
    try:
        return await page.evaluate(
            "(storageKey) => window.localStorage.getItem(storageKey) || ''",
            key,
        )
    except Exception:
        return ""


async def _read_secret_values(context):
    keys_str = ""
    web_protect_str = ""
    for page in context.pages:
        if not keys_str:
            keys_str = await _read_local_storage_value(page, KEYS_STORAGE_KEY)
        if not web_protect_str:
            web_protect_str = await _read_local_storage_value(page, WEB_PROTECT_STORAGE_KEY)
        if keys_str and web_protect_str:
            break
    return keys_str, web_protect_str


async def _collect_cookie_bundle(context):
    cookies = await context.cookies()
    cookie_dict = {}
    for cookie in cookies:
        if "douyin.com" not in cookie.get("domain", ""):
            continue
        cookie_dict[cookie["name"]] = cookie["value"]
    return cookie_dict, _cookie_dict_to_str(cookie_dict)


def _build_auth(cookies_str, web_protect_str, keys_str):
    auth = DouyinAuth()
    auth.perepare_auth(cookies_str, web_protect_str, keys_str)
    return auth


async def collect_account_credentials(
    account_code,
    profile_dir,
    timeout_seconds,
    poll_interval,
    browser_channel,
    headless,
    health_check,
):
    logger.info(
        "[%s] 凭证采集协程启动: profile_dir=%s timeout=%ss poll_interval=%ss browser_channel=%s headless=%s health_check=%s",
        account_code,
        profile_dir,
        timeout_seconds,
        poll_interval,
        browser_channel or "",
        headless,
        health_check,
    )
    async with async_playwright() as playwright:
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel

        context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        try:
            await _ensure_home_page(context)
            logger.info("[%s] profile_dir: %s", account_code, profile_dir)
            logger.info("[%s] 已打开抖音页面，请在这个浏览器窗口完成登录。", account_code)
            logger.info("[%s] 如果 web_protect_str 迟迟没有采到，请手动进入私信或创作者相关页面后再等待。", account_code)

            start_time = time.time()
            last_summary = None
            last_validation_error = ""
            last_validation_error_logged = ""
            while True:
                keys_str, web_protect_str = await _read_secret_values(context)
                cookie_dict, cookies_str = await _collect_cookie_bundle(context)
                summary = (
                    bool(cookie_dict.get("sessionid")),
                    bool(cookie_dict.get("s_v_web_id")),
                    bool(keys_str),
                    bool(web_protect_str),
                )
                if summary != last_summary:
                    logger.info(
                        "[%s] sessionid=%s s_v_web_id=%s keys=%s web_protect=%s",
                        account_code, summary[0], summary[1], summary[2], summary[3]
                    )
                    last_summary = summary

                try:
                    _validate_credentials_blob(cookies_str, keys_str, web_protect_str)
                    auth = _build_auth(cookies_str, web_protect_str, keys_str)
                    douyin_uid = ""
                    if health_check:
                        raw_uid = auth.get_uid()
                        if not raw_uid:
                            raise CredentialValidationError("auth.get_uid() returned an empty uid")
                        douyin_uid = str(raw_uid)
                        logger.info("[%s] 健康检查通过，uid=%s", account_code, douyin_uid)
                    logger.info(
                        "[%s] 凭证采集校验通过: elapsed=%ss summary=sessionid=%s,s_v_web_id=%s,keys=%s,web_protect=%s",
                        account_code,
                        round(time.time() - start_time, 2),
                        summary[0],
                        summary[1],
                        summary[2],
                        summary[3],
                    )
                    return CapturedCredentials(
                        cookies_str=cookies_str,
                        keys_str=keys_str,
                        web_protect_str=web_protect_str,
                        douyin_uid=douyin_uid,
                    )
                except Exception as exc:
                    last_validation_error = str(exc)
                    if last_validation_error != last_validation_error_logged:
                        logger.warning(
                            "[%s] 凭证校验未通过: error=%s summary=sessionid=%s,s_v_web_id=%s,keys=%s,web_protect=%s",
                            account_code,
                            last_validation_error,
                            summary[0],
                            summary[1],
                            summary[2],
                            summary[3],
                        )
                        last_validation_error_logged = last_validation_error

                if time.time() - start_time >= timeout_seconds:
                    detail = f" Last validation error: {last_validation_error}" if last_validation_error else ""
                    logger.warning(
                        "[%s] 凭证采集超时: elapsed=%ss summary=sessionid=%s,s_v_web_id=%s,keys=%s,web_protect=%s last_validation_error=%s",
                        account_code,
                        round(time.time() - start_time, 2),
                        summary[0],
                        summary[1],
                        summary[2],
                        summary[3],
                        last_validation_error,
                    )
                    raise CredentialCollectionIncomplete(
                        "timed out waiting for a complete credential bundle. "
                        "Please confirm the account is logged in and open a Douyin page that initializes the security SDK."
                        + detail
                    )
                await asyncio.sleep(poll_interval)
        finally:
            await context.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Collect Douyin IM credentials for one managed account.")
    parser.add_argument("--account-code", required=True, help="Stable account identifier used in SQLite.")
    parser.add_argument("--db-path", default="", help="SQLite database path. Defaults to project/_douyin_im_accounts.db")
    parser.add_argument("--profile-dir", default="", help="Browser profile directory. Defaults to project/profiles/<account_code>")
    parser.add_argument("--timeout", type=int, default=300, help="Maximum wait time in seconds. Default: 300")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Polling interval in seconds. Default: 2")
    parser.add_argument("--browser-channel", default="", help="Optional Playwright channel, e.g. chrome or msedge.")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode.")
    parser.add_argument("--skip-health-check", action="store_true", help="Skip auth.get_uid() validation before saving.")
    return parser.parse_args()


def main():
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    profile_dir = _build_profile_dir(project_root, args.account_code, args.profile_dir)
    db_path = _build_db_path(project_root, args.db_path)
    profile_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _open_db(db_path)
    started_at = time.time()
    try:
        _ensure_tables(conn)
        _upsert_account_status(conn, args.account_code, profile_dir, STATUS_COLLECTING, "")
        logger.info(
            "[%s] 凭证采集主流程开始: db_path=%s profile_dir=%s",
            args.account_code,
            db_path,
            profile_dir,
        )
        credentials = asyncio.run(
            collect_account_credentials(
                account_code=args.account_code,
                profile_dir=profile_dir,
                timeout_seconds=args.timeout,
                poll_interval=args.poll_interval,
                browser_channel=(
                    args.browser_channel.strip()
                    or os.environ.get("DY_PLAYWRIGHT_BROWSER_CHANNEL", "").strip()
                ),
                headless=args.headless,
                health_check=not args.skip_health_check,
            )
        )
        _save_credentials(conn, args.account_code, profile_dir, credentials)
        logger.info(
            "[%s] 采集成功，状态已写为 %s，SQLite: %s elapsed=%ss uid=%s",
            args.account_code,
            STATUS_CREDENTIALS_READY,
            db_path,
            round(time.time() - started_at, 2),
            credentials.douyin_uid,
        )
        return 0
    except (CredentialCollectionIncomplete, CredentialValidationError) as exc:
        _upsert_account_status(conn, args.account_code, profile_dir, STATUS_NEED_REFRESH, str(exc))
        logger.warning(
            "[%s] 采集未完成，状态已写为 %s: %s elapsed=%ss",
            args.account_code,
            STATUS_NEED_REFRESH,
            exc,
            round(time.time() - started_at, 2),
        )
        return 2
    except PlaywrightTimeoutError as exc:
        error_message = f"playwright timeout: {exc}"
        _upsert_account_status(conn, args.account_code, profile_dir, STATUS_ERROR, error_message)
        logger.error(
            "[%s] 浏览器操作超时，状态已写为 %s: %s elapsed=%ss",
            args.account_code,
            STATUS_ERROR,
            exc,
            round(time.time() - started_at, 2),
        )
        return 1
    except KeyboardInterrupt:
        _upsert_account_status(conn, args.account_code, profile_dir, STATUS_ERROR, "interrupted by user")
        logger.warning(
            "[%s] 已中断，状态已写为 %s elapsed=%ss",
            args.account_code,
            STATUS_ERROR,
            round(time.time() - started_at, 2),
        )
        return 130
    except Exception as exc:
        _upsert_account_status(conn, args.account_code, profile_dir, STATUS_ERROR, str(exc))
        logger.error(
            "[%s] 采集失败，状态已写为 %s: %s elapsed=%ss",
            args.account_code,
            STATUS_ERROR,
            exc,
            round(time.time() - started_at, 2),
        )
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
