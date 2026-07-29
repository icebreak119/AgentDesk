"""Generate AgentDesk submission screenshots: Runtime /docs capture + redacted logs."""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
OUT = ROOT / "07_系统截图"
LOG_DIR = REPO_ROOT / "logs"
ARCH = ROOT / "06_架构图.png"
RUNTIME_CONSOLE_URL = "http://127.0.0.1:8765/console"
FOOTER = "来源：AgentDesk 抖音 Channel Runtime 日志（已打码）· 初赛工程证据"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Consolas", "DejaVu Sans Mono"]
plt.rcParams["axes.unicode_minus"] = False


def redact(line: str) -> str:
    line = re.sub(r"nickname=[^\s]+", "nickname=[已打码]", line)
    line = re.sub(r"douyin_uid=\d+", "douyin_uid=43****4680", line)
    line = re.sub(r"customer=\d+", "customer=75****7963", line)
    line = re.sub(r"uid=\d+", "uid=43****4680", line)
    line = re.sub(r"user_id=\d+", "user_id=43****4680", line)
    line = re.sub(
        r"account_code=([0-9a-f]{8})[0-9a-f\-]+",
        r"account_code=\1-****-****-****-************",
        line,
    )
    line = re.sub(
        r"profile_id=([0-9a-f]{8})[0-9a-f\-]+",
        r"profile_id=\1-****-****-****-************",
        line,
    )
    line = re.sub(r"profile=([0-9a-f]{8})[0-9a-f\-]+", r"profile=\1-****", line)
    line = re.sub(r"conversation_id=[^\s]+", "conversation_id=0:1:***:****", line)
    line = re.sub(r"conv=0:1:[^\s]+", "conv=0:1:***:****", line)
    line = re.sub(r"peer_id=\d+", "peer_id=75****7963", line)
    line = re.sub(r"peer=\d+", "peer=75****7963", line)
    line = re.sub(r"C:\\Users\\[^\\]+", r"C:\\Users\\[redacted]", line)
    return line


def resolve_log_files() -> list[Path]:
    names = [
        "agentdesk.log",
        "yunduo.log",
        "yunduo.log.2026-07-28",
        "yunduo.log.2026-07-29",
    ]
    found: list[Path] = []
    for name in names:
        p = LOG_DIR / name
        if p.is_file():
            found.append(p)
    return found


def pick_lines(log_paths: list[Path], patterns: list[str], limit: int = 14) -> list[str]:
    out: list[str] = []
    for log_path in log_paths:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                if any(p in raw for p in patterns):
                    out.append(redact(raw.rstrip()))
    deduped: list[str] = []
    for line in out:
        if line not in deduped:
            deduped.append(line)
    if not deduped:
        return [f"# no log lines matched: {patterns[:3]}..."]
    return deduped[:limit]


