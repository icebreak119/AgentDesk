"""Standalone knowledge prompt fallback for Douyin IM auto replies."""

from __future__ import annotations

import os

DEFAULT_BASE_PROMPT = (
    "你是抖音私信客服助手。请直接回复客户最后一条消息，使用简洁自然的中文，"
    "不要使用 Markdown，不要编造未给出的价格、时效或承诺。"
)


def build_dy_im_system_prompt(*, base_prompt: str = "", max_reply_chars: int | None = None) -> str:
    prompt = (base_prompt or os.getenv("DY_IM_LLM_SYSTEM_PROMPT") or DEFAULT_BASE_PROMPT).strip()
    if not prompt:
        prompt = DEFAULT_BASE_PROMPT
    if max_reply_chars is None:
        try:
            max_reply_chars = int(os.getenv("DY_IM_MAX_REPLY_CHARS", "80") or 80)
        except ValueError:
            max_reply_chars = 80
    if max_reply_chars and "字以内" not in prompt:
        prompt = f"{prompt}控制在{max_reply_chars}字以内。"
    return prompt


def inject_knowledge_into_dy_im_env(env: dict[str, str]) -> dict[str, str]:
    if env.get("DY_IM_KNOWLEDGE_PROMPT_INJECTED") == "1":
        return env
    env["DY_IM_LLM_SYSTEM_PROMPT"] = build_dy_im_system_prompt(
        base_prompt=str(env.get("DY_IM_LLM_SYSTEM_PROMPT") or "")
    )
    env["DY_IM_KNOWLEDGE_PROMPT_INJECTED"] = "1"
    return env
