class AccountStatus:
    IDLE = "idle"
    COLLECTING = "collecting"
    CREDENTIALS_READY = "credentials_ready"
    STARTING = "starting"
    READY_FOR_NEXT = "ready_for_next"
    FULLY_ACTIVE = "fully_active"
    NEED_REFRESH = "need_refresh"
    ERROR = "error"


COLLECT_SCRIPT_ACTIVE = "active"

RUNTIME_STATUSES = {
    AccountStatus.IDLE,
    AccountStatus.COLLECTING,
    AccountStatus.CREDENTIALS_READY,
    AccountStatus.STARTING,
    AccountStatus.READY_FOR_NEXT,
    AccountStatus.FULLY_ACTIVE,
    AccountStatus.NEED_REFRESH,
    AccountStatus.ERROR,
}

COLLECT_DONE_STATUSES = {COLLECT_SCRIPT_ACTIVE, AccountStatus.CREDENTIALS_READY}
BLOCKING_STATUSES = {AccountStatus.NEED_REFRESH, AccountStatus.ERROR}
TERMINAL_STATUSES = {
    AccountStatus.FULLY_ACTIVE,
    AccountStatus.NEED_REFRESH,
    AccountStatus.ERROR,
}

LAUNCH_CONFIRM_SECONDS = 3.0
READY_FOR_NEXT_STABLE_SECONDS = 12.0
FULLY_ACTIVE_STABLE_SECONDS = 60.0
STATUS_POLL_INTERVAL_SECONDS = 1.0
CREDENTIALS_READY_TIMEOUT = 90.0
READY_FOR_NEXT_TIMEOUT = 300.0
FULLY_ACTIVE_CHECK_INTERVAL_SECONDS = 2.0

FIELD_ACCOUNT_CODE = "account_code"
FIELD_PROFILE_DIR = "profile_dir"
FIELD_COOKIES_STR = "cookies_str"
FIELD_KEYS_STR = "keys_str"
FIELD_WEB_PROTECT_STR = "web_protect_str"
FIELD_DOUYIN_UID = "douyin_uid"
FIELD_STATUS = "status"
FIELD_LAST_ERROR = "last_error"
FIELD_LAST_CAPTURED_AT = "last_captured_at"
FIELD_LAST_CHECK_AT = "last_check_at"
FIELD_CREATED_AT = "created_at"
FIELD_UPDATED_AT = "updated_at"

STATUS_LABELS = {
    AccountStatus.IDLE: "待采集",
    AccountStatus.COLLECTING: "采集中...",
    AccountStatus.CREDENTIALS_READY: "凭据已就绪",
    AccountStatus.STARTING: "启动中...",
    AccountStatus.READY_FOR_NEXT: "可启动下一个",
    AccountStatus.FULLY_ACTIVE: "稳定运行",
    AccountStatus.NEED_REFRESH: "需重新采集",
    AccountStatus.ERROR: "错误",
}
