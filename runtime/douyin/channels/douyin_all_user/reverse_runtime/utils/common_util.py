import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("dy.common_util")

dy_auth = None
dy_live_auth = None
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_env():
    """加载项目环境变量文件。

    优先级：.env.local > .env > 系统环境变量
    """
    env_file = PROJECT_ROOT / ".env"
    env_local_file = PROJECT_ROOT / ".env.local"

    if env_file.is_file():
        load_dotenv(env_file)
        logger.debug("已加载环境变量: %s", env_file)
    if env_local_file.is_file():
        load_dotenv(env_local_file, override=True)
        logger.debug("已加载本地环境变量: %s", env_local_file)


def _load_im_values():
    load_project_env()
    values = {
        "cookies_str": os.getenv("DY_IM_COOKIES", "").strip(),
        "web_protect_str": os.getenv("DY_IM_WEB_PROTECT", "").strip(),
        "keys_str": os.getenv("DY_IM_KEYS", "").strip(),
    }
    source = ".env"

    secret_path = os.getenv("DY_IM_SECRET_FILE", "").strip()
    candidates = []
    if secret_path:
        candidates.append(Path(secret_path).expanduser())
    candidates.append(Path(__file__).resolve().parent.parent / "_douyin_secrets.json")

    if not all(values.values()):
        for candidate in candidates:
            if not candidate.is_file():
                continue
            with candidate.open("r", encoding="utf-8") as fp:
                secret_data = json.load(fp)
            values["cookies_str"] = values["cookies_str"] or secret_data.get("cookies_str", "").strip()
            values["web_protect_str"] = values["web_protect_str"] or secret_data.get("web_protect_str", "").strip()
            values["keys_str"] = values["keys_str"] or secret_data.get("keys_str", "").strip()
            source = str(candidate)
            break

    return values, source


def load_env():
    global dy_auth, dy_live_auth
    load_project_env()
    cookies_dy = os.getenv('DY_COOKIES')
    cookies_live = os.getenv('DY_LIVE_COOKIES')
    from builder.auth import DouyinAuth
    dy_auth = DouyinAuth()
    dy_auth.perepare_auth(cookies_dy, "", "")
    dy_live_auth = DouyinAuth()
    dy_live_auth.perepare_auth(cookies_live, "", "")
    logger.info("已加载抖音认证配置")
    return dy_auth


def build_im_auth_from_credentials(cookies_str, web_protect_str, keys_str):
    from builder.auth import DouyinAuth
    im_auth = DouyinAuth()
    im_auth.perepare_auth(
        cookies_str,
        web_protect_str,
        keys_str,
    )
    return im_auth


def load_im_auth():
    values, source = _load_im_values()
    if not values["cookies_str"]:
        raise ValueError("missing DY_IM_COOKIES and _douyin_secrets.json")

    im_auth = build_im_auth_from_credentials(
        values["cookies_str"],
        values["web_protect_str"],
        values["keys_str"],
    )
    logger.info("已加载 IM 认证配置，来源: %s", source)
    return im_auth, source


def load_im_accounts_from_db(db_path, account_code=None, enabled_only=True):
    from utils.im_account_store import load_im_accounts_from_db as _load_im_accounts_from_db
    return _load_im_accounts_from_db(db_path, account_code=account_code, enabled_only=enabled_only)

def init():
    media_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../datas/media_datas'))
    excel_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../datas/excel_datas'))
    for base_path in [media_base_path, excel_base_path]:
        if not os.path.exists(base_path):
            os.makedirs(base_path)
            # logger.info(f'create {base_path}')
    cookies = load_env()
    base_path = {
        'media': media_base_path,
        'excel': excel_base_path,
    }
    return cookies, base_path
