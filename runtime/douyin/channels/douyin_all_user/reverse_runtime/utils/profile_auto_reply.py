"""读取侧栏 cb_ai 同步到 profile_meta.json 的 auto_reply 开关（逆向子进程可用）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

_RUNTIME_ROOT = Path(__file__).resolve().parents[1]


def profiles_root() -> Path:
    env = str(os.environ.get("DOUYIN_PROFILE_ROOT", "") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (_RUNTIME_ROOT / "profiles").resolve()


def load_profile_meta_field(profile_id: str, field: str, default: str = "") -> str:
    code = str(profile_id or "").strip()
    if not code:
        return default
    meta_path = profiles_root() / code / "profile_meta.json"
    if not meta_path.is_file():
        return default
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(meta, dict):
            return str(meta.get(field) or default).strip() or default
    except Exception:
        pass
    return default


def load_profile_im_uid(profile_id: str) -> str:
    return load_profile_meta_field(profile_id, "im_uid", "")


def save_profile_im_uid(profile_id: str, im_uid: str) -> None:
    uid = str(im_uid or "").strip()
    code = str(profile_id or "").strip()
    if not code or not uid:
        return
    try:
        from channels.douyin_all_user import session_store

        session_store.save_profile_meta(code, {"im_uid": uid})
    except Exception:
        meta_path = profiles_root() / code / "profile_meta.json"
        try:
            meta: dict = {}
            if meta_path.is_file():
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            meta["im_uid"] = uid
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def is_profile_auto_reply_enabled(profile_id: str, *, default: bool = False) -> bool:
    code = str(profile_id or "").strip()
    if not code or code == "default":
        return bool(default)

    meta_path = profiles_root() / code / "profile_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and "auto_reply" in meta:
                return bool(meta.get("auto_reply"))
        except Exception:
            pass

    try:
        from channels.douyin_all_user import session_store

        return session_store.is_profile_auto_reply_enabled(code, default=default)
    except Exception:
        return bool(default)
