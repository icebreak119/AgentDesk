"""短时开浏览器读抖音私信页活跃 conv_id 列表。失败即回退，永不阻塞。"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False


def _norm_pdir(pdir: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(pdir))
    except Exception:
        return pdir or ""


def _find_chromium_pids_for_pdir(pdir_norm: str) -> Set[int]:
    """按命令行包含该 profile_dir 找 chrome/chromium 进程 PID。"""
    if not _HAS_PSUTIL or not pdir_norm:
        return set()
    pids: Set[int] = set()
    try:
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                if "chrom" not in name:
                    continue
                cmdline = p.info.get("cmdline") or []
                joined = " ".join(cmdline)
                try:
                    joined_norm = os.path.normcase(joined)
                except Exception:
                    joined_norm = joined
                if pdir_norm in joined_norm:
                    pids.add(int(p.info["pid"]))
            except Exception:
                continue
    except Exception:
        pass
    return pids


def _kill_pids(pids: Set[int]) -> int:
    """kill 指定 PID 及其子进程树。返回实际 kill 数。"""
    if not _HAS_PSUTIL or not pids:
        return 0
    killed = 0
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            try:
                proc.kill()
                killed += 1
            except Exception:
                pass
        except Exception:
            continue
    return killed


def _prepare_thread_loop_for_playwright() -> None:
    """Windows 下 Qt/微信/云朵 server 会把进程 policy 设成 SelectorEventLoop，
    Playwright sync_api 走 subprocess 会 NotImplementedError。此处切回 Proactor。
    """
    if sys.platform != "win32":
        return
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception as exc:
        logger.warning("[browser_conv_reader] set WindowsProactorEventLoopPolicy 失败: %s", exc)
        return
    try:
        old = asyncio.get_event_loop()
        if old is not None and not old.is_closed():
            try:
                old.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception as exc:
        logger.warning("[browser_conv_reader] set Proactor loop 失败: %s", exc)

DOUYIN_CHAT_URL = "https://www.douyin.com/chat?isPopup=1"
_WAIT_JS = (
    "() => window.conversationStore "
    "&& window.conversationStore.conversationMap "
    "&& window.conversationStore.conversationMap.size > 0"
)
_READ_JS = """
() => {
  const out = { active: [], stranger: [] };
  try {
    if (window.conversationStore && window.conversationStore.conversationMap) {
      out.active = Array.from(window.conversationStore.conversationMap.keys()).map(String);
    }
  } catch (e) {}
  try {
    if (window.conversationStore && window.conversationStore.strangerConversationMap) {
      out.stranger = Array.from(window.conversationStore.strangerConversationMap.keys()).map(String);
    }
  } catch (e) {}
  return out;
}
"""


def read_active_conversation_ids(
    account_code: str,
    profile_dir: str,
    *,
    include_stranger: bool = True,
    timeout_ms: int = 25000,
    headless: bool = True,
) -> Optional[List[str]]:
    """从抖音私信页浏览器读活跃 conv_id 列表。任何失败返回 None。"""
    code = str(account_code or "").strip()
    pdir = str(profile_dir or "").strip()
    if not code or not pdir:
        logger.warning("[browser_conv_reader] account=%s 参数缺失: profile_dir=%s", code, pdir)
        return None

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        logger.warning("[browser_conv_reader] account=%s playwright 未安装: %s", code, exc)
        return None

    _prepare_thread_loop_for_playwright()

    pdir_norm = _norm_pdir(pdir)
    baseline_pids = _find_chromium_pids_for_pdir(pdir_norm)

    t0 = time.monotonic()
    result: Optional[List[str]] = None
    reason = ""
    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=pdir,
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                # 复用 launch 自带的初始页，避免多开一个 tab
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(DOUYIN_CHAT_URL, timeout=timeout_ms)
                page.wait_for_function(_WAIT_JS, timeout=timeout_ms)
                data = page.evaluate(_READ_JS) or {}
                active = list(data.get("active") or [])
                stranger = list(data.get("stranger") or []) if include_stranger else []
                combined = [x for x in active if x] + [x for x in stranger if x]
                # 去重保序
                seen = set()
                deduped = []
                for cid in combined:
                    if cid in seen:
                        continue
                    seen.add(cid)
                    deduped.append(cid)
                result = deduped
                reason = "ok"
            finally:
                # 显式关 page + context，不再依赖 with 隐式清理
                for pg in list(context.pages):
                    try:
                        pg.close()
                    except Exception:
                        pass
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        reason = f"failed:{type(exc).__name__}"
        logger.warning("[browser_conv_reader] account=%s 读取失败: %s", code, exc)
        result = None

    # 兜底：等待 chromium 子进程自然退出，超时则 kill 掉带该 pdir 的孤儿
    killed = 0
    if _HAS_PSUTIL:
        deadline = time.monotonic() + 3.0
        remaining: Set[int] = set()
        while True:
            current = _find_chromium_pids_for_pdir(pdir_norm)
            remaining = current - baseline_pids
            if not remaining or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        if remaining:
            killed = _kill_pids(remaining)
            logger.warning(
                "[browser_conv_reader] account=%s chromium 未自动退出，兜底 kill: pids=%s killed=%s",
                code, sorted(remaining), killed,
            )

    elapsed_ms = int((time.monotonic() - t0) * 1000.0)
    logger.info(
        "[browser_conv_reader] account=%s result=%s count=%s elapsed_ms=%s orphan_killed=%s",
        code,
        reason,
        len(result) if result is not None else -1,
        elapsed_ms,
        killed,
    )
    return result
