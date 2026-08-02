"""Generate redacted screenshots for the AgentDesk preliminary submission.

The console captures use the repository's real console.html with a local mock
API. This demonstrates the shipped UI without exposing a real account, chat,
credential, or workstation path. The trace captures are generated from the
same A/B/C scripts that pytest exercises.
"""

from __future__ import annotations

import html
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
OUT = ROOT / "07_系统截图"
ARCH = ROOT / "06_架构图.png"
CONSOLE_HTML = REPO_ROOT / "runtime" / "douyin" / "channels" / "douyin_reverse_ipc" / "static" / "console.html"

SCREENSHOTS = (
    "01_托管账号登录控制面.png",
    "02_抖音渠道控制台_脱敏演示.png",
    "03_多Agent架构图.png",
    "04_审批闭环Trace.png",
    "05_执行核验Trace.png",
    "06_跨渠道去重与案例复用Trace.png",
)
LEGACY_SCREENSHOTS = (
    "01_账号接入页.png",
    "02_会话工作台.png",
    "03_架构图或日志总览.png",
    "04_高风险审批任务.png",
    "05_核验失败任务.png",
)

ACCOUNT_CODE = "demo-shop-001"
ACCOUNT = {
    "account_code": ACCOUNT_CODE,
    "nickname": "演示店铺（已脱敏）",
    "enabled": True,
    "running": True,
}
CONVERSATIONS = [
    {
        "conversation_id": "demo:consult:001",
        "peer_uid": "customer-demo-001",
        "display_name": "客户 A（脱敏）",
    },
    {
        "conversation_id": "demo:refund:002",
        "peer_uid": "customer-demo-002",
        "display_name": "客户 B（脱敏）",
    },
]


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def static_console_server() -> Iterator[str]:
    """Serve console.html locally so relative fetch calls have an origin."""
    if not CONSOLE_HTML.is_file():
        raise FileNotFoundError(f"Missing console UI: {CONSOLE_HTML}")

    handler = partial(QuietStaticHandler, directory=str(CONSOLE_HTML.parent))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/{CONSOLE_HTML.name}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def json_response(route, payload: dict) -> None:
    route.fulfill(
        status=200,
        content_type="application/json; charset=utf-8",
        body=json.dumps(payload, ensure_ascii=False),
    )


def route_sanitized_api(route) -> None:
    """Return only static, non-sensitive data to console.html."""
    path = urlparse(route.request.url).path
    if path == "/ping":
        json_response(route, {"ok": True, "data": {"service": "AgentDesk demo runtime"}})
        return
    if path == "/accounts":
        json_response(route, {"ok": True, "data": [ACCOUNT]})
        return
    if path == f"/accounts/{ACCOUNT_CODE}/conversations":
        json_response(route, {"ok": True, "data": {"conversations": CONVERSATIONS}})
        return
    if path.startswith(f"/accounts/{ACCOUNT_CODE}/login/"):
        json_response(
            route,
            {
                "ok": True,
                "data": {
                    "job_id": "login-job-demo-001",
                    "account_code": ACCOUNT_CODE,
                    "status": "queued",
                    "message": "脱敏演示任务，不包含真实浏览器凭据。",
                },
            },
        )
        return
    if path.startswith("/login/jobs/"):
        json_response(route, {"ok": True, "data": {"job_id": "login-job-demo-001", "status": "queued"}})
        return
    if path.startswith(f"/accounts/{ACCOUNT_CODE}/"):
        json_response(
            route,
            {
                "ok": True,
                "data": {
                    "account_code": ACCOUNT_CODE,
                    "status": "ok",
                    "receipt": "demo-receipt-001",
                    "mode": "sanitized-demo",
                },
            },
        )
        return
    route.continue_()


