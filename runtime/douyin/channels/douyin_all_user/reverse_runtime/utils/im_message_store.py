import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_ui_event_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS douyin_ui_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_profile_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            inbound INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_douyin_ui_events_id ON douyin_ui_events(id)"
    )


def touch_runtime_activity(db_path, account_code) -> None:
    """记录账号最近一次实时消息落库时间（供未回复扫描让路）。"""
    account = str(account_code or "").strip()
    path = str(db_path or "").strip()
    if not account or not path:
        return
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS im_runtime_activity (
                    account_code TEXT PRIMARY KEY,
                    last_touch_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                INSERT INTO im_runtime_activity(account_code, last_touch_at)
                VALUES (?, ?)
                ON CONFLICT(account_code) DO UPDATE SET last_touch_at=excluded.last_touch_at
                """,
                (account, _now_str()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def seconds_since_runtime_activity(db_path, account_code) -> float:
    """距上次实时落库的秒数；无记录视为足够空闲。"""
    account = str(account_code or "").strip()
    path = str(db_path or "").strip()
    if not account or not path:
        return 1e9
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS im_runtime_activity (
                    account_code TEXT PRIMARY KEY,
                    last_touch_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            row = conn.execute(
                "SELECT last_touch_at FROM im_runtime_activity WHERE account_code = ? LIMIT 1",
                (account,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return 1e9
    if row is None:
        return 1e9
    raw = str(row[0] or "").strip()
    if not raw:
        return 1e9
    try:
        touched = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return max(0.0, (datetime.now() - touched).total_seconds())
    except Exception:
        return 1e9


def _enqueue_ui_event(
    db_path,
    account_code,
    conversation_id,
    *,
    inbound=True,
):
    account = str(account_code or "").strip()
    conv = str(conversation_id or "").strip()
    if not account or not conv:
        return
    try:
        from core.douyin_aggregate_notify import notify_douyin_conversation_message_changed

        notify_douyin_conversation_message_changed(account, conv, inbound=inbound)
    except Exception:
        pass


def _parse_created_at_ts(created_at: str) -> float:
    s = str(created_at or "").strip()
    if not s:
        return 0.0
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _find_recent_duplicate_outbound_msg_id(
    conn,
    account_code: str,
    conversation_id: str,
    text: str,
    *,
    window_seconds: int = 180,
) -> str:
    """发送脚本落库与 WebSocket 回显会各写一条 outbound，按内容+会话去重。"""
    now_ts = time.time()
    rows = conn.execute(
        """
        SELECT msg_id, created_at
        FROM messages
        WHERE account_profile_id = ?
          AND IFNULL(conversation_id, '') = ?
          AND direction = 'outbound'
          AND IFNULL(content, '') = ?
        ORDER BY id DESC
        LIMIT 8
        """,
        (account_code, conversation_id, text),
    ).fetchall()
    for row in rows:
        created_at = str(row["created_at"] or "")
        ts = _parse_created_at_ts(created_at)
        if not ts or (now_ts - ts) <= window_seconds:
            return str(row["msg_id"] or "")
    return ""


def _recent_outbound_has_ai_reply(
    conn,
    account_code: str,
    conversation_id: str,
    text: str,
    *,
    window_seconds: int = 300,
) -> bool:
    now_ts = time.time()
    rows = conn.execute(
        """
        SELECT created_at, is_ai_reply
        FROM messages
        WHERE account_profile_id = ?
          AND IFNULL(conversation_id, '') = ?
          AND direction = 'outbound'
          AND IFNULL(content, '') = ?
        ORDER BY id DESC
        LIMIT 8
        """,
        (account_code, conversation_id, text),
    ).fetchall()
    for row in rows:
        try:
            if int(row["is_ai_reply"] or 0) <= 0:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        created_at = str(row["created_at"] or "")
        ts = _parse_created_at_ts(created_at)
        if not ts or (now_ts - ts) <= window_seconds:
            return True
    return False


def _connect(db_path):
    conn = sqlite3.connect(str(Path(db_path).expanduser().resolve()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn, table_name):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _ensure_column(conn, table_name, column_name, column_sql):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def ensure_message_tables(db_path):
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT DEFAULT '',
                account_profile_id TEXT DEFAULT '',
                msg_id TEXT NOT NULL,
                conversation_id TEXT DEFAULT '',
                from_user_id TEXT DEFAULT '',
                to_user_id TEXT DEFAULT '',
                direction TEXT DEFAULT '',
                msg_type TEXT DEFAULT 'text',
                content TEXT DEFAULT '',
                media_url TEXT DEFAULT '',
                media_local_path TEXT DEFAULT '',
                ai_reply TEXT DEFAULT '',
                status TEXT DEFAULT '',
                read_status TEXT DEFAULT 'unread',
                error_msg TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                replied_at TEXT DEFAULT '',
                UNIQUE(account_profile_id, msg_id)
            )
            """
        )
        _ensure_column(conn, "messages", "profile_id", "TEXT DEFAULT ''")
        _ensure_column(conn, "messages", "account_profile_id", "TEXT DEFAULT ''")
        _ensure_column(conn, "messages", "conversation_id", "TEXT DEFAULT ''")
        _ensure_column(conn, "messages", "media_url", "TEXT DEFAULT ''")
        _ensure_column(conn, "messages", "media_local_path", "TEXT DEFAULT ''")
        _ensure_column(conn, "messages", "media_video_url", "TEXT DEFAULT ''")
        _ensure_column(conn, "messages", "media_video_local_path", "TEXT DEFAULT ''")
        _ensure_column(conn, "messages", "read_status", "TEXT DEFAULT 'unread'")
        _ensure_column(conn, "messages", "is_ai_reply", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_account_profile
            ON messages(account_profile_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(account_profile_id, conversation_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT DEFAULT '',
                conv_key TEXT NOT NULL,
                conversation_id TEXT DEFAULT '',
                conversation_short_id TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                avatar_local_path TEXT DEFAULT '',
                is_self INTEGER DEFAULT 0,
                source TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(conv_key, is_self)
            )
            """
        )
        _ensure_column(conn, "conversation_profiles", "profile_id", "TEXT DEFAULT ''")
        _ensure_column(conn, "conversation_profiles", "conversation_short_id", "TEXT DEFAULT ''")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_profiles_profile
            ON conversation_profiles(profile_id, conversation_id, is_self)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_profiles_display_name
            ON conversation_profiles(profile_id, display_name, is_self)
            """
        )
        conn.commit()
    finally:
        conn.close()


def _safe_name(value):
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._") or "unknown"


def _looks_like_uid(value):
    text = str(value or "").strip()
    return bool(text) and text.isdigit()


def _choose_profile_display_name(current_name, incoming_name, incoming_source="", conversation_id=""):
    current = str(current_name or "").strip()
    incoming = str(incoming_name or "").strip()
    conversation = str(conversation_id or "").strip()
    if not incoming:
        return current
    if not current or current == incoming:
        return incoming
    current_is_uid = _looks_like_uid(current) or (conversation and current == conversation)
    incoming_is_uid = _looks_like_uid(incoming) or (conversation and incoming == conversation)
    if incoming_source == "reverse_runtime_profile" and current_is_uid and not incoming_is_uid:
        return incoming
    if current_is_uid and not incoming_is_uid:
        return incoming
    if not current_is_uid and incoming_is_uid:
        return current
    return current


def _conversation_conv_key(account_code, conversation_id, display_name):
    account = _safe_name(account_code)
    conversation_id = str(conversation_id or "").strip()
    if conversation_id:
        return f"acct:{account}:cid:{conversation_id}"
    return f"acct:{account}:name:{_safe_name(display_name or account_code)}"


def _find_existing_profile(db_path, account_code, conversation_id):
    conv_key = _conversation_conv_key(account_code, conversation_id, "")
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT display_name FROM conversation_profiles WHERE conv_key = ? AND is_self = 0 LIMIT 1",
                (conv_key,),
            ).fetchone()
            return str(row[0] or "").strip() if row else ""
        finally:
            conn.close()
    except Exception:
        return ""


