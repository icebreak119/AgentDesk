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
RUNTIME_DOCS_URL = "http://127.0.0.1:8765/docs"
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
        if http_ok(RUNTIME_DOCS_URL):
            print("runtime server ready at", RUNTIME_DOCS_URL)
            return proc
        time.sleep(0.5)
    proc.terminate()
    print("runtime server failed to start")
    return None


def capture_runtime_docs(out_path: Path) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed, skip /docs capture")
        return False

    if not http_ok(RUNTIME_DOCS_URL):
        print("runtime /docs not reachable")
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(RUNTIME_DOCS_URL, wait_until="networkidle", timeout=20000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(out_path), full_page=False)
            browser.close()
        return out_path.is_file() and out_path.stat().st_size > 30_000
    except Exception as exc:
        print("docs capture failed:", exc)
        return False


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logs = resolve_log_files()
    if not logs:
        print("warning: no log files under", LOG_DIR)

    server_proc = try_start_runtime_server()
    docs_out = OUT / "01_账号接入页.png"
    if capture_runtime_docs(docs_out):
        print("captured runtime /docs ->", docs_out.name)
    else:
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

    render_log_png(
        "02 抖音私信入站链路",
        "WebSocket 入站 + unreplied_scan + profile 隔离（account_code）",
        pick_lines(
            logs,
            [
                "douyin_recv_server",
                "WebSocket connection open",
                "messaging_ready",
                "unreplied_scan 已入队",
                "dy_apis.douyin_recv_msg",
            ],
            limit=12,
        ),
        OUT / "02_会话工作台.png",
    )

    if ARCH.is_file():
        shutil.copy2(ARCH, OUT / "03_架构图或日志总览.png")
    else:
        render_log_png(
            "03 AgentDesk 架构总览",
            "AgentTeams 分层 + 抖音 Channel Runtime",
            pick_lines(logs, ["managed_controller", "douyin_ipc", "douyin_reverse_ipc"], limit=10),
            OUT / "03_架构图或日志总览.png",
        )

    render_log_png(
        "04 待回复 / AI 管线分流",
        "入站消息 → pending_reply → 主进程 AI 管线（高风险可人工介入）",
        pick_lines(
            logs,
            [
                "platform_pending_reply_dbg",
                "IM reply engine",
                "文本消息跳过子进程自动回复",
                "由主进程 AI 管线处理",
                "unreplied_scan 已入队",
            ],
            limit=12,
        ),
        OUT / "04_高风险审批任务.png",
    )

    render_log_png(
        "05 发送失败 / 核验告警",
        "OutcomeVerify 相关：DOM 超时、IPC 发送失败、profile 解析告警",
        pick_lines(
            logs,
            [
                "im_browser_conv_reader",
                "result=failed",
                "IPC 发送文本失败",
                "发送抖音私信失败",
                "preview_account_id",
                "未能将 profile_id",
                "verify_failed",
            ],
            limit=12,
        ),
        OUT / "05_核验失败任务.png",
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