def add_demo_badge(page) -> None:
    page.evaluate(
        """
        () => {
          const form = document.getElementById('login-form');
          const panel = form?.closest('section.panel');
          if (!panel || panel.querySelector('.submission-demo-badge')) return;
          const badge = document.createElement('div');
          badge.className = 'submission-demo-badge';
          badge.textContent = '脱敏演示数据 · 本地 Mock API · 不含真实凭据';
          badge.style.cssText = [
            'margin:0 0 14px', 'padding:9px 12px', 'border-radius:8px',
            'background:#e7f6f1', 'border:1px solid #8fd3c4',
            'color:#136f63', 'font-size:13px', 'font-weight:600'
          ].join(';');
          panel.querySelector('.panel-body')?.prepend(badge);
        }
        """
    )


def capture_sanitized_console() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("截图生成需要 playwright；请先在 runtime/douyin 安装 requirements.txt。") from exc

    with static_console_server() as url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=1)
            page.route("**/*", route_sanitized_api)
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_selector("#account-list .account-item", timeout=15000)
            page.wait_for_selector("#conversation-list .conversation-item", timeout=15000)
            add_demo_badge(page)

            login_panel = page.locator("section.panel").filter(has=page.locator("#login-form")).first
            login_panel.screenshot(path=str(OUT / SCREENSHOTS[0]))

            conversation = page.locator("#conversation-list .conversation-item").first
            conversation.click()
            page.wait_for_timeout(250)
            page.screenshot(path=str(OUT / SCREENSHOTS[1]), full_page=True)
        finally:
            browser.close()


def run_trace(module: str, output_name: str, *extra_args: str) -> list[dict]:
    trace_path = OUT / output_name
    command = [sys.executable, "-m", module, "-o", str(trace_path), *extra_args]
    proc = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(f"Trace demo failed: {' '.join(command)}\n{proc.stderr}")
    try:
        return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    finally:
        trace_path.unlink(missing_ok=True)


def trace_row(event: dict) -> tuple[str, str, str]:
    agent = str(event.get("agent", "-"))
    action = str(event.get("event") or event.get("skill") or event.get("tool") or "trace")
    status = str(event.get("to") or event.get("status") or event.get("from") or "-")
    return agent, action, status


