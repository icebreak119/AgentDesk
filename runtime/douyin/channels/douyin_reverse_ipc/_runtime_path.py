"""Ensure reverse_runtime is importable as ``utils`` / ``dy_apis``."""

from __future__ import annotations

import sys
from pathlib import Path

from channels.douyin_all_user.reverse_runtime_utils_preload import preload_reverse_runtime_utils

_RUNTIME_ROOT = (Path(__file__).resolve().parent.parent / "douyin_all_user" / "reverse_runtime").resolve()


def ensure_reverse_runtime_on_path() -> Path:
    root = str(_RUNTIME_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    # 主工程 utils 已在 sys.modules 时，子模块仍需注入（开发态与 frozen 均适用）
    if "utils" in sys.modules:
        preload_reverse_runtime_utils(_RUNTIME_ROOT)
    return _RUNTIME_ROOT
