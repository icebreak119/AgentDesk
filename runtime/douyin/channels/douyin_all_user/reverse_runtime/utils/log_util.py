# -*- coding: utf-8 -*-
"""抖音 runtime 最小统一日志模块。

复用项目 logging_utils，为抖音 runtime 提供统一的日志输出方式。

路径结构：
  仓库根/utils/logging_utils.py
  仓库根/channels/douyin_all_user/reverse_runtime/utils/log_util.py
"""

import importlib.util
import logging
import os
import sys
from pathlib import Path

# 延迟导入，避免循环依赖
_ensured = False


def _resolve_project_root() -> str:
    """源码：仓库根；打包子进程：exe 目录（优先 ``YUNDUO_CLIENT_ROOT``）。"""
    env_root = os.environ.get("YUNDUO_CLIENT_ROOT", "").strip()
    if env_root:
        return env_root
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parents[4])


_PROJECT_ROOT = _resolve_project_root()


def _ensure_logging():
    global _ensured
    if _ensured:
        return
    _ensured = True
    try:
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", "").strip()
            if meipass:
                candidates.append(Path(meipass) / "utils" / "logging_utils.py")
        candidates.append(Path(_PROJECT_ROOT) / "utils" / "logging_utils.py")
        for path in candidates:
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location(
                "yunduo_logging_utils",
                str(path),
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.ensure_app_logging()
                return
        raise ImportError("cannot load utils.logging_utils")
    except Exception:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )


def get_logger(name: str = "dy.runtime") -> logging.Logger:
    """获取命名 logger。"""
    _ensure_logging()
    return logging.getLogger(name)


def log_info(msg: str, *args, **kwargs):
    """便捷 info 日志。"""
    get_logger().info(msg, *args, **kwargs)


def log_warning(msg: str, *args, **kwargs):
    """便捷 warning 日志。"""
    get_logger().warning(msg, *args, **kwargs)


def log_error(msg: str, *args, **kwargs):
    """便捷 error 日志。"""
    get_logger().error(msg, *args, **kwargs)


def log_debug(msg: str, *args, **kwargs):
    """便捷 debug 日志。"""
    get_logger().debug(msg, *args, **kwargs)
