# -*- coding: utf-8 -*-
"""Douyin IM 运行时配置常量与辅助函数。

集中管理 DY_IM_* 环境变量的默认值，消除 _base_env() 与 ReplyConfig.from_env() 之间的重复定义。

优先级（从高到低）：
1. 调用方显式传入的值
2. 系统环境变量
3. 此处定义的默认值
"""

from __future__ import annotations

import os
from typing import Dict

# ──────────────────────────────────────────────────────────────
# DY_IM_* 默认值常量
# ──────────────────────────────────────────────────────────────

# 回复模式：ai | fixed | off
DEFAULT_REPLY_MODE = "ai"

# 固定回复文本（当 mode=fallback 或 LLM 不可用时使用）
DEFAULT_FALLBACK_TEXT = "你好"

# 兼容旧配置名
DEFAULT_AUTO_REPLY_TEXT = "你好"

# LLM 配置：默认走项目内置预览服 AI；LLM_ 仅作为兜底，默认留空由 env 覆盖。
DEFAULT_USE_PREVIEW_SERVICE = "true"
DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_API_KEY = ""
DEFAULT_LLM_MODEL = "gpt-4o-mini"

# 数值型配置（ReplyConfig.from_env() 中有独立的最小值钳制逻辑）
DEFAULT_MAX_REPLY_CHARS = "80"
DEFAULT_QUEUE_SIZE = "200"
DEFAULT_WORKER_COUNT = "2"
DEFAULT_SEEN_CACHE_SIZE = "2000"
DEFAULT_LLM_TIMEOUT = "8"
DEFAULT_LLM_MAX_TOKENS = "128"
DEFAULT_LLM_TEMPERATURE = "0.4"

# 性能与容量治理（P2）
DEFAULT_MAX_ACTIVE_BOTS = "30"
DEFAULT_API_FALLBACK_ENABLED = "true"
DEFAULT_COLLECT_HEADLESS = "true"


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def api_fallback_on_disconnect_allowed() -> bool:
    """断线时是否允许 API fallback 轮询（连接正常时不应启用）。"""
    return _env_truthy("DY_API_FALLBACK_ENABLED", default=True)


def max_active_bots() -> int:
    raw = str(os.environ.get("DY_MAX_ACTIVE_BOTS") or DEFAULT_MAX_ACTIVE_BOTS).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return int(DEFAULT_MAX_ACTIVE_BOTS)


def collect_headless_default() -> bool:
    return _env_truthy("DY_COLLECT_HEADLESS", default=True)


# ──────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────

def apply_dy_im_env_defaults(env: Dict[str, str], extra: Dict[str, str] | None = None) -> Dict[str, str]:
    """为子进程环境注入 DY_IM_* 默认值。

    Args:
        env: 基础环境变量字典（通常来自 os.environ.copy()）
        extra: 额外覆盖值（优先级最高）

    Returns:
        注入默认值后的环境变量字典（原地修改并返回）
    """
    if extra:
        env.update(extra)

    # 回复模式
    env.setdefault("DY_IM_REPLY_MODE", DEFAULT_REPLY_MODE)
    env.setdefault("DY_IM_FALLBACK_TEXT", DEFAULT_FALLBACK_TEXT)
    env.setdefault("DY_IM_AUTO_REPLY_TEXT", DEFAULT_AUTO_REPLY_TEXT)

    # LLM 配置
    env.setdefault("DY_IM_USE_PREVIEW_SERVICE", DEFAULT_USE_PREVIEW_SERVICE)
    env.setdefault("DY_IM_LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
    env.setdefault("DY_IM_LLM_API_KEY", DEFAULT_LLM_API_KEY)
    env.setdefault("DY_IM_LLM_MODEL", DEFAULT_LLM_MODEL)

    env.setdefault("DY_MAX_ACTIVE_BOTS", DEFAULT_MAX_ACTIVE_BOTS)
    env.setdefault("DY_API_FALLBACK_ENABLED", DEFAULT_API_FALLBACK_ENABLED)
    env.setdefault("DY_COLLECT_HEADLESS", DEFAULT_COLLECT_HEADLESS)

    return env


def get_default(env_var: str, fallback: str = "") -> str:
    """获取 DY_IM_* 环境变量的默认值。

    用于 ReplyConfig.from_env() 等场景，避免硬编码默认值。
    """
    defaults = {
        "DY_IM_REPLY_MODE": DEFAULT_REPLY_MODE,
        "DY_IM_FALLBACK_TEXT": DEFAULT_FALLBACK_TEXT,
        "DY_IM_AUTO_REPLY_TEXT": DEFAULT_AUTO_REPLY_TEXT,
        "DY_IM_USE_PREVIEW_SERVICE": DEFAULT_USE_PREVIEW_SERVICE,
        "DY_IM_LLM_BASE_URL": DEFAULT_LLM_BASE_URL,
        "DY_IM_LLM_API_KEY": DEFAULT_LLM_API_KEY,
        "DY_IM_LLM_MODEL": DEFAULT_LLM_MODEL,
        "DY_IM_MAX_REPLY_CHARS": DEFAULT_MAX_REPLY_CHARS,
        "DY_IM_QUEUE_SIZE": DEFAULT_QUEUE_SIZE,
        "DY_IM_WORKER_COUNT": DEFAULT_WORKER_COUNT,
        "DY_IM_SEEN_CACHE_SIZE": DEFAULT_SEEN_CACHE_SIZE,
        "DY_IM_LLM_TIMEOUT": DEFAULT_LLM_TIMEOUT,
        "DY_IM_LLM_MAX_TOKENS": DEFAULT_LLM_MAX_TOKENS,
        "DY_IM_LLM_TEMPERATURE": DEFAULT_LLM_TEMPERATURE,
    }
    return defaults.get(env_var, fallback)