def render_log_png(title: str, subtitle: str, lines: list[str], out_path: Path) -> None:
    fig = plt.figure(figsize=(14, 8), dpi=160, facecolor="#0f172a")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#0f172a")
    ax.axis("off")

    ax.text(0.03, 0.95, title, color="#e2e8f0", fontsize=18, weight="bold", va="top")
    ax.text(0.03, 0.90, subtitle, color="#94a3b8", fontsize=11, va="top")
    ax.add_patch(Rectangle((0.02, 0.08), 0.96, 0.78, fill=False, edgecolor="#334155", linewidth=1.2))

    y = 0.82
    for line in lines:
        color = "#f8fafc"
        if "WARNING" in line or "failed" in line or "verify" in line.lower():
            color = "#fbbf24"
        if "ERROR" in line:
            color = "#f87171"
        if "important_consult" in line:
            color = "#fb923c"
        ax.text(0.035, y, line[:150], color=color, fontsize=8.2, family="Microsoft YaHei", va="top")
        y -= 0.052
        if y < 0.1:
            break

    ax.text(0.03, 0.03, FOOTER, color="#64748b", fontsize=9, va="bottom")
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def try_start_runtime_server() -> subprocess.Popen | None:
    db = REPO_ROOT / "channels" / "douyin_all_user" / "reverse_runtime" / "_douyin_im_accounts.db"
    if not db.is_file():
        print("skip runtime server: missing db", db)
        return None
    if port_open("127.0.0.1", 8765):
        return None
    cmd = [
        sys.executable,
        "-m",
        "channels.douyin_reverse_ipc.http_server",
        "--db-path",
        str(db),
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        if http_ok(RUNTIME_CONSOLE_URL):
            print("runtime server ready at", RUNTIME_CONSOLE_URL)
            return proc
        time.sleep(0.5)
    proc.terminate()
    print("runtime server failed to start")
    return None


def capture_runtime_docs(out_path: Path) -> bool:
    return capture_console_suite(OUT).get(out_path.name, False)


def capture_console_suite(out_dir: Path) -> dict[str, bool]:
    """Capture multiple real /console screenshots for submission."""
    results: dict[str, bool] = {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed, skip console capture")
        return results

    if not http_ok(RUNTIME_CONSOLE_URL):
        print("runtime /console not reachable")
        return results

    def _shot_page(filename: str, *, min_size: int = 20_000, full_page: bool = False, clip: dict | None = None) -> None:
        out_path = out_dir / filename
        try:
            page.screenshot(path=str(out_path), full_page=full_page, clip=clip)
            ok = out_path.is_file() and out_path.stat().st_size >= min_size
            results[filename] = ok
            if ok:
                print("captured console ->", filename)
            else:
                print("capture too small:", filename)
        except Exception as exc:
            print("capture failed", filename, exc)
            results[filename] = False

    def _shot(locator, filename: str, *, min_size: int = 20_000) -> None:
        path = out_dir / filename
        try:
            locator.screenshot(path=str(path))
            ok = path.is_file() and path.stat().st_size >= min_size
            results[filename] = ok
            if ok:
                print("captured console ->", filename)
            else:
                print("capture too small:", filename)
        except Exception as exc:
            print("capture failed", filename, exc)
            results[filename] = False

    def _panel_clip(*labels: str, include_hero: bool = False) -> dict[str, float] | None:
        boxes = []
        if include_hero:
            hero = page.locator("header.hero").bounding_box()
            if hero:
                boxes.append(hero)
        for label in labels:
            panel = page.locator("section.panel").filter(has_text=label).first
            if panel.count():
                box = panel.bounding_box()
                if box:
                    boxes.append(box)
        if not boxes:
            return None
        y = min(b["y"] for b in boxes)
        bottom = max(b["y"] + b["height"] for b in boxes)
        return {"x": 0.0, "y": y, "width": 1440.0, "height": min(bottom - y + 20.0, 2200.0)}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(RUNTIME_CONSOLE_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_selector(
                "#account-list .account-item, #account-list .empty",
                timeout=15000,
            )
            page.wait_for_timeout(1200)

            account_panel = page.locator("section.panel").nth(0)
            _shot(account_panel, "01_账号接入页.png")

            select_btn = page.locator('button[data-action="select"]').first
            if select_btn.count():
                select_btn.click()
                page.wait_for_timeout(400)

            reload_btn = page.locator("#btn-reload-conversations")
            if reload_btn.count():
                reload_btn.click()
            page.wait_for_selector(
                "#conversation-list .conversation-item, #conversation-list .empty",
                timeout=15000,
            )
            page.wait_for_timeout(1800)

            clip = _panel_clip("账号托管", "发送文本私信", "私信会话", include_hero=True)
            if not clip:
                hero = page.locator("header.hero").bounding_box()
                conv = page.locator("section.panel").filter(has_text="私信会话").first.bounding_box()
                if hero and conv:
                    y = max(0.0, hero["y"])
                    clip = {
                        "x": 0.0,
                        "y": y,
                        "width": 1440.0,
                        "height": min((conv["y"] + conv["height"] - y) + 24.0, 2200.0),
                    }
            if clip:
                _shot_page("02_会话工作台.png", clip=clip, min_size=35_000)
            else:
                page.screenshot(path=str(out_dir / "02_会话工作台.png"), full_page=True)
                out = out_dir / "02_会话工作台.png"
                results["02_会话工作台.png"] = out.is_file() and out.stat().st_size >= 35_000
                if results["02_会话工作台.png"]:
                    print("captured console -> 02_会话工作台.png (full page)")

            conv_item = page.locator("#conversation-list .conversation-item").first
            if conv_item.count():
                conv_item.click()
                page.wait_for_timeout(600)

            send_clip = _panel_clip("发送文本私信", "私信会话")
            if send_clip:
                _shot_page("04_高风险审批任务.png", clip=send_clip, min_size=30_000)
            else:
                send_panel = page.locator("section.panel").filter(has_text="发送文本私信")
                _shot(send_panel.first, "04_高风险审批任务.png")

            page.evaluate(
                """
                async () => {
                  const log = document.getElementById('result-log');
                  const code = document.getElementById('account-select')?.value;
                  if (!log || !code) return;
                  const stamp = new Date().toLocaleString('zh-CN', { hour12: false });
                  try {
                    const res = await fetch(`/accounts/${encodeURIComponent(code)}/refresh_profiles`, { method: 'POST' });
                    const data = await res.json();
                    log.textContent = `[${stamp}] 资料同步 / 状态核验\\n` + JSON.stringify(data, null, 2);
                  } catch (err) {
                    log.textContent = `[${stamp}] 资料同步失败\\n` + String(err?.message || err);
                  }
                }
                """
            )
            page.wait_for_timeout(1800)

            result_panel = page.locator("section.panel").filter(has_text="调用结果")
            result_panel.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            _shot(result_panel.first, "05_核验失败任务.png", min_size=15_000)

            browser.close()
    except Exception as exc:
        print("console suite capture failed:", exc)

    return results


def build_architecture() -> None:
    script = ROOT / "build_arch_diagram.py"
    if not script.is_file():
        print("warning: missing", script)
        return
    import runpy

    runpy.run_path(str(script), run_name="__main__")


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logs = resolve_log_files()
    if not logs:
        print("warning: no log files under", LOG_DIR)

    server_proc = try_start_runtime_server()
    captured = capture_console_suite(OUT)
    docs_out = OUT / "01_账号接入页.png"
    if not captured.get("01_账号接入页.png"):
        render_log_png(
            "01 AgentDesk 抖音 Runtime / 账号托管",
            "ChannelIngress + IPC：凭证就绪 → 收信启动 → messaging_ready",
            pick_lines(
                logs,
                [
                    "请求启动抖音托管账号",
                    "抖音托管登录采集开始",
                    "采集成功，状态已写为 credentials_ready",
                    "抖音 IPC 收信已启动",
                    "抖音托管账号启动成功",
                    "抖音私信 messaging_ready",
                    "douyin_reverse_ipc",
                ],
                limit=12,
            ),
            docs_out,
        )

    if not captured.get("02_会话工作台.png"):
        print(
            "ERROR: 02_会话工作台.png 未截到真实控制台。"
            "请先启动 8765 服务后重跑：python docs/goai/build_submission_screenshots.py"
        )

    build_architecture()
    if ARCH.is_file():
        shutil.copy2(ARCH, OUT / "03_架构图或日志总览.png")
        print("updated architecture -> 03_架构图或日志总览.png")
    else:
        render_log_png(
            "03 AgentDesk 架构总览",
            "AgentTeams 分层 + 抖音 Channel Runtime",
            pick_lines(logs, ["managed_controller", "douyin_ipc", "douyin_reverse_ipc"], limit=10),
            OUT / "03_架构图或日志总览.png",
        )

    if not captured.get("04_高风险审批任务.png"):
        print(
            "ERROR: 04_高风险审批任务.png 未截到真实控制台。"
            "请先启动 8765 服务后重跑：python docs/goai/build_submission_screenshots.py"
        )

    if not captured.get("05_核验失败任务.png"):
        print(
            "ERROR: 05_核验失败任务.png 未截到真实控制台。"
            "请先启动 8765 服务后重跑：python docs/goai/build_submission_screenshots.py"
        )

    if server_proc is not None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()

    for tmp in OUT.glob("_ui_*.png"):
        tmp.unlink(missing_ok=True)

    print("written:", OUT)
    for p in sorted(OUT.glob("*.png")):
        print(" ", p.name, p.stat().st_size)


if __name__ == "__main__":
    build()
