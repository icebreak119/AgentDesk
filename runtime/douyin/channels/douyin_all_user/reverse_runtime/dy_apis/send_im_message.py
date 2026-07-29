import argparse
import logging
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from dy_apis.douyin_api import DouyinAPI
from utils.common_util import build_im_auth_from_credentials, load_project_env
from utils.im_account_store import load_im_accounts_from_db, validate_im_account_credentials
from utils.im_message_store import ensure_message_tables, save_outbound_message, upsert_conversation_profile
from utils.im_send_result import summarize_send_response, validate_send_response
from utils.log_util import get_logger

logger = get_logger("dy.send_message")


def _default_db_path():
    return Path(__file__).resolve().parent.parent / "_douyin_im_accounts.db"


def _parse_args():
    parser = argparse.ArgumentParser(description="Send one Douyin IM message from managed SQLite credentials.")
    parser.add_argument("--db-path", default="", help="SQLite database path. Defaults to project/_douyin_im_accounts.db")
    parser.add_argument("--account-code", required=True, help="Managed account_code in SQLite.")
    parser.add_argument("--sender-id", required=True, help="Target user id from the inbound conversation.")
    parser.add_argument("--conversation-id", default="", help="Conversation id used for local chat history grouping.")
    parser.add_argument("--text", required=True, help="Message text to send.")
    return parser.parse_args()


def _load_managed_account(db_path, account_code):
    accounts = load_im_accounts_from_db(db_path, account_code=account_code, enabled_only=True)
    if not accounts:
        raise RuntimeError(f"no enabled im account found in SQLite: {account_code}")
    if len(accounts) != 1:
        raise RuntimeError("managed send expects exactly one account_code")
    return accounts[0]


def _check_send_response(result):
    logger.info("send response: %s", summarize_send_response(result))
    validate_send_response(result)


def main():
    args = _parse_args()
    text = str(args.text or "").strip()
    sender_id = str(args.sender_id or "").strip()
    if not text:
        raise SystemExit("text is required")
    if not sender_id.isdigit():
        raise SystemExit("sender-id must be numeric")

    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else _default_db_path()
    conversation_id = str(args.conversation_id or "").strip()

    load_project_env()
    account = _load_managed_account(db_path, args.account_code)
    validate_im_account_credentials(account)
    auth = build_im_auth_from_credentials(
        account.cookies_str,
        account.web_protect_str,
        account.keys_str,
    )

    ensure_message_tables(db_path)
    upsert_conversation_profile(db_path, account.account_code, conversation_id, sender_id)

    conversation_id_real, conversation_short_id, ticket = DouyinAPI.resolve_or_create_conversation(
        auth,
        int(sender_id),
        conversation_id=conversation_id,
    )
    result = DouyinAPI.send_msg(auth, conversation_id_real, conversation_short_id, ticket, text)
    _check_send_response(result)
    save_outbound_message(
        db_path,
        account.account_code,
        conversation_id or conversation_id_real,
        sender_id,
        text,
        status="sent",
    )
    logger.info("[%s] send success to %s", account.account_code, sender_id)


if __name__ == "__main__":
    main()