def upsert_self_profile(
    db_path,
    account_code,
    display_name="",
    user_id="",
    avatar_url="",
    avatar_local_path="",
):
    ensure_message_tables(db_path)
    now_str = _now_str()
    display = str(display_name or "").strip() or str(user_id or "").strip() or str(account_code or "").strip()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO conversation_profiles (
                profile_id, conv_key, conversation_id, display_name,
                avatar_url, avatar_local_path, is_self, source, updated_at
            ) VALUES (?, ?, '', ?, ?, ?, 1, 'reverse_runtime', ?)
            ON CONFLICT(conv_key, is_self) DO UPDATE SET
                profile_id=excluded.profile_id,
                display_name=CASE
                    WHEN excluded.display_name != '' THEN excluded.display_name
                    ELSE conversation_profiles.display_name
                END,
                avatar_url=CASE
                    WHEN excluded.avatar_url != '' THEN excluded.avatar_url
                    ELSE conversation_profiles.avatar_url
                END,
                avatar_local_path=CASE
                    WHEN excluded.avatar_local_path != '' THEN excluded.avatar_local_path
                    ELSE conversation_profiles.avatar_local_path
                END,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                account_code,
                f"self:{account_code}",
                display,
                str(avatar_url or "").strip(),
                str(avatar_local_path or "").strip(),
                now_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_conversation_profile(
    db_path,
    account_code,
    conversation_id="",
    display_name="",
    source="reverse_runtime",
    avatar_url="",
    avatar_local_path="",
    conversation_short_id="",
):
    ensure_message_tables(db_path)
    name = str(display_name or "").strip() or str(conversation_id or "").strip()
    conv_key = _conversation_conv_key(account_code, conversation_id, name)
    now_str = _now_str()
    conn = _connect(db_path)
    try:
        existing_row = conn.execute(
            """
            SELECT display_name, source
            FROM conversation_profiles
            WHERE conv_key = ? AND is_self = 0
            LIMIT 1
            """,
            (conv_key,),
        ).fetchone()
        existing_name = str(existing_row["display_name"] or "").strip() if existing_row is not None else ""
        existing_source = str(existing_row["source"] or "").strip() if existing_row is not None else ""
        resolved_name = _choose_profile_display_name(
            existing_name,
            name,
            incoming_source=source,
            conversation_id=conversation_id,
        )
        resolved_source = str(source or "").strip()
        if resolved_name == existing_name and existing_source and resolved_source != "reverse_runtime_profile":
            resolved_source = existing_source
        conn.execute(
            """
            INSERT INTO conversation_profiles (
                profile_id, conv_key, conversation_id, conversation_short_id, display_name,
                avatar_url, avatar_local_path, is_self, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(conv_key, is_self) DO UPDATE SET
                profile_id=excluded.profile_id,
                conversation_id=CASE
                    WHEN excluded.conversation_id != '' THEN excluded.conversation_id
                    ELSE conversation_profiles.conversation_id
                END,
                conversation_short_id=CASE
                    WHEN excluded.conversation_short_id != '' THEN excluded.conversation_short_id
                    ELSE conversation_profiles.conversation_short_id
                END,
                display_name=CASE
                    WHEN excluded.display_name != '' THEN excluded.display_name
                    ELSE conversation_profiles.display_name
                END,
                avatar_url=CASE
                    WHEN excluded.avatar_url != '' THEN excluded.avatar_url
                    ELSE conversation_profiles.avatar_url
                END,
                avatar_local_path=CASE
                    WHEN excluded.avatar_local_path != '' THEN excluded.avatar_local_path
                    ELSE conversation_profiles.avatar_local_path
                END,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                account_code,
                conv_key,
                str(conversation_id or "").strip(),
                str(conversation_short_id or "").strip(),
                resolved_name,
                str(avatar_url or "").strip(),
                str(avatar_local_path or "").strip(),
                resolved_source,
                now_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _message_id(account_code, direction_key, conversation_id, unique_token):
    return f"{account_code}\x1e{direction_key}\x1e{conversation_id}\x1e{unique_token}"


def _find_existing_rows_by_msg_ids(conn, account_code: str, *msg_ids: str):
    normalized = []
    for raw in msg_ids:
        value = str(raw or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    if not account_code or not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    return conn.execute(
        f"""
        SELECT id, msg_id, direction, created_at, replied_at
        FROM messages
        WHERE account_profile_id = ?
          AND msg_id IN ({placeholders})
        ORDER BY id ASC
        """,
        (account_code, *normalized),
    ).fetchall()


def save_inbound_message(
    db_path,
    account_code,
    conversation_id,
    sender_id,
    content,
    *,
    msg_type="text",
    unique_token="",
    peer_user_id="",
    display_name="",
    avatar_url="",
    avatar_local_path="",
    media_url="",
    media_local_path="",
    media_video_url="",
    media_video_local_path="",
    read_status="unread",
    known_self_uids=None,
    allow_content_window_dedupe=True,
    created_at="",
    touch_realtime_activity=True,
    skip_ui_notify=False,
):
    """落库一条入站消息（来自对端）。

    DB 方向语义：
      inbound:  from_user_id = peer uid,  to_user_id = '我' (self)
      outbound: from_user_id = self uid,  to_user_id = peer uid

    防御性纠偏：如果调用方误把 self uid 当成 sender_id 传入
    （首次历史回补的旧 bug 路径），自动改走 save_outbound_message，
    避免把自己的消息落成 inbound。``allow_content_window_dedupe``
    会透传给 save_outbound_message，历史回补场景应传 False。
    """
    ensure_message_tables(db_path)
    conversation_id = str(conversation_id or "").strip()
    sender_id = str(sender_id or "").strip()
    text = str(content or "").strip()
    if not account_code or not conversation_id or not sender_id or not text:
        return ""

    # 防御性 self uid 检测：若 sender 命中已知 self uid，绝不允许落成 inbound
    self_uid_set = set()
    if known_self_uids:
        for raw in known_self_uids:
            uid = str(raw or "").strip()
            if uid:
                self_uid_set.add(uid)
    if sender_id in self_uid_set:
        # 自动纠正为 outbound，direction 不能被错置
        resolved_peer = str(peer_user_id or "").strip()
        if not resolved_peer or resolved_peer == sender_id:
            # 从 conversation_id 末两段里挑出非 self 的对端
            parts = conversation_id.split(":")
            if len(parts) >= 4:
                for participant in (parts[-2], parts[-1]):
                    participant = str(participant or "").strip()
                    if participant and participant != sender_id:
                        resolved_peer = participant
                        break
        return save_outbound_message(
            db_path,
            account_code,
            conversation_id,
            resolved_peer or peer_user_id or "",
            text,
            msg_type=msg_type,
            sender_id=sender_id,
            unique_token=unique_token,
            media_url=media_url,
            media_local_path=media_local_path,
            media_video_url=media_video_url,
            media_video_local_path=media_video_local_path,
            allow_content_window_dedupe=allow_content_window_dedupe,
            created_at=created_at,
            touch_realtime_activity=touch_realtime_activity,
            skip_ui_notify=skip_ui_notify,
        )

    token = str(unique_token or "").strip() or str(int(time.time() * 1000))
    msg_id = _message_id(account_code, "network", conversation_id, token)
    now_str = _now_str()
    created_at_str = str(created_at or "").strip() or now_str
    peer = str(peer_user_id or sender_id or "").strip()
    existing = _find_existing_profile(db_path, account_code, conversation_id)
    profile_display = str(display_name or "").strip()
    if not existing and not profile_display:
        profile_display = peer
    upsert_conversation_profile(
        db_path,
        account_code,
        conversation_id,
        profile_display,
        avatar_url=avatar_url,
        avatar_local_path=avatar_local_path,
    )

    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO messages (
                profile_id, account_profile_id, msg_id, conversation_id,
                from_user_id, to_user_id, direction, msg_type, content, media_url, media_local_path,
                media_video_url, media_video_local_path,
                ai_reply, status, read_status, error_msg, created_at, replied_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'inbound', ?, ?, ?, ?, ?, ?, '', 'sent', ?, '', ?, '')
            ON CONFLICT(account_profile_id, msg_id) DO UPDATE SET
                conversation_id=excluded.conversation_id,
                from_user_id=excluded.from_user_id,
                to_user_id=excluded.to_user_id,
                direction=excluded.direction,
                msg_type=excluded.msg_type,
                content=excluded.content,
                media_url=CASE
                    WHEN excluded.media_url != '' THEN excluded.media_url
                    ELSE messages.media_url
                END,
                media_local_path=CASE
                    WHEN excluded.media_local_path != '' THEN excluded.media_local_path
                    ELSE messages.media_local_path
                END,
                media_video_url=CASE
                    WHEN excluded.media_video_url != '' THEN excluded.media_video_url
                    ELSE messages.media_video_url
                END,
                media_video_local_path=CASE
                    WHEN excluded.media_video_local_path != '' THEN excluded.media_video_local_path
                    ELSE messages.media_video_local_path
                END,
                status=excluded.status,
                read_status=excluded.read_status,
                created_at=excluded.created_at
            """,
            (
                account_code,
                account_code,
                msg_id,
                conversation_id,
                sender_id,
                "我",
                str(msg_type or "text"),
                text,
                str(media_url or "").strip(),
                str(media_local_path or "").strip(),
                str(media_video_url or "").strip(),
                str(media_video_local_path or "").strip(),
                str(read_status or "unread"),
                created_at_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    if touch_realtime_activity:
        touch_runtime_activity(db_path, account_code)
    if not skip_ui_notify:
        _enqueue_ui_event(
            db_path,
            account_code,
            conversation_id,
            inbound=True,
        )
    return msg_id


def load_peer_display_name(db_path, account_code, conversation_id):
    """读取会话对方展示名（供 AI ``customerName``）。"""
    return _find_existing_profile(db_path, account_code, conversation_id)


def load_private_ai_message_list(
    db_path,
    account_code,
    conversation_id,
    *,
    max_messages=10,
):
    """从 SQLite 加载会话文本历史，供 ``/chat/completion/private`` 的 ``messageList`` 使用。"""
    account = str(account_code or "").strip()
    conv = str(conversation_id or "").strip()
    path = str(db_path or "").strip()
    if not account or not conv or not path:
        return []

    limit = max(1, int(max_messages))
    conn = _connect(path)
    try:
        rows = conn.execute(
            """
            SELECT direction, content
            FROM messages
            WHERE account_profile_id = ?
              AND conversation_id = ?
              AND IFNULL(content, '') != ''
              AND IFNULL(msg_type, 'text') = 'text'
            ORDER BY id DESC
            LIMIT ?
            """,
            (account, conv, limit),
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in reversed(rows):
        text = str(row["content"] or "").strip()
        if not text:
            continue
        direction = str(row["direction"] or "").strip().lower()
        role = "assistant" if direction in {"outbound", "out"} else "user"
        items.append({"role": role, "msg": text})
    return items


def save_outbound_message(
    db_path,
    account_code,
    conversation_id,
    peer_user_id,
    content,
    *,
    msg_type="text",
    sender_id="我",
    status="sent",
    error_msg="",
    unique_token="",
    media_url="",
    media_local_path="",
    media_video_url="",
    media_video_local_path="",
    allow_content_window_dedupe=True,
    created_at="",
    replied_at="",
    touch_realtime_activity=True,
    is_ai_reply=False,
    skip_ui_notify=False,
):
    """落库一条出站消息（自己发送）。

    DB 方向语义：outbound: from_user_id = self uid, to_user_id = peer uid。

    ``allow_content_window_dedupe``：
      - True（默认）：保留"发送脚本落库 + WebSocket 回显"的内容+时间窗去重，
        仅用于本地刚发出的即时消息补偿。
      - False：历史回补场景必须传 False，避免把多条真实历史 outbound
        （同会话、同文案、回补瞬间 created_at 接近）误判成重复而吞掉。
        历史回补的去重只允许依赖真正稳定的消息身份
        （unique_token / server_message_id / index_in_conversation / msg_id）。
    """
    ensure_message_tables(db_path)
    conversation_id = str(conversation_id or "").strip()
    peer_user_id = str(peer_user_id or "").strip()
    text = str(content or "").strip()
    if not account_code or not conversation_id or not peer_user_id or not text:
        return ""

    token = str(unique_token or "").strip() or str(int(time.time() * 1000))
    msg_id = _message_id(account_code, "outbound", conversation_id, token)
    now_str = _now_str()
    created_at_str = str(created_at or "").strip() or now_str
    replied_at_str = str(replied_at or "").strip()
    if not replied_at_str and str(status or "sent") == "sent":
        replied_at_str = created_at_str
    ai_reply_flag = 1 if bool(is_ai_reply) else 0
    upsert_conversation_profile(db_path, account_code, conversation_id, peer_user_id)

    conn = _connect(db_path)
    try:
        if not ai_reply_flag:
            try:
                if _recent_outbound_has_ai_reply(
                    conn,
                    account_code,
                    conversation_id,
                    text,
                ):
                    ai_reply_flag = 1
            except Exception:
                pass
        legacy_msg_id = _message_id(account_code, "network", conversation_id, token)
        existing_rows = _find_existing_rows_by_msg_ids(
            conn,
            account_code,
            msg_id,
            legacy_msg_id,
        )
        if existing_rows:
            keeper = next(
                (row for row in existing_rows if str(row["msg_id"] or "").strip() == msg_id),
                existing_rows[0],
            )
            keeper_id = int(keeper["id"] or 0)
            conn.execute(
                """
                UPDATE messages
                SET msg_id = ?,
                    conversation_id = ?,
                    from_user_id = ?,
                    to_user_id = ?,
                    direction = 'outbound',
                    msg_type = ?,
                    content = ?,
                    media_url = CASE
                        WHEN ? != '' THEN ?
                        ELSE media_url
                    END,
                    media_local_path = CASE
                        WHEN ? != '' THEN ?
                        ELSE media_local_path
                    END,
                    media_video_url = CASE
                        WHEN ? != '' THEN ?
                        ELSE media_video_url
                    END,
                    media_video_local_path = CASE
                        WHEN ? != '' THEN ?
                        ELSE media_video_local_path
                    END,
                    status = ?,
                    read_status = 'read',
                    error_msg = ?,
                    created_at = ?,
                    replied_at = CASE
                        WHEN ? != '' THEN ?
                        WHEN ? = 'sent' AND IFNULL(replied_at, '') = '' THEN ?
                        ELSE replied_at
                    END,
                    is_ai_reply = CASE
                        WHEN ? > 0 THEN 1
                        ELSE is_ai_reply
                    END
                WHERE id = ?
                """,
                (
                    msg_id,
                    conversation_id,
                    str(sender_id or "我").strip() or "我",
                    peer_user_id,
                    str(msg_type or "text"),
                    text,
                    str(media_url or "").strip(),
                    str(media_url or "").strip(),
                    str(media_local_path or "").strip(),
                    str(media_local_path or "").strip(),
                    str(media_video_url or "").strip(),
                    str(media_video_url or "").strip(),
                    str(media_video_local_path or "").strip(),
                    str(media_video_local_path or "").strip(),
                    str(status or "sent"),
                    str(error_msg or ""),
                    created_at_str,
                    replied_at_str,
                    replied_at_str,
                    str(status or "sent"),
                    created_at_str,
                    ai_reply_flag,
                    keeper_id,
                ),
            )
            duplicate_ids = [
                int(row["id"] or 0)
                for row in existing_rows
                if int(row["id"] or 0) and int(row["id"] or 0) != keeper_id
            ]
            if duplicate_ids:
                placeholders = ",".join("?" for _ in duplicate_ids)
                conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    duplicate_ids,
                )
            conn.commit()
            return msg_id

        # 内容+时间窗去重仅用于"本地刚发出的即时消息补偿"，
        # 历史回补必须跳过，否则多条真实 outbound（同文案、回补瞬间 created_at 接近）会被吞掉
        if allow_content_window_dedupe:
            existing_msg_id = _find_recent_duplicate_outbound_msg_id(
                conn, account_code, conversation_id, text
            )
            if existing_msg_id:
                if ai_reply_flag > 0:
                    conn.execute(
                        """
                        UPDATE messages
                        SET is_ai_reply = 1
                        WHERE account_profile_id = ?
                          AND msg_id = ?
                          AND IFNULL(is_ai_reply, 0) = 0
                        """,
                        (account_code, existing_msg_id),
                    )
                    conn.commit()
                return existing_msg_id

        conn.execute(
            """
            INSERT INTO messages (
                profile_id, account_profile_id, msg_id, conversation_id,
                from_user_id, to_user_id, direction, msg_type, content, media_url, media_local_path,
                media_video_url, media_video_local_path,
                ai_reply, status, read_status, error_msg, created_at, replied_at, is_ai_reply
            ) VALUES (?, ?, ?, ?, ?, ?, 'outbound', ?, ?, ?, ?, ?, ?, '', ?, 'read', ?, ?, ?, ?)
            ON CONFLICT(account_profile_id, msg_id) DO UPDATE SET
                conversation_id=excluded.conversation_id,
                from_user_id=excluded.from_user_id,
                to_user_id=excluded.to_user_id,
                msg_type=excluded.msg_type,
                content=excluded.content,
                media_url=CASE
                    WHEN excluded.media_url != '' THEN excluded.media_url
                    ELSE messages.media_url
                END,
                media_local_path=CASE
                    WHEN excluded.media_local_path != '' THEN excluded.media_local_path
                    ELSE messages.media_local_path
                END,
                media_video_url=CASE
                    WHEN excluded.media_video_url != '' THEN excluded.media_video_url
                    ELSE messages.media_video_url
                END,
                media_video_local_path=CASE
                    WHEN excluded.media_video_local_path != '' THEN excluded.media_video_local_path
                    ELSE messages.media_video_local_path
                END,
                status=excluded.status,
                error_msg=excluded.error_msg,
                created_at=excluded.created_at,
                replied_at=excluded.replied_at,
                is_ai_reply=CASE
                    WHEN excluded.is_ai_reply > 0 THEN 1
                    ELSE messages.is_ai_reply
                END
            """,
            (
                account_code,
                account_code,
                msg_id,
                conversation_id,
                str(sender_id or "我").strip() or "我",
                peer_user_id,
                str(msg_type or "text"),
                text,
                str(media_url or "").strip(),
                str(media_local_path or "").strip(),
                str(media_video_url or "").strip(),
                str(media_video_local_path or "").strip(),
                str(status or "sent"),
                str(error_msg or ""),
                created_at_str,
                replied_at_str,
                ai_reply_flag,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    if touch_realtime_activity:
        touch_runtime_activity(db_path, account_code)
    if not skip_ui_notify:
        _enqueue_ui_event(
            db_path,
            account_code,
            conversation_id,
            inbound=False,
        )
    return msg_id