def trace_html(title: str, subtitle: str, events: list[dict], conclusion: str) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(agent)}</td>"
        f"<td>{html.escape(action)}</td>"
        f"<td><span class='status'>{html.escape(status)}</span></td>"
        "</tr>"
        for agent, action, status in (trace_row(event) for event in events)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f4f8f7; color: #143c3b; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; }}
  main {{ width: 1440px; min-height: 860px; padding: 54px 64px; }}
  .eyebrow {{ color: #168a7a; font-size: 17px; font-weight: 700; margin: 0 0 14px; }}
  h1 {{ font-size: 38px; line-height: 1.2; margin: 0; }}
  .subtitle {{ color: #5c6c72; font-size: 18px; margin: 14px 0 32px; }}
  .chips {{ display: flex; gap: 12px; margin-bottom: 24px; }}
  .chip {{ border: 1px solid #b9dcd5; background: #e7f6f1; border-radius: 18px; color: #136f63; padding: 8px 14px; font-size: 14px; }}
  .panel {{ background: #fff; border: 1px solid #d7e2df; border-radius: 10px; overflow: hidden; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 17px; }}
  th {{ background: #e9f2f7; color: #32749a; padding: 16px 20px; text-align: left; font-size: 15px; }}
  td {{ border-top: 1px solid #e4ece9; padding: 15px 20px; }}
  .status {{ color: #136f63; background: #e7f6f1; border-radius: 5px; padding: 4px 8px; }}
  .conclusion {{ margin-top: 26px; background: #143c3b; color: #fff; border-radius: 9px; padding: 20px 24px; font-size: 19px; line-height: 1.5; }}
  footer {{ color: #6b7b80; font-size: 14px; margin-top: 24px; }}
</style>
</head>
<body>
<main>
  <p class="eyebrow">AgentDesk · 参考编排器 Trace（Mock，离线可复现）</p>
  <h1>{html.escape(title)}</h1>
  <p class="subtitle">{html.escape(subtitle)}</p>
  <div class="chips"><span class="chip">TaskContext</span><span class="chip">Trace JSONL</span><span class="chip">不含真实账号数据</span></div>
  <section class="panel">
    <table><thead><tr><th>Agent</th><th>事件 / Skill</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table>
  </section>
  <div class="conclusion">{html.escape(conclusion)}</div>
  <footer>证据来源：仓内 orchestrator/demo 剧本运行输出；可用 python -m pytest -q 复现。</footer>
</main>
</body>
</html>"""


def render_html_screenshot(source: str, output: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("截图生成需要 playwright；请先在 runtime/douyin 安装 requirements.txt。") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page.set_content(source, wait_until="networkidle")
            page.screenshot(path=str(output), full_page=True)
        finally:
            browser.close()


def build_architecture() -> None:
    script = ROOT / "build_arch_diagram.py"
    if not script.is_file():
        raise FileNotFoundError(f"Missing architecture generator: {script}")
    subprocess.run([sys.executable, str(script)], cwd=str(REPO_ROOT), check=True)
    if not ARCH.is_file():
        raise FileNotFoundError(f"Missing generated architecture: {ARCH}")
    (OUT / SCREENSHOTS[2]).write_bytes(ARCH.read_bytes())


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for filename in LEGACY_SCREENSHOTS:
        (OUT / filename).unlink(missing_ok=True)

    capture_sanitized_console()
    build_architecture()

    approval_events = run_trace("orchestrator.demo.script_b_approval", "_approval_trace.jsonl")
    render_html_screenshot(
        trace_html(
            "高风险审批闭环",
            "退款 / 账户变更先进入 suspended，批准后才允许执行与核验。",
            approval_events,
            "成功证据：approval_required -> approval_granted -> business_action_executed -> business_action_verified -> customer_notification_sent。",
        ),
        OUT / SCREENSHOTS[3],
    )

    execution_events = run_trace(
        "orchestrator.demo.script_b_approval",
        "_rollback_trace.jsonl",
        "--inject-verify-failure",
    )
    render_html_screenshot(
        trace_html(
            "退款核验失败与补偿回滚",
            "企业动作核验失败时先回滚，不向客户发送“退款成功”通知。",
            execution_events,
            "回滚证据：business_action_verified=failed -> business_action_rollback_started -> business_action_rollback_verified；Trace 保留 rollback_of，未出现 customer_notification_sent。",
        ),
        OUT / SCREENSHOTS[4],
    )

    case_knowledge = OUT / "_case_knowledge_trace.jsonl"
    try:
        multichannel_events = run_trace(
            "orchestrator.demo.script_c_multichannel_case",
            "_multichannel_trace.jsonl",
            "--knowledge-output",
            str(case_knowledge),
        )
        showcase_events = [
            event
            for event in multichannel_events
            if event.get("event") == "duplicate_linked"
            or event.get("skill") in {"SessionNormalize", "CustomerConfirm", "CaseDigest"}
            or (
                event.get("skill") == "CaseKnowledgeRetrieve"
                and int((event.get("output") or {}).get("hit_count") or 0) > 0
            )
        ]
        render_html_screenshot(
            trace_html(
                "跨渠道去重、客户确认与案例复用",
                "企微仅使用统一 SessionEvent 离线契约，不声明真实渠道适配器已接入。",
                showcase_events,
                "剧本 C 证据：抖音首条任务完成并匿名归档；同内容企微任务被 deduplicated；后续咨询命中 case:// 引用后再次确认与沉淀。",
            ),
            OUT / SCREENSHOTS[5],
        )
    finally:
        case_knowledge.unlink(missing_ok=True)

    missing = [filename for filename in SCREENSHOTS if not (OUT / filename).is_file()]
    if missing:
        raise RuntimeError(f"Missing generated screenshots: {', '.join(missing)}")
    print(f"Written {len(SCREENSHOTS)} redacted screenshots to {OUT}")
    for filename in SCREENSHOTS:
        path = OUT / filename
        print(f"  {filename}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    build()
