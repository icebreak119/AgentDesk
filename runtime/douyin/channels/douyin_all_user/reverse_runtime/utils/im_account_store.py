import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from utils.im_account_contract import RUNTIME_STATUSES


class IMAccountStoreError(Exception):
    pass


class IMAccountNotFoundError(IMAccountStoreError):
    pass


class InvalidIMAccountCredentials(ValueError):
    pass


@dataclass(frozen=True)
class IMAccount:
    account_code: str
    profile_dir: str
    cookies_str: str
    keys_str: str
    web_protect_str: str
    douyin_uid: str
    status: str
    last_error: str
    last_captured_at: str
    last_check_at: str
    enabled: int


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect(db_path):
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def load_im_accounts_from_db(db_path, account_code=None, enabled_only=True):
    clauses = []
    params = []
    if account_code:
        clauses.append("account_code = ?")
        params.append(account_code)
    if enabled_only:
        clauses.append("enabled = 1")

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    sql = f"""
        SELECT
            account_code,
            profile_dir,
            cookies_str,
            keys_str,
            web_protect_str,
            douyin_uid,
            status,
            last_error,
            last_captured_at,
            last_check_at,
            enabled
        FROM im_accounts
        {where_sql}
        ORDER BY account_code
    """

    conn = _connect(db_path)
    try:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table: im_accounts" not in str(exc).lower():
                raise
            rows = []
    finally:
        conn.close()

    return [
        IMAccount(
            account_code=row["account_code"],
            profile_dir=row["profile_dir"],
            cookies_str=row["cookies_str"],
            keys_str=row["keys_str"],
            web_protect_str=row["web_protect_str"],
            douyin_uid=row["douyin_uid"],
            status=row["status"],
            last_error=row["last_error"],
            last_captured_at=row["last_captured_at"],
            last_check_at=row["last_check_at"],
            enabled=int(row["enabled"]),
        )
        for row in rows
    ]


def update_im_account_status(db_path, account_code, status, last_error="", douyin_uid=None):
    if status not in RUNTIME_STATUSES:
        raise ValueError(f"unsupported im account status: {status}")

    now_str = _now_str()
    assignments = [
        "status = ?",
        "last_error = ?",
        "last_check_at = ?",
        "updated_at = ?",
    ]
    params = [status, last_error or "", now_str, now_str]
    if douyin_uid is not None:
        assignments.append("douyin_uid = ?")
        params.append(str(douyin_uid))
    params.append(account_code)

    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            f"""
            UPDATE im_accounts
            SET {", ".join(assignments)}
            WHERE account_code = ?
            """,
            params,
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise IMAccountNotFoundError(f"im account not found: {account_code}")
    finally:
        conn.close()


def _load_secret_payload(raw_value, required_fields, label):
    if not raw_value:
        raise InvalidIMAccountCredentials(f"{label} is empty")

    try:
        outer = json.loads(raw_value)
        inner_raw = outer["data"]
        inner = json.loads(inner_raw) if isinstance(inner_raw, str) else inner_raw
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InvalidIMAccountCredentials(f"{label} is not a valid secret payload") from exc

    if not isinstance(inner, dict):
        raise InvalidIMAccountCredentials(f"{label} data is not an object")

    missing = [field for field in required_fields if not inner.get(field)]
    if missing:
        raise InvalidIMAccountCredentials(f"{label} missing fields: {', '.join(missing)}")
    return inner


def validate_im_credentials_bundle(cookies_str, keys_str, web_protect_str):
    if not cookies_str:
        raise InvalidIMAccountCredentials("cookies_str is empty")
    if "sessionid=" not in cookies_str:
        raise InvalidIMAccountCredentials("cookies_str missing sessionid")
    if "s_v_web_id=" not in cookies_str:
        raise InvalidIMAccountCredentials("cookies_str missing s_v_web_id")
    _load_secret_payload(keys_str, ["ec_privateKey"], "keys_str")
    _load_secret_payload(web_protect_str, ["ticket", "ts_sign", "client_cert"], "web_protect_str")


def validate_im_account_credentials(account):
    validate_im_credentials_bundle(
        account.cookies_str,
        account.keys_str,
        account.web_protect_str,
    )
