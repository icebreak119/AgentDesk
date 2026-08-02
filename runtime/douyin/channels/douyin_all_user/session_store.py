"""Minimal profile metadata store for the standalone Douyin runtime."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_RUNTIME_ROOT = Path(__file__).resolve().parent / "reverse_runtime"


def profiles_root() -> Path:
    env = str(os.environ.get("DOUYIN_PROFILE_ROOT", "") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (_RUNTIME_ROOT / "profiles").resolve()


def profile_dir(profile_id: str) -> Path:
    return profiles_root() / str(profile_id or "").strip()


def save_profile_meta(profile_id: str, extra: Optional[Dict[str, Any]] = None) -> None:
    code = str(profile_id or "").strip()
    if not code:
        return
    meta_path = profile_dir(code) / "profile_meta.json"
    payload: Dict[str, Any] = {
        "profile_id": code,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    if meta_path.is_file():
        try:
            current = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                current.update(payload)
                payload = current
        except Exception:
            pass
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_profile_meta(profile_id: str) -> Dict[str, Any]:
    code = str(profile_id or "").strip()
    if not code:
        return {}
    meta_path = profile_dir(code) / "profile_meta.json"
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_profile_auto_reply_enabled(profile_id: str, *, default: bool = False) -> bool:
    meta = load_profile_meta(profile_id)
    if "auto_reply" not in meta:
        return bool(default)
    return bool(meta.get("auto_reply"))
